import io
import json

import pytest
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from nordic_close import cli, db, ecb
from nordic_close.api import create_app
from nordic_close.config import Settings
from tests.conftest import SAMPLE


@pytest.fixture()
def client(sample_db):
    return TestClient(create_app(sample_db))


def test_health_and_static_page(client):
    assert client.get("/api/health").json()["status"] == "ok"
    page = client.get("/")
    assert page.status_code == 200 and "Nordic close" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]
    assert page.headers["x-content-type-options"] == "nosniff"


def test_reads_return_decimal_strings(client):
    body = client.get("/api/reports/income-statement?period=2026-10").json()
    revenue = next(r for r in body["rows"] if r["key"] == "revenue")
    assert isinstance(revenue["values"]["month"], str) and body["currency"] == "SEK"
    assert client.get("/api/reports/balance-sheet?period=2026-10").json()["check"] == "0.00"


@pytest.mark.parametrize("path", [
    "/api/periods", "/api/entities", "/api/reports/kpis?period=2026-10",
    "/api/reports/entities?period=2026-10", "/api/reports/variance?period=2026-10&view=ytd",
    "/api/controls/summary?period=2026-10", "/api/controls/intercompany?period=2026-10",
    "/api/controls/reconciliations?period=2026-10", "/api/controls/flux?period=2026-10",
    "/api/controls/calendar", "/api/fx?period=2026-10", "/api/close/runs",
])
def test_every_read_route_answers(client, path):
    assert client.get(path).status_code == 200


def test_summary_explains_the_block(client):
    body = client.get("/api/controls/summary?period=2026-10").json()
    assert body["status"] == "blocked"
    failed = {g["gate"]: g["reason"] for g in body["gates"] if g["status"] == "fail"}
    assert "SE -> DK" in failed["intercompany"]


def test_intercompany_and_reconciliation_detail(client):
    ic = client.get("/api/controls/intercompany?period=2026-10").json()
    pair = next(p for p in ic["pairs"] if not p["matches"])
    assert pair["suggested_journal"]["entity"] == "DK" and ic["eliminations"]
    rec = client.get("/api/controls/reconciliations?period=2026-10").json()["reconciliations"]
    fi = next(r for r in rec if r["entity"] == "FI")
    assert fi["transpositions"][0]["difference"] == "270.00"


def test_no_data_is_404_and_bad_period_is_422(client):  # EC-7, EC-8
    assert client.get("/api/reports/kpis?period=2027-01").status_code == 404
    assert client.get("/api/reports/kpis?period=2026-13").status_code == 422
    assert client.get("/api/reports/kpis?period=oct").status_code == 422
    assert client.get("/api/close/runs?period=bad").status_code == 422
    assert client.post("/api/close/runs", json={"period": "nope"}).status_code == 422
    assert client.get("/api/export/pack.xlsx?period=2027-01").status_code == 404


def test_post_run_stores_history(client):
    created = client.post("/api/close/runs", json={"period": "2026-10"})
    assert created.status_code == 201 and created.json()["status"] == "blocked"
    history = client.get("/api/close/runs?period=2026-10").json()
    assert history[0]["run_id"] == created.json()["run_id"]


