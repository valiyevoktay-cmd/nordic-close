from decimal import Decimal

from nordic_close import reports
from tests.conftest import line, load, write_dataset

D = Decimal


def pair_journals(buyer_booked=True):
    """SE sells EUR 1,000 to FI. SEK per EUR is a constant 10, so SE books SEK 10,000."""
    journals = [
        ("SE1", "SE", "2026-01-10", [
            line("1150", 10000, "I1", "FI", "1000.00"), line("4020", -10000, "I1", "FI", "1000.00")]),
    ]
    if buyer_booked:
        journals.append(("FI1", "FI", "2026-01-10", [
            line("5020", 1000, "I1", "SE", "1000.00"), line("2050", -1000, "I1", "SE", "1000.00")]))
    return journals


def test_matching_pair_is_eliminated_to_zero(tmp_path):  # AC-10
    _, c = load(write_dataset(tmp_path / "d", pair_journals()))
    state = c.state("2026-01")
    assert all(p.matches for p in state.pairs)
    for code in ("1150", "2050", "4020", "5020", "1190", "7090", "7030"):
        assert state.bs.get(code, 0) == 0 and state.pl_ytd.get(code, 0) == 0
    assert state.tb_sum() == 0


def test_late_booked_invoice_shows_exact_difference_and_journal(tmp_path):  # AC-9
    _, c = load(write_dataset(tmp_path / "d", pair_journals(buyer_booked=False)))
    state = c.state("2026-01")
    pair = state.pairs[0]
    assert not pair.matches
    assert pair.bs_diff_eur == D("1000.00") and pair.pl_diff_eur == D("1000.00")
    journal = pair.suggestion
    assert journal["entity"] == "FI"
    assert (journal["debit"], journal["credit"]) == ("5020", "2050")
    assert journal["amount_eur"] == "1000.00" and journal["amount_local"] == "1000.00"
    # The difference sits on visible suspense lines instead of disappearing.
    assert state.bs["1190"] == D("10000.00")
    assert state.pl_ytd["7090"] == D("-10000.00")
    assert state.tb_sum() == 0


def test_difference_within_tolerance_is_treated_as_rounding(tmp_path):
    journals = pair_journals()
    journals[1] = ("FI1", "FI", "2026-01-10", [
        line("5020", 1000, "I1", "SE", "999.50"), line("2050", -1000, "I1", "SE", "999.50")])
    _, c = load(write_dataset(tmp_path / "d", journals))
    assert c.state("2026-01").pairs[0].matches  # default tolerance is EUR 1.00


def test_buyer_side_excess_suggests_seller_journal(tmp_path):
    journals = pair_journals()
    journals[0] = ("SE1", "SE", "2026-01-10", [
        line("1150", 5000, "I1", "FI", "500.00"), line("4020", -5000, "I1", "FI", "500.00")])
    _, c = load(write_dataset(tmp_path / "d", journals))
    pair = c.state("2026-01").pairs[0]
    assert pair.bs_diff_eur == D("-500.00")
    assert pair.suggestion["entity"] == "SE"
    assert (pair.suggestion["debit"], pair.suggestion["credit"]) == ("1150", "4020")


def test_unequal_bs_and_pl_differences_give_no_suggestion(tmp_path):
    journals = pair_journals()
    journals.append(("FI2", "FI", "2026-01-20", [
        line("2050", 400, "S1", "SE", "400.00"), line("1010", -400, "S1")]))  # settles part only
    _, c = load(write_dataset(tmp_path / "d", journals))
    pair = c.state("2026-01").pairs[0]
    assert not pair.matches and pair.suggestion is None


def test_statements_foot_after_elimination(tmp_path):
    journals = [
        ("SE0", "SE", "2026-01-01", [line("1010", 20000), line("3010", -20000)]),
        ("FI0", "FI", "2026-01-01", [line("1010", 2000), line("3010", -2000)]),
        *pair_journals(),
        ("FI3", "FI", "2026-01-15", [line("1010", 300), line("4010", -300)]),
    ]
    _, c = load(write_dataset(tmp_path / "d", journals))
    bs = reports.balance_sheet(c, "2026-01")
    assert bs["check"] == 0
    revenue = next(r for r in reports.income_statement(c, "2026-01") if r.key == "revenue")
    assert revenue.values["month"] == D("3000.00")  # FI external revenue 300 EUR x 10
