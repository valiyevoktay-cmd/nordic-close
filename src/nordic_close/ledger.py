"""Reading entities, accounts and ledger movements from the database."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal

from .money import from_minor


@dataclass(frozen=True)
class Entity:
    code: str
    name: str
    currency: str
    country: str
    is_parent: bool


@dataclass(frozen=True)
class Account:
    code: str
    name: str
    kind: str
    statement: str
    report_line: str
    sort_order: int
    is_intercompany: bool

    @property
    def is_pl(self) -> bool:
        return self.statement == "PL"


def load_entities(conn: sqlite3.Connection) -> list[Entity]:
    rows = conn.execute("SELECT * FROM entity ORDER BY is_parent DESC, code")
    return [
        Entity(r["code"], r["name"], r["currency"], r["country"], bool(r["is_parent"]))
        for r in rows
    ]


def load_accounts(conn: sqlite3.Connection) -> dict[str, Account]:
    rows = conn.execute("SELECT * FROM group_account ORDER BY sort_order")
    return {
        r["code"]: Account(
            r["code"],
            r["name"],
            r["kind"],
            r["statement"],
            r["report_line"],
            r["sort_order"],
            bool(r["is_intercompany"]),
        )
        for r in rows
    }


def periods_with_data(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT period FROM journal ORDER BY period")
    return [r["period"] for r in rows]


def previous_period(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7])
    if month == 1:
        return f"{year - 1}-12"
    return f"{year}-{month - 1:02d}"


def movements(conn: sqlite3.Connection, entity_code: str) -> dict[tuple[str, str], Decimal]:
    """Net debit-positive movement per (period, group account) in the entity's currency."""
    rows = conn.execute(
        """
        SELECT j.period AS period, m.group_code AS code,
               SUM(l.debit_minor) - SUM(l.credit_minor) AS net
        FROM journal_line l
        JOIN journal j ON j.journal_id = l.journal_id
        JOIN account_map m ON m.entity_code = j.entity_code AND m.local_code = l.local_code
        WHERE j.entity_code = ?
        GROUP BY j.period, m.group_code
        """,
        (entity_code,),
    )
    return {(r["period"], r["code"]): from_minor(r["net"]) for r in rows}


def trial_balance(
    moves: dict[tuple[str, str], Decimal], accounts: dict[str, Account], period: str
) -> dict[str, Decimal]:
    """Local-currency trial balance: balance-sheet accounts cumulative, P&L for the month."""
    tb: dict[str, Decimal] = {}
    for (p, code), amount in moves.items():
        account = accounts[code]
        if account.is_pl:
            if p != period:
                continue
        elif p > period:
            continue
        tb[code] = tb.get(code, Decimal(0)) + amount
    return tb


IcKey = tuple[str, str, str, str]


def ic_flows(
    conn: sqlite3.Connection, group_codes: tuple[str, ...]
) -> tuple[dict[IcKey, Decimal], dict[IcKey, Decimal]]:
    """Intercompany amounts per (entity, partner, group account, period), debit-positive.

    Returns two dictionaries: the amounts in EUR (the invoicing currency) and the amounts in
    the entity's own currency.
    """
    placeholders = ",".join("?" for _ in group_codes)
    rows = conn.execute(
        f"""
        SELECT j.entity_code AS entity, l.ic_partner AS partner, m.group_code AS code,
               j.period AS period,
               SUM(CASE WHEN l.debit_minor > 0 THEN l.ic_eur_minor ELSE -l.ic_eur_minor END) AS eur,
               SUM(l.debit_minor) - SUM(l.credit_minor) AS local
        FROM journal_line l
        JOIN journal j ON j.journal_id = l.journal_id
        JOIN account_map m ON m.entity_code = j.entity_code AND m.local_code = l.local_code
        WHERE l.ic_partner IS NOT NULL AND m.group_code IN ({placeholders})
        GROUP BY j.entity_code, l.ic_partner, m.group_code, j.period
        """,  # noqa: S608 - only placeholders are interpolated
        group_codes,
    )
    eur: dict[IcKey, Decimal] = {}
    local: dict[IcKey, Decimal] = {}
    for r in rows:
        key = (r["entity"], r["partner"], r["code"], r["period"])
        eur[key] = from_minor(r["eur"])
        local[key] = from_minor(r["local"])
    return eur, local
