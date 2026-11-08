from datetime import date
from decimal import Decimal

from nordic_close.controls import bank_rec
from tests.conftest import line, load, write_dataset

D = Decimal


def dataset(tmp_path, ledger_cash, bank, *, bank_opening="1000.00", closing=None):
    """SE with opening cash 1,000. ledger_cash: list of (date, ref, signed amount)."""
    journals = [("SE0", "SE", "2026-01-01", [line("1010", 1000, "OPENING"), line("3010", -1000)])]
    for n, (day, ref, amount) in enumerate(ledger_cash, start=1):
        journals.append((f"SE{n}", "SE", day, [line("1010", amount, ref), line("4010", -amount)]))
    bank_total = sum(D(a) for _, _, a in bank)
    closing = closing or f"{D(bank_opening) + bank_total:.2f}"
    folder = write_dataset(
        tmp_path / "d", journals,
        bank=[("SE", d, r, a) for d, r, a in bank],
        balances=[("SE", "2026-01", bank_opening, closing)],
    )
    return load(folder)


def test_everything_matches(tmp_path):
    conn, _ = dataset(tmp_path, [("2026-01-05", "R1", 200), ("2026-01-06", "R2", -50)],
                      [("2026-01-05", "R1", "200.00"), ("2026-01-06", "R2", "-50.00")])
    r = bank_rec.reconcile(conn, "SE", "2026-01")
    assert r.status == "reconciled" and r.proof_difference == 0 and r.matched == 2 and r.passes


def test_amount_and_date_window_match_without_reference(tmp_path):
    conn, _ = dataset(tmp_path, [("2026-01-05", "R1", 200)], [("2026-01-07", "", "200.00")])
    r = bank_rec.reconcile(conn, "SE", "2026-01")
    assert r.status == "reconciled" and r.matched == 1


def test_outside_window_is_not_matched(tmp_path):
    conn, _ = dataset(tmp_path, [("2026-01-05", "R1", 200)], [("2026-01-12", "", "200.00")])
    r = bank_rec.reconcile(conn, "SE", "2026-01")
    assert len(r.outstanding) == 1 and len(r.unrecorded) == 1


def test_transposition_is_detected(tmp_path):  # AC-11
    conn, _ = dataset(tmp_path, [("2026-01-10", "PAY1", -47520)], [("2026-01-10", "PAY1", "-47250.00")])
    r = bank_rec.reconcile(conn, "SE", "2026-01")
    assert len(r.transpositions) == 1
    assert r.transpositions[0].difference == D("270.00")
    assert r.status == "open_items" and r.proof_difference == 0
    assert r.journals[0]["amount"] == "270.00"


def test_bank_fee_not_in_ledger_needs_a_journal(tmp_path):  # AC-12
    conn, _ = dataset(tmp_path, [("2026-01-05", "R1", 200)],
                      [("2026-01-05", "R1", "200.00"), ("2026-01-31", "FEE", "-18.50")])
    r = bank_rec.reconcile(conn, "SE", "2026-01")
    assert r.status == "open_items" and not r.passes
    assert [i.amount for i in r.unrecorded] == [D("-18.50")]
    assert r.journals[0]["debit"] == "6020" and r.journals[0]["credit"] == "1010"


def test_ledger_only_item_is_timing(tmp_path):
    conn, _ = dataset(tmp_path, [("2026-01-30", "CHQ", -75)], [])
    r = bank_rec.reconcile(conn, "SE", "2026-01")
    assert r.status == "timing_only" and r.passes
    assert r.outstanding[0].age_days == 1


def test_stale_item_blocks(tmp_path):
    conn, _ = dataset(tmp_path, [("2026-01-02", "CHQ", -75)], [])
    r = bank_rec.reconcile(conn, "SE", "2026-01", stale_days=10)
    assert r.status == "stale_items" and not r.passes


def test_opening_difference_breaks_the_proof(tmp_path):  # EC-5
    conn, _ = dataset(tmp_path, [("2026-01-05", "R1", 200)], [("2026-01-05", "R1", "200.00")],
                      bank_opening="1100.00")
    r = bank_rec.reconcile(conn, "SE", "2026-01")
    assert r.status == "not_proven" and r.proof_difference == D("100.00")


def test_each_bank_line_is_matched_once(tmp_path):  # EC-6
    conn, _ = dataset(tmp_path, [("2026-01-05", "R1", 200)],
                      [("2026-01-05", "R1", "200.00"), ("2026-01-05", "R1", "200.00")])
    r = bank_rec.reconcile(conn, "SE", "2026-01")
    assert r.matched == 1 and len(r.unrecorded) == 1


def test_same_preparer_and_reviewer_fails_segregation(tmp_path):
    conn, _ = dataset(tmp_path, [], [])
    conn.execute("UPDATE bank_statement_balance SET reviewer = preparer")
    r = bank_rec.reconcile(conn, "SE", "2026-01")
    assert r.status == "reconciled" and not r.segregation_ok and not r.passes


def test_period_without_statement(tmp_path):
    conn, _ = dataset(tmp_path, [], [])
    r = bank_rec.reconcile(conn, "SE", "2026-02")
    assert r.status == "no_statement" and not r.passes


def test_item_cleared_next_month_matches_across_periods(tmp_path):
    journals = [("SE0", "SE", "2026-01-01", [line("1010", 1000, "OPENING"), line("3010", -1000)]),
                ("SE1", "SE", "2026-01-30", [line("1010", -75, "CHQ"), line("4010", 75)])]
    folder = write_dataset(
        tmp_path / "d", journals, bank=[("SE", "2026-02-03", "CHQ", "-75.00")],
        balances=[("SE", "2026-01", "1000.00", "1000.00"), ("SE", "2026-02", "1000.00", "925.00")])
    conn, _ = load(folder)
    assert bank_rec.reconcile(conn, "SE", "2026-01").status == "timing_only"
    assert bank_rec.reconcile(conn, "SE", "2026-02").status == "reconciled"


def test_match_function_directly():
    a = bank_rec.Item("ledger", date(2026, 1, 1), "X", D("10"), "")
    b = bank_rec.Item("bank", date(2026, 1, 1), "X", D("10"), "")
    ledger_left, bank_left, trans, matched = bank_rec.match([a], [b])
    assert (ledger_left, bank_left, trans, matched) == ([], [], [], 1)
