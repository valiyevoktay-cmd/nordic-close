from datetime import date
from decimal import Decimal

from nordic_close.consolidate import translation_proof
from nordic_close.fx import FxTable
from nordic_close.ledger import Account, Entity, trial_balance
from nordic_close.translate import translate_entity, year_months

D = Decimal

ACCOUNTS = {
    "1010": Account("1010", "Cash", "ASSET", "BS", "cash", 1, False),
    "3010": Account("3010", "Capital", "EQUITY", "BS", "share_capital", 2, False),
    "4010": Account("4010", "Revenue", "INCOME", "PL", "revenue", 3, False),
    "6020": Account("6020", "Opex", "EXPENSE", "PL", "other_opex", 4, False),
}
ENTITY = Entity("SE", "Entity SE", "SEK", "SE", False)


def hand_case():
    """SEK entity reporting in EUR.

    SEK per EUR: 10 on 31 Dec (rate 0.1), 8 on every day of January except 5 on the 30th.
    Opening: cash 1000, share capital 1000. January: revenue 400, costs 100, all in cash.
    """
    rows = [("2025-12-31", "SEK", "10")]
    for d in range(1, 31):
        day = date(2026, 1, d)
        if day.weekday() < 5:
            rows.append((day.isoformat(), "SEK", "5" if d == 30 else "8"))
    fx = FxTable.from_rows(rows)
    moves = {
        ("2026-01", "1010"): D(1300),
        ("2026-01", "3010"): D(-1000),
        ("2026-01", "4010"): D(-400),
        ("2026-01", "6020"): D(100),
    }
    return fx, moves


def test_hand_computed_translation_and_reserve():  # AC-7
    fx, moves = hand_case()
    te = translate_entity(ENTITY, moves, ACCOUNTS, fx, "EUR", "2026-01")

    closing, opening = D(1) / 5, D(1) / 10          # 30 Jan is the last rate of the month
    average = fx.average("SEK", "EUR", "2026-01").rate
    assert te.rates.closing == closing and te.rates.opening == opening
    assert te.bs["1010"] == D(1300) * closing               # assets at closing rate
    assert te.bs["3010"] == D(-1000) * opening              # equity at the opening rate
    assert abs(te.pl_ytd["4010"] - D(-400) * average) < D("1e-20")  # income at average rate
    profit = (D(400) - D(100)) * average
    expected_reserve_credit = D(1300) * closing - D(1000) * opening - profit
    assert abs(-te.cta - expected_reserve_credit) < D("1e-20")


def test_translated_trial_balance_sums_to_zero():  # AC-8
    fx, moves = hand_case()
    te = translate_entity(ENTITY, moves, ACCOUNTS, fx, "EUR", "2026-01")
    assert abs(te.tb_sum()) < D("0.01")
    assert abs(translation_proof(te, ACCOUNTS, moves)) < D("1e-20")


def test_same_currency_has_no_reserve():  # EC-2
    fx = FxTable.from_rows([("2025-12-31", "SEK", "10"), ("2026-01-30", "SEK", "10")])
    moves = {("2026-01", "1010"): D(500), ("2026-01", "3010"): D(-500)}
    te = translate_entity(ENTITY, moves, ACCOUNTS, fx, "SEK", "2026-01")
    assert te.cta == 0
    assert te.bs["1010"] == 500


def test_trial_balance_helper_splits_month_and_cumulative():
    moves = {("2026-01", "1010"): D(10), ("2026-02", "1010"): D(5),
             ("2026-01", "4010"): D(-3), ("2026-02", "4010"): D(-4)}
    tb = trial_balance(moves, ACCOUNTS, "2026-02")
    assert tb == {"1010": D(15), "4010": D(-4)}
    assert year_months("2026-03") == ["2026-01", "2026-02", "2026-03"]
