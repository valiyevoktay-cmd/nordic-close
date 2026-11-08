import sqlite3

import pytest

from nordic_close import db, ingest
from tests.conftest import line, load, write_dataset


def balanced(jid="J1", ent="SE", day="2026-01-10"):
    return (jid, ent, day, [line("1010", 100), line("4010", -100)])


def try_load(tmp_path, journals):
    folder = write_dataset(tmp_path / "data", journals)
    conn = db.connect(":memory:")
    db.init_schema(conn)
    return conn, folder


def test_valid_dataset_loads(tmp_path):
    conn, folder = try_load(tmp_path, [balanced()])
    summary = ingest.load_dataset(conn, folder)
    assert summary.journals == 1 and summary.lines == 2
    assert conn.execute("SELECT COUNT(*) FROM fx_rate").fetchone()[0] > 0


def test_unbalanced_journal_is_rejected_and_nothing_written(tmp_path):  # AC-1
    conn, folder = try_load(tmp_path, [("J9", "SE", "2026-01-10", [line("1010", 100), line("4010", -99.99)])])
    with pytest.raises(ingest.IngestError, match="J9 is unbalanced"):
        ingest.load_dataset(conn, folder)
    assert conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 0


def test_unmapped_account_is_rejected(tmp_path):  # AC-2
    conn, folder = try_load(tmp_path, [("J1", "SE", "2026-01-10", [line("9999", 5), line("4010", -5)])])
    with pytest.raises(ingest.IngestError, match="entity SE has no mapping for account 9999"):
        ingest.load_dataset(conn, folder)


def test_unknown_entity_is_rejected(tmp_path):
    conn, folder = try_load(tmp_path, [balanced(ent="XX")])
    with pytest.raises(ingest.IngestError, match="unknown entity 'XX'"):
        ingest.load_dataset(conn, folder)


def test_journal_id_reused_for_another_entity(tmp_path):
    conn, folder = try_load(tmp_path, [balanced("J1", "SE"), balanced("J1", "FI")])
    with pytest.raises(ingest.IngestError, match="reused"):
        ingest.load_dataset(conn, folder)


def test_line_with_debit_and_credit_is_rejected(tmp_path):
    folder = write_dataset(tmp_path / "data", [balanced()])
    text = (folder / "journals.csv").read_text().replace("100.00,,", "100.00,100.00,", 1)
    (folder / "journals.csv").write_text(text)
    conn = db.connect(":memory:")
    db.init_schema(conn)
    with pytest.raises(ingest.IngestError, match="both debit and credit"):
        ingest.load_dataset(conn, folder)


def test_three_decimals_rejected(tmp_path):
    folder = write_dataset(tmp_path / "data", [balanced()])
    text = (folder / "journals.csv").read_text().replace("100.00", "100.001")
    (folder / "journals.csv").write_text(text)
    conn = db.connect(":memory:")
    db.init_schema(conn)
    with pytest.raises(ingest.IngestError, match="more than two decimals"):
        ingest.load_dataset(conn, folder)


def test_intercompany_line_needs_partner_and_eur(tmp_path):
    conn, folder = try_load(tmp_path, [("J1", "SE", "2026-01-10", [line("1150", 10), line("4020", -10)])])
    with pytest.raises(ingest.IngestError, match="needs ic_partner and ic_eur"):
        ingest.load_dataset(conn, folder)


def test_partner_on_ordinary_account_is_rejected(tmp_path):
    conn, folder = try_load(tmp_path, [("J1", "SE", "2026-01-10",
                                        [line("1010", 10, partner="FI", eur="1"), line("4010", -10)])])
    with pytest.raises(ingest.IngestError, match="only allowed on intercompany"):
        ingest.load_dataset(conn, folder)


def test_bad_date_and_bad_number(tmp_path):
    conn, folder = try_load(tmp_path, [balanced(day="2026-13-40")])
    with pytest.raises(ingest.IngestError, match="not a date"):
        ingest.load_dataset(conn, folder)
    folder2 = write_dataset(tmp_path / "d2", [balanced()])
    (folder2 / "journals.csv").write_text((folder2 / "journals.csv").read_text().replace("100.00", "abc", 1))
    with pytest.raises(ingest.IngestError, match="not a number"):
        ingest.load_dataset(conn, folder2)


def test_missing_file(tmp_path):
    conn, folder = try_load(tmp_path, [balanced()])
    (folder / "budget.csv").unlink()
    with pytest.raises(ingest.IngestError, match="missing input file"):
        ingest.load_dataset(conn, folder)


def test_locked_period_blocks_reload(tmp_path):
    conn, folder = try_load(tmp_path, [balanced()])
    ingest.load_dataset(conn, folder)
    conn.execute("INSERT INTO period_lock VALUES ('2026-01', 'now', 'test')")
    conn.commit()
    with pytest.raises(ingest.IngestError, match="period 2026-01 is locked"):
        ingest.load_dataset(conn, folder)


def test_reload_replaces_data(tmp_path):
    conn, folder = try_load(tmp_path, [balanced()])
    ingest.load_dataset(conn, folder)
    ingest.load_dataset(conn, folder)
    assert conn.execute("SELECT COUNT(*) FROM journal").fetchone()[0] == 1


def test_database_rejects_unbalanced_sign_rules(tmp_path):
    conn, folder = try_load(tmp_path, [balanced()])
    ingest.load_dataset(conn, folder)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO journal_line (journal_id, line_no, local_code, debit_minor, credit_minor) "
            "VALUES ('J1', 9, '1010', 5, 5)"
        )


def test_load_helper_roundtrip(tmp_path):
    conn, consolidator = load(write_dataset(tmp_path / "x", [balanced()]))
    assert consolidator.data.periods == ["2026-01"]
