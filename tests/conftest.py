"""Shared fixtures: the sample dataset and a tiny builder for focused scenarios."""

from __future__ import annotations

import csv
from datetime import date, timedelta
from pathlib import Path

import pytest

from nordic_close import db, ingest
from nordic_close.config import Settings
from nordic_close.consolidate import Consolidator, LedgerData
from nordic_close.fx import FxTable

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "data" / "sample"
FX_FILE = ROOT / "data" / "fx" / "ecb_eur_rates.csv"

ACCOUNTS = [
    ("1010", "Cash", "ASSET", "BS", "cash", 10, 0),
    ("1100", "Receivables", "ASSET", "BS", "trade_receivables", 20, 0),
    ("1150", "IC receivables", "ASSET", "BS", "ic_receivables", 25, 1),
    ("1190", "IC suspense", "ASSET", "BS", "ic_suspense", 27, 0),
    ("2010", "Payables", "LIABILITY", "BS", "trade_payables", 60, 0),
    ("2050", "IC payables", "LIABILITY", "BS", "ic_payables", 65, 1),
    ("3010", "Share capital", "EQUITY", "BS", "share_capital", 100, 0),
    ("3050", "Retained earnings", "EQUITY", "BS", "retained_earnings", 110, 0),
    ("3090", "Translation reserve", "EQUITY", "BS", "translation_reserve", 120, 0),
    ("4010", "Revenue", "INCOME", "PL", "revenue", 200, 0),
    ("4020", "IC revenue", "INCOME", "PL", "ic_revenue", 210, 1),
    ("5010", "COGS", "EXPENSE", "PL", "cogs", 220, 0),
    ("5020", "IC COGS", "EXPENSE", "PL", "ic_cogs", 230, 1),
    ("6020", "Other opex", "EXPENSE", "PL", "other_opex", 250, 0),
    ("7030", "IC FX residual", "EXPENSE", "PL", "ic_fx_residual", 285, 0),
    ("7090", "IC P&L suspense", "EXPENSE", "PL", "ic_pl_suspense", 295, 0),
    ("8010", "Tax", "EXPENSE", "PL", "income_tax", 300, 0),
]


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def line(acc, amount, ref="", partner="", eur=""):
    return {"acc": acc, "amount": amount, "ref": ref, "partner": partner, "eur": eur}


def write_dataset(
    folder: Path,
    journals: list[tuple[str, str, str, list[dict]]],
    *,
    bank: list[tuple[str, str, str, str]] = (),
    balances: list[tuple[str, str, str, str]] = (),
    sek_per_eur: str = "10",
    entities: tuple[tuple[str, str, int], ...] = (("SE", "SEK", 1), ("FI", "EUR", 0)),
) -> Path:
    """Write a minimal dataset. Local account codes equal the group codes.

    journals: (journal_id, entity, date, lines) with signed amounts (debit positive).
    bank: (entity, date, ref, signed amount). balances: (entity, period, opening, closing).
    """
    folder.mkdir(parents=True, exist_ok=True)
    write_csv(folder / "entities.csv",
              ["code", "name", "currency", "country", "is_parent", "ownership_pct"],
              [dict(code=c, name=f"Entity {c}", currency=cur, country=c, is_parent=p,
                    ownership_pct=100) for c, cur, p in entities])
    write_csv(folder / "group_accounts.csv",
              ["code", "name", "kind", "statement", "report_line", "sort_order", "is_intercompany"],
              [dict(zip(["code", "name", "kind", "statement", "report_line", "sort_order",
                         "is_intercompany"], a, strict=True)) for a in ACCOUNTS])
    write_csv(folder / "account_map.csv", ["entity_code", "local_code", "local_name", "group_code"],
              [dict(entity_code=c, local_code=a[0], local_name=a[1], group_code=a[0])
               for c, _, _ in entities for a in ACCOUNTS])
    rows = []
    for jid, ent, day, lines in journals:
        for n, ln in enumerate(lines, start=1):
            amt = ln["amount"]
            rows.append({
                "journal_id": jid, "entity_code": ent, "booking_date": day, "description": "test",
                "line_no": n, "local_code": ln["acc"],
                "debit": f"{amt:.2f}" if amt > 0 else "", "credit": f"{-amt:.2f}" if amt < 0 else "",
                "ref": ln["ref"], "memo": "", "ic_partner": ln["partner"], "ic_eur": ln["eur"],
            })
    write_csv(folder / "journals.csv",
              ["journal_id", "entity_code", "booking_date", "description", "line_no", "local_code",
               "debit", "credit", "ref", "memo", "ic_partner", "ic_eur"], rows)
    write_csv(folder / "budget.csv", ["entity_code", "period", "group_code", "amount"], [])
    write_csv(folder / "bank_statements.csv",
              ["entity_code", "statement_date", "ref", "amount", "description"],
              [dict(entity_code=e, statement_date=d, ref=r, amount=a, description="") for e, d, r, a in bank])
    write_csv(folder / "bank_balances.csv",
              ["entity_code", "period", "opening", "closing", "preparer", "reviewer"],
              [dict(entity_code=e, period=p, opening=o, closing=c, preparer="Ann", reviewer="Bo")
               for e, p, o, c in balances])
    write_csv(folder / "explanations.csv", ["period", "line_key", "text", "author", "reviewer"], [])

    fx_dir = folder.parent / "fx"
    fx_dir.mkdir(exist_ok=True)
    rates = []
    day = date(2025, 12, 1)
    while day <= date(2026, 3, 31):
        if day.weekday() < 5:
            rates.append(dict(rate_date=day.isoformat(), currency="SEK", per_eur=sek_per_eur, source="test"))
        day += timedelta(days=1)
    write_csv(fx_dir / "ecb_eur_rates.csv", ["rate_date", "currency", "per_eur", "source"], rates)
    return folder


def load(folder: Path, presentation: str = "SEK"):
    conn = db.connect(":memory:")
    db.init_schema(conn)
    ingest.load_dataset(conn, folder)
    consolidator = Consolidator(LedgerData.load(conn), FxTable.from_db(conn), presentation)
    return conn, consolidator


@pytest.fixture(scope="session")
def sample_conn():
    conn = db.connect(":memory:")
    db.init_schema(conn)
    ingest.load_dataset(conn, SAMPLE)
    return conn


@pytest.fixture(scope="session")
def sample_consolidator(sample_conn):
    return Consolidator(LedgerData.load(sample_conn), FxTable.from_db(sample_conn), "SEK")


@pytest.fixture()
def sample_settings(tmp_path):
    return Settings.from_env({
        "NORDIC_CLOSE_DB": str(tmp_path / "close.db"),
        "NORDIC_CLOSE_DATA": str(SAMPLE),
    })


@pytest.fixture()
def sample_db(sample_settings):
    conn = db.connect(sample_settings.db_path)
    db.init_schema(conn)
    ingest.load_dataset(conn, SAMPLE)
    conn.close()
    return sample_settings
