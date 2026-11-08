"""IAS 21 translation of an entity's ledger into the presentation currency.

* Assets and liabilities: closing rate at the end of the period.
* Income and expenses: the average rate of the month in which they were booked.
* Equity (share capital and brought-forward earnings): the rate on the opening date, which is
  the closing rate of 31 December of the previous year.
* The difference is the translation reserve. It is computed so the translated trial balance
  sums to zero.

Balances are debit-positive. A credit balance is negative.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from .fx import FxTable, period_bounds
from .ledger import Account, Entity
from .money import mul_rate

CTA_CODE = "3090"


@dataclass(frozen=True)
class EntityRates:
    currency: str
    closing: Decimal
    closing_date: date
    closing_age_days: int
    average: Decimal
    average_days: int
    opening: Decimal
    opening_date: date


@dataclass
class TranslatedEntity:
    entity: Entity
    period: str
    rates: EntityRates
    local_bs: dict[str, Decimal]
    local_pl_month: dict[str, Decimal]
    local_pl_ytd: dict[str, Decimal]
    bs: dict[str, Decimal]
    pl_month: dict[str, Decimal]
    pl_ytd: dict[str, Decimal]
    cta: Decimal
    monthly_average: dict[str, Decimal] = field(default_factory=dict)

    def tb_sum(self) -> Decimal:
        """Sum of the translated trial balance (balance sheet incl. reserve + YTD P&L)."""
        return sum(self.bs.values(), Decimal(0)) + sum(self.pl_ytd.values(), Decimal(0))


def year_months(period: str) -> list[str]:
    year, month = int(period[:4]), int(period[5:7])
    return [f"{year}-{m:02d}" for m in range(1, month + 1)]


def translate_entity(
    entity: Entity,
    moves: dict[tuple[str, str], Decimal],
    accounts: dict[str, Account],
    fx: FxTable,
    presentation: str,
    period: str,
) -> TranslatedEntity:
    _, period_end = period_bounds(period)
    opening_date = date(period_end.year - 1, 12, 31)
    closing = fx.closing(entity.currency, presentation, period_end)
    opening = fx.closing(entity.currency, presentation, opening_date)
    average = fx.average(entity.currency, presentation, period)
    months = year_months(period)
    month_avg = {m: fx.average(entity.currency, presentation, m).rate for m in months}

    local_bs: dict[str, Decimal] = {}
    local_pl_month: dict[str, Decimal] = {}
    local_pl_ytd: dict[str, Decimal] = {}
    pl_ytd: dict[str, Decimal] = {}
    for (p, code), amount in moves.items():
        account = accounts[code]
        if account.is_pl:
            if p in month_avg:
                local_pl_ytd[code] = local_pl_ytd.get(code, Decimal(0)) + amount
                pl_ytd[code] = pl_ytd.get(code, Decimal(0)) + mul_rate(amount, month_avg[p])
            if p == period:
                local_pl_month[code] = local_pl_month.get(code, Decimal(0)) + amount
        elif p <= period:
            local_bs[code] = local_bs.get(code, Decimal(0)) + amount

    bs: dict[str, Decimal] = {}
    for code, amount in local_bs.items():
        rate = opening.rate if accounts[code].kind == "EQUITY" else closing.rate
        bs[code] = mul_rate(amount, rate)
    pl_month = {code: mul_rate(amount, average.rate) for code, amount in local_pl_month.items()}

    difference = sum(bs.values(), Decimal(0)) + sum(pl_ytd.values(), Decimal(0))
    cta = -difference
    bs[CTA_CODE] = cta

    return TranslatedEntity(
        entity=entity,
        period=period,
        rates=EntityRates(
            currency=entity.currency,
            closing=closing.rate,
            closing_date=closing.rate_date,
            closing_age_days=closing.age_days,
            average=average.rate,
            average_days=average.days_used,
            opening=opening.rate,
            opening_date=opening_date,
        ),
        local_bs=local_bs,
        local_pl_month=local_pl_month,
        local_pl_ytd=local_pl_ytd,
        bs=bs,
        pl_month=pl_month,
        pl_ytd=pl_ytd,
        cta=cta,
        monthly_average=month_avg,
    )
