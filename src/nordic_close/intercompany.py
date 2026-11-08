"""Intercompany comparison and elimination.

Each intercompany line carries the counterparty and the amount in EUR, the currency the group
invoices in. The two sides of a pair are compared in EUR, so a difference is a genuine
disagreement and not an exchange-rate artefact. Elimination is then done in the presentation
currency; what is left after removing a genuine difference is the exchange-rate residual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .fx import FxTable, period_bounds
from .ledger import IcKey
from .money import mul_rate, q2
from .translate import TranslatedEntity, year_months

RECEIVABLE = "1150"
PAYABLE = "2050"
REVENUE = "4020"
COGS = "5020"
IC_CODES = (RECEIVABLE, PAYABLE, REVENUE, COGS)

SUSPENSE_BS = "1190"
SUSPENSE_PL = "7090"
FX_RESIDUAL = "7030"


@dataclass
class PairCheck:
    seller: str
    buyer: str
    receivable_eur: Decimal
    payable_eur: Decimal
    revenue_eur_ytd: Decimal
    cogs_eur_ytd: Decimal
    tolerance_eur: Decimal
    suggestion: dict | None = field(default=None)

    @property
    def bs_diff_eur(self) -> Decimal:
        return self.receivable_eur - self.payable_eur

    @property
    def pl_diff_eur(self) -> Decimal:
        return self.revenue_eur_ytd - self.cogs_eur_ytd

    @property
    def matches(self) -> bool:
        return (
            abs(self.bs_diff_eur) <= self.tolerance_eur
            and abs(self.pl_diff_eur) <= self.tolerance_eur
        )


@dataclass
class Elimination:
    seller: str
    buyer: str
    bs_lines: dict[str, Decimal]
    pl_lines: dict[str, Decimal]


def _get(flows: dict[IcKey, Decimal], entity: str, partner: str, code: str, period: str) -> Decimal:
    return flows.get((entity, partner, code, period), Decimal(0))


def _cumulative(
    flows: dict[IcKey, Decimal], entity: str, partner: str, code: str, up_to: str
) -> Decimal:
    return sum(
        (v for (e, p, c, per), v in flows.items() if (e, p, c) == (entity, partner, code) and per <= up_to),
        start=Decimal(0),
    )


def pair_keys(flows: dict[IcKey, Decimal]) -> list[tuple[str, str]]:
    """Ordered (seller, buyer) pairs found on either side of the books."""
    pairs: set[tuple[str, str]] = set()
    for entity, partner, code, _ in flows:
        if code in (RECEIVABLE, REVENUE):
            pairs.add((entity, partner))
        else:
            pairs.add((partner, entity))
    return sorted(pairs)


def check_pairs(
    eur: dict[IcKey, Decimal],
    period: str,
    tolerance_eur: Decimal,
    currencies: dict[str, str],
    fx: FxTable,
) -> list[PairCheck]:
    months = set(year_months(period))
    _, period_end = period_bounds(period)
    checks: list[PairCheck] = []
    for seller, buyer in pair_keys(eur):
        receivable = _cumulative(eur, seller, buyer, RECEIVABLE, period)
        payable = -_cumulative(eur, buyer, seller, PAYABLE, period)
        revenue = -sum(
            (_get(eur, seller, buyer, REVENUE, m) for m in months), start=Decimal(0)
        )
        cogs = sum((_get(eur, buyer, seller, COGS, m) for m in months), start=Decimal(0))
        check = PairCheck(seller, buyer, receivable, payable, revenue, cogs, tolerance_eur)
        if not check.matches:
            check.suggestion = _suggest(check, currencies, fx, period_end)
        checks.append(check)
    return checks


def _suggest(check: PairCheck, currencies: dict[str, str], fx: FxTable, day: date) -> dict | None:
    """Propose the journal that would bring the books into agreement.

    Only offered when the balance difference and the P&L difference are the same amount, which
    is what a missing or late-booked invoice looks like.
    """
    if abs(check.bs_diff_eur - check.pl_diff_eur) > check.tolerance_eur:
        return None
    diff = check.bs_diff_eur
    if diff > 0:
        entity, debit, credit = check.buyer, COGS, PAYABLE
        eur_amount = diff
    else:
        entity, debit, credit = check.seller, RECEIVABLE, REVENUE
        eur_amount = -diff
    currency = currencies[entity]
    local = q2(mul_rate(eur_amount, fx.closing("EUR", currency, day).rate))
    return {
        "entity": entity,
        "date": day.isoformat(),
        "debit": debit,
        "credit": credit,
        "amount_eur": str(q2(eur_amount)),
        "amount_local": str(local),
        "currency": currency,
        "counterparty": check.seller if entity == check.buyer else check.buyer,
    }


def build_eliminations(
    eur: dict[IcKey, Decimal],
    local: dict[IcKey, Decimal],
    entities: dict[str, TranslatedEntity],
    fx: FxTable,
    presentation: str,
    period: str,
    tolerance_eur: Decimal,
) -> list[Elimination]:
    months = year_months(period)
    _, period_end = period_bounds(period)
    eur_closing = fx.closing("EUR", presentation, period_end).rate
    eur_average = {m: fx.average("EUR", presentation, m).rate for m in months}
    result: list[Elimination] = []

    for seller, buyer in pair_keys(eur):
        te_s, te_b = entities[seller], entities[buyer]

        rec_local = _cumulative(local, seller, buyer, RECEIVABLE, period)
        pay_local = -_cumulative(local, buyer, seller, PAYABLE, period)
        rec_group = mul_rate(rec_local, te_s.rates.closing)
        pay_group = mul_rate(pay_local, te_b.rates.closing)
        bs_eur_diff = _cumulative(eur, seller, buyer, RECEIVABLE, period) + _cumulative(
            eur, buyer, seller, PAYABLE, period
        )
        bs_real = Decimal(0) if abs(bs_eur_diff) <= tolerance_eur else mul_rate(bs_eur_diff, eur_closing)
        bs_total = rec_group - pay_group
        bs_lines = {
            PAYABLE: pay_group,
            RECEIVABLE: -rec_group,
            SUSPENSE_BS: bs_real,
            FX_RESIDUAL: bs_total - bs_real,
        }

        rev_group = Decimal(0)
        cogs_group = Decimal(0)
        real_group = Decimal(0)
        eur_diff_ytd = Decimal(0)
        for m in months:
            rev_local = -_get(local, seller, buyer, REVENUE, m)
            cogs_local = _get(local, buyer, seller, COGS, m)
            rev_group += mul_rate(rev_local, te_s.monthly_average[m])
            cogs_group += mul_rate(cogs_local, te_b.monthly_average[m])
            month_diff = -_get(eur, seller, buyer, REVENUE, m) - _get(eur, buyer, seller, COGS, m)
            eur_diff_ytd += month_diff
            real_group += mul_rate(month_diff, eur_average[m])
        pl_real = Decimal(0) if abs(eur_diff_ytd) <= tolerance_eur else -real_group
        pl_total = cogs_group - rev_group
        pl_lines = {
            REVENUE: rev_group,
            COGS: -cogs_group,
            SUSPENSE_PL: pl_real,
            FX_RESIDUAL: pl_total - pl_real,
        }
        result.append(Elimination(seller, buyer, bs_lines, pl_lines))
    return result
