from decimal import Decimal

from nordic_close import reports
from nordic_close.controls import gate
from nordic_close.service import CloseService

D = Decimal


def test_sample_trial_balances_sum_to_zero(sample_conn):  # AC-3
    rows = sample_conn.execute(
        "SELECT j.entity_code AS e, j.period AS p, SUM(l.debit_minor) - SUM(l.credit_minor) AS d "
        "FROM journal_line l JOIN journal j ON j.journal_id = l.journal_id GROUP BY e, p"
    ).fetchall()
    assert rows and all(r["d"] == 0 for r in rows)


def test_every_period_translates_and_foots(sample_consolidator):  # AC-8 on real rates
    for period in sample_consolidator.data.periods:
        state = sample_consolidator.state(period)
        assert abs(state.raw_tb_sum) < D("0.01")
        assert state.tb_sum() == 0
        assert reports.balance_sheet(sample_consolidator, period)["check"] == 0


def test_month_pl_adds_up_to_ytd(sample_consolidator):
    c = sample_consolidator
    for code in ("4010", "5010", "6010"):
        total = sum((c.pl_month(p).get(code, D(0)) for p in c.data.periods), D(0))
        assert total == c.state(c.data.periods[-1]).pl_ytd[code]


def test_income_statement_subtotals(sample_consolidator):
    rows = {r.key: r.values["month"] for r in reports.income_statement(sample_consolidator, "2026-10")}
    assert rows["gross_profit"] == rows["revenue"] + rows["cogs"]
    assert rows["net_income"] == rows["pbt"] + rows["income_tax"]
    assert rows["ebitda"] == rows["gross_profit"] + rows["personnel"] + rows["other_opex"]


def test_variance_effects_add_up(sample_consolidator):  # AC-17
    for view in ("month", "ytd"):
        v = reports.variance(sample_consolidator, "2026-10", view)
        for row in v["rows"]:
            assert row["operating_effect"] + row["fx_effect"] == row["variance"]
            assert row["variance"] == row["actual"] - row["budget"]
        assert v["total"]["variance"] == v["total"]["operating_effect"] + v["total"]["fx_effect"]


def test_kpis_and_entity_view(sample_consolidator):
    kpis = {k["key"]: k for k in reports.kpis(sample_consolidator, "2026-10")}
    assert D("0.3") < kpis["gross_margin"]["value"] < D("0.7")
    assert kpis["dso"]["unit"] == "days"
    entities = reports.entity_view(sample_consolidator, "2026-10")
    assert [e["entity"] for e in entities] == ["SE", "DK", "FI", "NO"]
    se = entities[0]
    assert se["translation_reserve"] == 0 and se["closing_rate"] == 1


def test_first_period_has_no_prior_columns(sample_consolidator):
    first = sample_consolidator.data.periods[0]
    row = reports.income_statement(sample_consolidator, first)[0]
    assert row.values["prior_month"] is None
    assert reports.flux_lines(sample_consolidator, first) == []


def test_september_is_ready_and_october_is_blocked(
    sample_conn, sample_consolidator, sample_settings,
):  # AC-18
    sept = gate.assess(sample_conn, sample_settings, sample_consolidator, "2026-09")
    assert sept.status == "ready"
    octo = gate.assess(sample_conn, sample_settings, sample_consolidator, "2026-10")
    failed = {g.name for g in octo.gates if g.status == "fail"}
    assert octo.status == "blocked" and failed == {"intercompany", "bank_reconciliation"}
    ic = next(g for g in octo.gates if g.name == "intercompany")
    assert "SE -> DK differs by EUR 120,000.00" in ic.reason
    assert {r.entity: r.status for r in octo.bank} == {
        "SE": "reconciled", "NO": "timing_only", "DK": "open_items", "FI": "open_items"}


def test_run_is_deterministic_and_stored(sample_db):  # AC-19
    service = CloseService(sample_db)
    conn = service.connect()
    first = service.run_close(conn, "2026-09", actor="test")
    second = service.run_close(conn, "2026-09", actor="test")
    assert first["input_hash"] == second["input_hash"]
    assert first["output_hash"] == second["output_hash"]
    assert first["run_id"] != second["run_id"] and first["status"] == "ready"
    runs = service.runs(conn, "2026-09")
    assert len(runs) == 2 and len(runs[0]["gates"]) == 8
    assert conn.execute("SELECT COUNT(*) FROM audit_log WHERE action = 'close_run'").fetchone()[0] == 2


def test_changed_input_changes_the_hash(sample_db):
    service = CloseService(sample_db)
    conn = service.connect()
    before = service.input_hash(conn)
    conn.execute("UPDATE explanation SET text = text || ' x' WHERE rowid = 1")
    conn.commit()
    assert service.input_hash(conn) != before


def test_lock_and_periods(sample_db):
    service = CloseService(sample_db)
    conn = service.connect()
    service.run_close(conn, "2026-09")
    service.lock(conn, "2026-09", "test")
    listing = {p["period"]: p for p in service.periods(conn)}
    assert listing["2026-09"]["locked"] and listing["2026-09"]["last_run_status"] == "ready"
    assert not listing["2026-10"]["locked"]


def test_ledger_gate_detects_tampering(sample_db):
    service = CloseService(sample_db)
    conn = service.connect()
    conn.execute("UPDATE journal_line SET debit_minor = debit_minor + 1 WHERE id = 5 AND debit_minor > 0")
    conn.commit()
    assert gate.ledger_integrity(conn).status == "fail"


def test_fx_gate_flags_stale_closing_rate(sample_conn, sample_consolidator):
    from nordic_close.consolidate import Consolidator
    from nordic_close.fx import FxTable
    rows = [r for r in sample_conn.execute("SELECT * FROM fx_rate") if r["rate_date"] <= "2026-10-20"]
    fx = FxTable.from_rows((r["rate_date"], r["currency"], r["per_eur"]) for r in rows)
    c = Consolidator(sample_consolidator.data, fx, "SEK")
    result = gate.fx_gate(c, "2026-10")
    assert result.status == "fail" and "days older than month end" in result.reason
