"""Exchange rates: ECB euro reference rates and the cross rates derived from them.

The ECB quotes units of foreign currency per 1 EUR. A cross rate "dst per 1 src" is therefore
``per_eur(dst) / per_eur(src)`` on the same date.
"""

from __future__ import annotations

import calendar
import sqlite3
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from .money import div_rate, mul_rate

LOOKBACK_DAYS = 14


class RateError(Exception):
    """Raised when a rate that is needed does not exist."""


@dataclass(frozen=True)
class ClosingRate:
    rate: Decimal
    rate_date: date
    age_days: int


@dataclass(frozen=True)
class AverageRate:
    rate: Decimal
    days_used: int
    first: date
    last: date


def period_bounds(period: str) -> tuple[date, date]:
    """First and last calendar day of a YYYY-MM period."""
    year, month = int(period[:4]), int(period[5:7])
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, 1), date(year, month, last_day)


class FxTable:
    def __init__(self, per_eur: dict[str, dict[date, Decimal]]):
        self._per_eur = per_eur

    @classmethod
    def from_rows(cls, rows: Iterable[tuple[str, str, str]]) -> FxTable:
        table: dict[str, dict[date, Decimal]] = {}
        for rate_date, currency, value in rows:
            table.setdefault(currency, {})[date.fromisoformat(rate_date)] = Decimal(value)
        return cls(table)

    @classmethod
    def from_db(cls, conn: sqlite3.Connection) -> FxTable:
        cur = conn.execute("SELECT rate_date, currency, per_eur FROM fx_rate ORDER BY rate_date")
        return cls.from_rows((r["rate_date"], r["currency"], r["per_eur"]) for r in cur)

    def currencies(self) -> list[str]:
        return sorted(self._per_eur)

    def _per_eur_on(self, currency: str, day: date) -> Decimal | None:
        if currency == "EUR":
            return Decimal(1)
        return self._per_eur.get(currency, {}).get(day)

    def cross_on(self, src: str, dst: str, day: date) -> Decimal | None:
        """Units of ``dst`` for one unit of ``src`` on ``day``, or None if either rate is missing."""
        if src == dst:
            return Decimal(1)
        a = self._per_eur_on(src, day)
        b = self._per_eur_on(dst, day)
        if a is None or b is None:
            return None
        return div_rate(b, a)

    def closing(self, src: str, dst: str, as_of: date) -> ClosingRate:
        """Rate on ``as_of``, or on the last earlier day that has one (weekends, holidays)."""
        for back in range(LOOKBACK_DAYS + 1):
            day = as_of - timedelta(days=back)
            rate = self.cross_on(src, dst, day)
            if rate is not None and (src == dst or self._has_any(src, dst, day)):
                return ClosingRate(rate=rate, rate_date=day, age_days=back)
        raise RateError(f"no {src}/{dst} rate within {LOOKBACK_DAYS} days before {as_of}")

    def _has_any(self, src: str, dst: str, day: date) -> bool:
        return self._per_eur_on(src, day) is not None and self._per_eur_on(dst, day) is not None

    def average(self, src: str, dst: str, period: str) -> AverageRate:
        """Arithmetic mean of the daily cross rates in the month."""
        first, last = period_bounds(period)
        if src == dst:
            return AverageRate(Decimal(1), (last - first).days + 1, first, last)
        days = [
            first + timedelta(days=i)
            for i in range((last - first).days + 1)
            if self._has_any(src, dst, first + timedelta(days=i))
        ]
        if not days:
            raise RateError(f"no {src}/{dst} rates in {period}")
        total = sum(
            (self.cross_on(src, dst, d) for d in days),  # type: ignore[misc]
            start=Decimal(0),
        )
        return AverageRate(
            rate=div_rate(total, Decimal(len(days))),
            days_used=len(days),
            first=days[0],
            last=days[-1],
        )

    def convert(self, amount: Decimal, src: str, dst: str, day: date) -> Decimal:
        """Convert at the rate of ``day`` (or the last earlier day with a rate)."""
        return mul_rate(amount, self.closing(src, dst, day).rate)
