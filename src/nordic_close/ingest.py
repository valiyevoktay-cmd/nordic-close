"""Load CSV inputs into the database.

Everything is validated in memory first. If anything is wrong, nothing is written.
"""

from __future__ import annotations

import csv
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

from .money import to_minor

IC_ACCOUNTS = {"1150", "2050", "4020", "5020"}

DATA_TABLES = (
    "explanation",
    "bank_statement_balance",
    "bank_statement_line",
    "budget_line",
    "journal_line",
    "journal",
    "account_map",
    "group_account",
    "entity",
)


class IngestError(Exception):
    """Raised when an input file fails validation."""


@dataclass
class LoadSummary:
    entities: int
    accounts: int
    mappings: int
    journals: int
    lines: int
    fx_rates: int
    budget_lines: int
    bank_lines: int


def _read(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        raise IngestError(f"missing input file: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        return [{k: (v or "").strip() for k, v in row.items()} for row in csv.DictReader(handle)]


def _dec(value: str, where: str) -> Decimal:
    if value == "":
        return Decimal(0)
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise IngestError(f"{where}: {value!r} is not a number") from exc


def _iso(value: str, where: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise IngestError(f"{where}: {value!r} is not a date (YYYY-MM-DD)") from exc


def _minor(value: Decimal, where: str) -> int:
    minor = to_minor(value)
    if value != Decimal(minor) / 100:
        raise IngestError(f"{where}: {value} has more than two decimals")
    return minor


def validate_journals(
    rows: list[dict[str, str]],
    entities: set[str],
    mapping: dict[tuple[str, str], str],
) -> list[dict]:
    """Return journal lines ready to insert, or raise IngestError."""
    headers: dict[str, tuple[str, str, str]] = {}
    totals: dict[str, int] = defaultdict(int)
    seen_lines: set[tuple[str, int]] = set()
    lines: list[dict] = []

    for number, row in enumerate(rows, start=2):
        jid = row["journal_id"]
        where = f"journals.csv line {number} (journal {jid})"
        if not jid:
            raise IngestError(f"journals.csv line {number}: journal_id is empty")
        entity = row["entity_code"]
        if entity not in entities:
            raise IngestError(f"{where}: unknown entity {entity!r}")
        booking = _iso(row["booking_date"], where)
        header = (entity, booking, row["description"])
        if jid in headers and headers[jid][:2] != header[:2]:
            raise IngestError(f"{where}: journal id is reused with a different entity or date")
        headers.setdefault(jid, header)

        line_no = int(row["line_no"] or 0)
        if (jid, line_no) in seen_lines:
            raise IngestError(f"{where}: line number {line_no} appears twice")
        seen_lines.add((jid, line_no))

        local = row["local_code"]
        if (entity, local) not in mapping:
            raise IngestError(f"{where}: entity {entity} has no mapping for account {local}")

        debit = _dec(row["debit"], where)
        credit = _dec(row["credit"], where)
        if debit < 0 or credit < 0:
            raise IngestError(f"{where}: amounts must not be negative")
        if debit > 0 and credit > 0:
            raise IngestError(f"{where}: a line cannot have both debit and credit")
        d_minor, c_minor = _minor(debit, where), _minor(credit, where)
        totals[jid] += d_minor - c_minor

        group = mapping[(entity, local)]
        partner = row["ic_partner"] or None
        ic_eur = row["ic_eur"]
        if group in IC_ACCOUNTS:
            if partner is None or ic_eur == "":
                raise IngestError(f"{where}: intercompany account {local} needs ic_partner and ic_eur")
            if partner not in entities or partner == entity:
                raise IngestError(f"{where}: invalid intercompany partner {partner!r}")
        elif partner is not None or ic_eur != "":
            raise IngestError(f"{where}: ic_partner is only allowed on intercompany accounts")
        ic_minor = _minor(_dec(ic_eur, where), where) if ic_eur != "" else None
        if ic_minor is not None and ic_minor < 0:
            raise IngestError(f"{where}: ic_eur must not be negative")

        lines.append(
            {
                "journal_id": jid,
                "line_no": line_no,
                "local_code": local,
                "debit": d_minor,
                "credit": c_minor,
                "ref": row["ref"],
                "memo": row["memo"],
                "ic_partner": partner,
                "ic_eur": ic_minor,
            }
        )

    for jid, total in totals.items():
        if total != 0:
            raise IngestError(
                f"journal {jid} is unbalanced: debits minus credits = {Decimal(total) / 100}"
            )
    for line in lines:
        line["_header"] = headers[line["journal_id"]]
    return lines


def load_dataset(
    conn: sqlite3.Connection, data_dir: Path, fx_csv: Path | None = None
) -> LoadSummary:
    """Replace the input tables with the contents of ``data_dir``. All or nothing."""
    entities_rows = _read(data_dir / "entities.csv")
    accounts_rows = _read(data_dir / "group_accounts.csv")
    map_rows = _read(data_dir / "account_map.csv")
    journal_rows = _read(data_dir / "journals.csv")
    budget_rows = _read(data_dir / "budget.csv")
    bank_rows = _read(data_dir / "bank_statements.csv")
    balance_rows = _read(data_dir / "bank_balances.csv")
    explanation_rows = _read(data_dir / "explanations.csv")
    fx_rows = _read(fx_csv or data_dir.parent / "fx" / "ecb_eur_rates.csv")

    entity_codes = {r["code"] for r in entities_rows}
    group_codes = {r["code"] for r in accounts_rows}
    mapping: dict[tuple[str, str], str] = {}
    for r in map_rows:
        if r["entity_code"] not in entity_codes:
            raise IngestError(f"account_map.csv: unknown entity {r['entity_code']!r}")
        if r["group_code"] not in group_codes:
            raise IngestError(
                f"account_map.csv: {r['entity_code']}/{r['local_code']} maps to unknown "
                f"group account {r['group_code']!r}"
            )
        mapping[(r["entity_code"], r["local_code"])] = r["group_code"]

    lines = validate_journals(journal_rows, entity_codes, mapping)

    locked = {r["period"] for r in conn.execute("SELECT period FROM period_lock")}
    touched = {line["_header"][1][:7] for line in lines}
    clash = sorted(touched & locked)
    if clash:
        raise IngestError(f"cannot load: period {clash[0]} is locked")

    with conn:
        for table in DATA_TABLES:
            conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed table names
        conn.execute("DELETE FROM fx_rate")
        conn.executemany(
            "INSERT INTO entity VALUES (?,?,?,?,?,?)",
            [
                (r["code"], r["name"], r["currency"], r["country"],
                 int(r["is_parent"] or 0), int(r["ownership_pct"] or 100))
                for r in entities_rows
            ],
        )
        conn.executemany(
            "INSERT INTO group_account VALUES (?,?,?,?,?,?,?)",
            [
                (r["code"], r["name"], r["kind"], r["statement"], r["report_line"],
                 int(r["sort_order"]), int(r["is_intercompany"] or 0))
                for r in accounts_rows
            ],
        )
        conn.executemany(
            "INSERT INTO account_map VALUES (?,?,?,?)",
            [(r["entity_code"], r["local_code"], r["local_name"], r["group_code"]) for r in map_rows],
        )
        journals: dict[str, tuple[str, str, str]] = {}
        for line in lines:
            journals.setdefault(line["journal_id"], line["_header"])
        conn.executemany(
            "INSERT INTO journal VALUES (?,?,?,?,?)",
            [(jid, h[0], h[1], h[1][:7], h[2]) for jid, h in journals.items()],
        )
        conn.executemany(
            "INSERT INTO journal_line "
            "(journal_id, line_no, local_code, debit_minor, credit_minor, ref, memo, "
            "ic_partner, ic_eur_minor) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            [
                (row["journal_id"], row["line_no"], row["local_code"], row["debit"], row["credit"],
                 row["ref"], row["memo"], row["ic_partner"], row["ic_eur"])
                for row in lines
            ],
        )
        conn.executemany(
            "INSERT INTO budget_line VALUES (?,?,?,?)",
            [
                (r["entity_code"], r["period"], r["group_code"],
                 _minor(_dec(r["amount"], "budget.csv"), "budget.csv"))
                for r in budget_rows
            ],
        )
        conn.executemany(
            "INSERT INTO bank_statement_line (entity_code, statement_date, ref, amount_minor, description) "
            "VALUES (?,?,?,?,?)",
            [
                (r["entity_code"], _iso(r["statement_date"], "bank_statements.csv"), r["ref"],
                 _minor(_dec(r["amount"], "bank_statements.csv"), "bank_statements.csv"),
                 r["description"])
                for r in bank_rows
            ],
        )
        conn.executemany(
            "INSERT INTO bank_statement_balance VALUES (?,?,?,?,?,?)",
            [
                (r["entity_code"], r["period"],
                 _minor(_dec(r["opening"], "bank_balances.csv"), "bank_balances.csv"),
                 _minor(_dec(r["closing"], "bank_balances.csv"), "bank_balances.csv"),
                 r["preparer"], r["reviewer"])
                for r in balance_rows
            ],
        )
        conn.executemany(
            "INSERT INTO explanation VALUES (?,?,?,?,?)",
            [(r["period"], r["line_key"], r["text"], r["author"], r["reviewer"]) for r in explanation_rows],
        )
        conn.executemany(
            "INSERT INTO fx_rate VALUES (?,?,?,?)",
            [(r["rate_date"], r["currency"], r["per_eur"], r["source"]) for r in fx_rows],
        )

    return LoadSummary(
        entities=len(entities_rows),
        accounts=len(accounts_rows),
        mappings=len(map_rows),
        journals=len({line["journal_id"] for line in lines}),
        lines=len(lines),
        fx_rates=len(fx_rows),
        budget_lines=len(budget_rows),
        bank_lines=len(bank_rows),
    )
