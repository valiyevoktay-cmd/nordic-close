from datetime import date
from decimal import Decimal

import pytest

from nordic_close.fx import FxTable, RateError, period_bounds
from nordic_close.money import fmt, from_minor, q2, to_minor


def test_minor_units_round_trip():
    assert to_minor("12.345") == 1235
    assert to_minor(Decimal("0.1") + Decimal("0.2")) == 30
    assert from_minor(1235) == Decimal("12.35")
    assert q2(Decimal("2.675")) == Decimal("2.68")
    assert fmt(Decimal("1234567.891")) == "1,234,567.89"


def make_fx():
    rows = [
        ("2026-01-29", "SEK", "10"), ("2026-01-30", "SEK", "11"),   # Thursday, Friday
        ("2026-01-29", "NOK", "10"), ("2026-01-30", "NOK", "10"),
    ]
    return FxTable.from_rows(rows)


def test_average_is_mean_of_daily_rates():  # AC-4
    fx = FxTable.from_rows([
        ("2026-01-05", "SEK", "10"), ("2026-01-06", "SEK", "8"), ("2026-01-07", "SEK", "5"),
    ])
    average = fx.average("EUR", "SEK", "2026-01")
    expected = Decimal(23) / 3
    assert abs(average.rate - expected) < Decimal("1e-20")
    assert average.days_used == 3


def test_cross_average_uses_daily_crosses():
    fx = FxTable.from_rows([
        ("2026-01-05", "SEK", "10"), ("2026-01-05", "NOK", "10"),
        ("2026-01-06", "SEK", "12"), ("2026-01-06", "NOK", "8"),
    ])
    average = fx.average("NOK", "SEK", "2026-01")
    assert average.rate == (Decimal(1) + Decimal("1.5")) / 2


def test_closing_uses_previous_business_day_on_weekend():  # AC-5
    fx = make_fx()
    closing = fx.closing("EUR", "SEK", date(2026, 1, 31))  # Saturday
    assert closing.rate_date == date(2026, 1, 30)
    assert closing.age_days == 1
    assert closing.rate == Decimal(11)


def test_missing_rates_raise():  # AC-6
    fx = make_fx()
    with pytest.raises(RateError, match="EUR/DKK"):
        fx.closing("EUR", "DKK", date(2026, 1, 31))
    with pytest.raises(RateError):
        fx.average("EUR", "SEK", "2026-03")


def test_same_currency_is_one():  # EC-2
    fx = make_fx()
    assert fx.closing("SEK", "SEK", date(2026, 1, 31)).rate == 1
    assert fx.average("SEK", "SEK", "2026-01").rate == 1


def test_gap_in_month_uses_available_days():  # EC-3
    fx = FxTable.from_rows([("2026-01-05", "SEK", "10"), ("2026-01-20", "SEK", "20")])
    assert fx.average("EUR", "SEK", "2026-01").days_used == 2


def test_period_bounds_and_convert():
    assert period_bounds("2026-02") == (date(2026, 2, 1), date(2026, 2, 28))
    fx = make_fx()
    assert fx.convert(Decimal(100), "EUR", "SEK", date(2026, 1, 30)) == Decimal(1100)
    assert fx.currencies() == ["NOK", "SEK"]