def test_api_key_is_enforced(sample_db):  # AC-20
    keyed = Settings.from_env({
        "NORDIC_CLOSE_DB": str(sample_db.db_path), "NORDIC_CLOSE_DATA": str(SAMPLE),
        "NORDIC_CLOSE_API_KEY": "s3cret"})
    c = TestClient(create_app(keyed))
    assert c.get("/api/periods").status_code == 401
    assert c.get("/api/periods", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/api/periods", headers={"X-API-Key": "s3cret"}).status_code == 200
    assert c.post("/api/close/runs", json={"period": "2026-10"}).status_code == 401
    assert c.get("/api/health").status_code == 200


def test_missing_database_gives_503(tmp_path):
    settings = Settings.from_env({"NORDIC_CLOSE_DB": str(tmp_path / "none.db")})
    assert TestClient(create_app(settings)).get("/api/periods").status_code == 503


def test_excel_export_has_live_formulas_and_matching_values(client):
    response = client.get("/api/export/pack.xlsx?period=2026-10")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    formulas = load_workbook(io.BytesIO(response.content))
    values = load_workbook(io.BytesIO(response.content), data_only=True)
    assert formulas.sheetnames[:3] == ["Summary", "Income statement", "Balance sheet"]
    sheet = formulas["Income statement"]
    labels = {sheet.cell(row=r, column=1).value: r for r in range(1, 30)}
    row = labels["Net income"]
    assert str(sheet.cell(row=row, column=4).value).startswith("=")
    api = client.get("/api/reports/income-statement?period=2026-10").json()
    net = next(r for r in api["rows"] if r["key"] == "net_income")["values"]["ytd"]
    assert abs(values["Income statement"].cell(row=row, column=4).value - float(net)) < 0.01
    assert values["Summary"]["B3"].value == "BLOCKED"
    audit = db.connect(client.app.state.settings.db_path).execute(
        "SELECT COUNT(*) FROM audit_log WHERE action = 'export'").fetchone()[0]
    assert audit == 1


# ---- command line

@pytest.fixture()
def cli_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NORDIC_CLOSE_DB", str(tmp_path / "cli.db"))
    monkeypatch.setenv("NORDIC_CLOSE_DATA", str(SAMPLE))
    return tmp_path


def test_cli_init_close_export(cli_env, capsys):
    assert cli.main(["init"]) == 0
    assert "loaded 4 entities" in capsys.readouterr().out
    assert cli.main(["close", "--period", "2026-09"]) == 0
    assert "READY" in capsys.readouterr().out
    assert cli.main(["close", "--period", "2026-10", "--json"]) == 4
    assert json.loads(capsys.readouterr().out)["status"] == "blocked"
    out = cli_env / "pack.xlsx"
    assert cli.main(["export", "--period", "2026-10", "--out", str(out)]) == 0
    assert out.stat().st_size > 5000


def test_cli_lock_only_when_ready(cli_env):
    cli.main(["init"])
    cli.main(["close", "--period", "2026-10", "--lock"])
    cli.main(["close", "--period", "2026-09", "--lock"])
    conn = db.connect(cli_env / "cli.db")
    assert [r["period"] for r in conn.execute("SELECT period FROM period_lock")] == ["2026-09"]


def test_cli_errors(cli_env, capsys, tmp_path):
    cli.main(["init"])
    assert cli.main(["close", "--period", "2030-01"]) == 2
    assert cli.main(["close", "--period", "x"]) == 2
    assert cli.main(["export", "--period", "2030-01"]) == 2
    empty = tmp_path / "empty"
    empty.mkdir()
    assert cli.main(["init", "--data", str(empty)]) == 1
    assert "load failed" in capsys.readouterr().err


def test_cli_init_force_resets(cli_env):
    cli.main(["init"])
    cli.main(["close", "--period", "2026-09"])
    cli.main(["init", "--force"])
    conn = db.connect(cli_env / "cli.db")
    assert conn.execute("SELECT COUNT(*) FROM close_run").fetchone()[0] == 0


def test_cli_serve_and_fx_update(cli_env, monkeypatch, tmp_path):
    called = {}
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: called.update(kw))
    assert cli.main(["serve", "--port", "9999"]) == 0
    assert called["port"] == 9999 and called["host"] == "127.0.0.1"

    class Fake(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(url, timeout):
        assert url.startswith("https://data-api.ecb.europa.eu/")
        return Fake(b"KEY,TIME_PERIOD,OBS_VALUE\nx,2026-01-05,10.5\nx,2026-01-06,\n")

    monkeypatch.setattr(ecb.urllib.request, "urlopen", fake_urlopen)
    out = tmp_path / "rates.csv"
    assert cli.main(["fx-update", "--start", "2026-01-01", "--end", "2026-01-31", "--out", str(out)]) == 0
    lines = out.read_text().splitlines()
    assert lines[0] == "rate_date,currency,per_eur,source" and len(lines) == 4  # 3 currencies x 1 row


def test_settings_validation():
    with pytest.raises(ValueError, match="presentation currency"):
        Settings.from_env({"NORDIC_CLOSE_PRESENTATION_CURRENCY": "USD"})
    assert Settings.from_env({"NORDIC_CLOSE_PRESENTATION_CURRENCY": "nok"}).presentation_currency == "NOK"


def test_db_reset_and_presentation_currency_switch(tmp_path):
    from nordic_close import ingest
    from nordic_close.consolidate import Consolidator, LedgerData
    from nordic_close.fx import FxTable
    conn = db.connect(":memory:")
    db.init_schema(conn)
    ingest.load_dataset(conn, SAMPLE)
    c = Consolidator(LedgerData.load(conn), FxTable.from_db(conn), "EUR")
    state = c.state("2026-10")
    assert state.entities["FI"].rates.closing == 1
    assert state.tb_sum() == 0
    db.reset(conn)
    assert conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 0


def test_text_starting_with_equals_is_not_a_formula(sample_conn, sample_consolidator):
    import io as _io

    from nordic_close.controls.gate import assess
    from nordic_close.excel_pack import build_pack
    assessment = assess(sample_conn, Settings.from_env({}), sample_consolidator, "2026-10")
    assessment.gates[0].reason = '=HYPERLINK("http://example.com","x")'
    data = build_pack(sample_consolidator, assessment, "2026-10")
    sheet = load_workbook(_io.BytesIO(data))["Summary"]
    cells = [c for row in sheet.iter_rows() for c in row if c.value and "HYPERLINK" in str(c.value)]
    assert cells and all(c.data_type == "s" for c in cells)
