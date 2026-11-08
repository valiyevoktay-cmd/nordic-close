"""Consolidation: translate every entity, eliminate intercompany items, round, and foot."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from . import intercompany as ic
from .fx import FxTable
from .ledger import (
    Account,
    Entity,
    ic_flows,
    load_accounts,
    load_entities,
    movements,
    periods_with_data,
    previous_period,
)
from .money import from_minor, mul_rate, q2
from .translate import CTA_CODE, TranslatedEntity, translate_entity


@dataclass
class LedgerData:
    entities: list[Entity]
    accounts: dict[str, Account]
    moves: dict[str, dict[tuple[str, str], Decimal]]
    ic_eur: dict
    ic_local: dict
    budget: dict[tuple[str, str, str], Decimal]
    periods: list[str]

    @classmethod
    def load(cls, conn: sqlite3.Connection) -> LedgerData:
        entities = load_entities(conn)
        eur, local = ic_flows(conn, ic.IC_CODES)
        budget = {
            (r["entity_code"], r["period"], r["group_code"]): from_minor(r["amount_minor"])
            for r in conn.execute("SELECT * FROM budget_line")
        }
        return cls(
            entities=entities,
            accounts=load_accounts(conn),
            moves={e.code: movements(conn, e.code) for e in entities},
            ic_eur=eur,
            ic_local=local,
            budget=budget,
            periods=periods_with_data(conn),
        )


@dataclass
class GroupState:
    """The consolidated position at the end of one period."""

    period: str
    presentation: str
    entities: dict[str, TranslatedEntity]
    pairs: list[ic.PairCheck]
    eliminations: list[ic.Elimination]
    bs: dict[str, Decimal]
    pl_ytd: dict[str, Decimal]
    raw_tb_sum: Decimal

    def tb_sum(self) -> Decimal:
        return sum(self.bs.values(), Decimal(0)) + sum(self.pl_ytd.values(), Decimal(0))


class Consolidator:
    def __init__(
        self,
        data: LedgerData,
        fx: FxTable,
        presentation: str,
        ic_tolerance_eur: Decimal = Decimal("1.00"),
    ):
        self.data = data
        self.fx = fx
        self.presentation = presentation
        self.ic_tolerance_eur = ic_tolerance_eur
        self._cache: dict[str, GroupState] = {}

    def state(self, period: str) -> GroupState:
        if period not in self._cache:
            self._cache[period] = self._build(period)
        return self._cache[period]

    def _build(self, period: str) -> GroupState:
        translated = {
            e.code: translate_entity(
                e, self.data.moves[e.code], self.data.accounts, self.fx, self.presentation, period
            )
            for e in self.data.entities
        }
        currencies = {e.code: e.currency for e in self.data.entities}
        pairs = ic.check_pairs(
            self.data.ic_eur, period, self.ic_tolerance_eur, currencies, self.fx
        )
        eliminations = ic.build_eliminations(
            self.data.ic_eur,
            self.data.ic_local,
            translated,
            self.fx,
            self.presentation,
            period,
            self.ic_tolerance_eur,
        )

        bs: dict[str, Decimal] = {}
        pl: dict[str, Decimal] = {}

        def add(code: str, amount: Decimal) -> None:
            target = pl if self.data.accounts[code].is_pl else bs
            target[code] = target.get(code, Decimal(0)) + amount

        for te in translated.values():
            for code, amount in te.bs.items():
                add(code, amount)
            for code, amount in te.pl_ytd.items():
                add(code, amount)
        for elimination in eliminations:
            for code, amount in elimination.bs_lines.items():
                add(code, amount)
            for code, amount in elimination.pl_lines.items():
                add(code, amount)

        raw_sum = sum(bs.values(), Decimal(0)) + sum(pl.values(), Decimal(0))
        bs_r = {c: q2(v) for c, v in bs.items() if c != CTA_CODE}
        pl_r = {c: q2(v) for c, v in pl.items()}
        plug = -(sum(bs_r.values(), Decimal(0)) + sum(pl_r.values(), Decimal(0)))
        bs_r[CTA_CODE] = q2(plug)
        # Keep only accounts with a balance, so empty lines do not clutter the reports.
        bs_r = {c: v for c, v in bs_r.items() if v != 0}
        pl_r = {c: v for c, v in pl_r.items() if v != 0}

        return GroupState(
            period=period,
            presentation=self.presentation,
            entities=translated,
            pairs=pairs,
            eliminations=eliminations,
            bs=bs_r,
            pl_ytd=pl_r,
            raw_tb_sum=raw_sum,
        )

    def pl_month(self, period: str) -> dict[str, Decimal]:
        """Month P&L is the change in year-to-date P&L, so month and YTD always agree."""
        current = self.state(period).pl_ytd
        if period[5:7] == "01":
            return dict(current)
        prior = self.state(previous_period(period)).pl_ytd
        out: dict[str, Decimal] = {}
        for code in set(current) | set(prior):
            value = current.get(code, Decimal(0)) - prior.get(code, Decimal(0))
            if value != 0:
                out[code] = value
        return out


def translation_proof(
    te: TranslatedEntity,
    accounts: dict[str, Account],
    moves: dict[tuple[str, str], Decimal],
) -> Decimal:
    """Recompute the translation reserve from net assets and profit; return the difference.

    reserve = net assets at the closing rate - equity at the opening rate
              - the sum of each month's net profit at that month's average rate.

    ``translate_entity`` derives the reserve as the figure that makes the translated trial
    balance sum to zero. This route nets the P&L month by month first and works from the raw
    ledger movements, so it fails if the wrong rate was applied to any group of accounts.
    """
    net_assets = sum(
        (v for c, v in te.local_bs.items() if accounts[c].kind != "EQUITY"), start=Decimal(0)
    )
    equity_credit = -sum(
        (v for c, v in te.local_bs.items() if accounts[c].kind == "EQUITY"), start=Decimal(0)
    )
    profit_translated = Decimal(0)
    for month, rate in te.monthly_average.items():
        month_profit = -sum(
            (v for (p, c), v in moves.items() if p == month and accounts[c].is_pl),
            start=Decimal(0),
        )
        profit_translated += mul_rate(month_profit, rate)
    expected_reserve_credit = (
        mul_rate(net_assets, te.rates.closing)
        - mul_rate(equity_credit, te.rates.opening)
        - profit_translated
    )
    return (-te.cta) - expected_reserve_credit
