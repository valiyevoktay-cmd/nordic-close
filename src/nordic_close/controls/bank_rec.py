"""Bank reconciliation: match the ledger cash account to the bank statement and prove it.

Matching runs over the whole history up to the period end, so an item that was outstanding at
the end of one month and cleared in the next is matched when it clears.

Amounts are signed from the company's point of view: money in is positive.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from ..fx import period_bounds
from ..money import from_minor

DATE_WINDOW_DAYS = 3
TRANSPOSITION_WINDOW_DAYS = 10
OPENING_REF = "OPENING"


@dataclass
class Item:
    side: str  # "ledger" or "bank"
    day: date
    ref: str
    amount: Decimal
    description: str
    age_days: int = 0


@dataclass
class Transposition:
    ledger: Item
    bank: Item

    @property
    def difference(self) -> Decimal:
        return self.bank.amount - self.ledger.amount


@dataclass
class RecResult:
    entity: str
    period: str
    ledger_opening: Decimal
    ledger_closing: Decimal
    bank_opening: Decimal
    bank_closing: Decimal
    matched: int
    outstanding: list[Item] = field(default_factory=list)  # in ledger, not yet at the bank
    unrecorded: list[Item] = field(default_factory=list)  # at the bank, not in the ledger
    transpositions: list[Transposition] = field(default_factory=list)
    proof_difference: Decimal = Decimal(0)
    stale_days: int = 90
    preparer: str = ""
    reviewer: str = ""
    journals: list[dict] = field(default_factory=list)
    has_statement: bool = True

    @property
    def stale(self) -> list[Item]:
        return [i for i in self.outstanding if i.age_days > self.stale_days]

    @property
    def segregation_ok(self) -> bool:
        return bool(self.preparer) and bool(self.reviewer) and self.preparer != self.reviewer

    @property
    def status(self) -> str:
        if not self.has_statement:
            return "no_statement"
        if self.proof_difference != 0:
            return "not_proven"
        if self.unrecorded or self.transpositions:
            return "open_items"
        if self.stale:
            return "stale_items"
        if self.outstanding:
            return "timing_only"
        return "reconciled"

    @property
    def passes(self) -> bool:
        return self.status in {"reconciled", "timing_only"} and self.segregation_ok


def _ledger_lines(conn: sqlite3.Connection, entity: str, end: date) -> list[Item]:
    rows = conn.execute(
        """
        SELECT j.booking_date AS day, l.ref AS ref, j.description AS description,
               l.debit_minor - l.credit_minor AS net
        FROM journal_line l
        JOIN journal j ON j.journal_id = l.journal_id
        JOIN account_map m ON m.entity_code = j.entity_code AND m.local_code = l.local_code
        WHERE j.entity_code = ? AND m.group_code = '1010' AND j.booking_date <= ?
        ORDER BY j.booking_date, l.id
        """,
        (entity, end.isoformat()),
    )
    return [
        Item("ledger", date.fromisoformat(r["day"]), r["ref"], from_minor(r["net"]), r["description"])
        for r in rows
    ]


def _bank_lines(conn: sqlite3.Connection, entity: str, end: date) -> list[Item]:
    rows = conn.execute(
        """
        SELECT statement_date AS day, ref, amount_minor, description
        FROM bank_statement_line
        WHERE entity_code = ? AND statement_date <= ?
        ORDER BY statement_date, id
        """,
        (entity, end.isoformat()),
    )
    return [
        Item("bank", date.fromisoformat(r["day"]), r["ref"], from_minor(r["amount_minor"]), r["description"])
        for r in rows
    ]


def _digits(amount: Decimal) -> str:
    return "".join(sorted(f"{abs(amount):.2f}".replace(".", "")))


def match(ledger: list[Item], bank: list[Item]) -> tuple[list[Item], list[Item], list[Transposition], int]:
    """Three passes: reference and amount, amount within a date window, digit transposition."""
    ledger_left = list(ledger)
    bank_left = list(bank)
    matched = 0

    # Pass 1: same reference and same amount. Each bank line is used at most once.
    for item in list(ledger_left):
        if not item.ref:
            continue
        for candidate in bank_left:
            if candidate.ref == item.ref and candidate.amount == item.amount:
                ledger_left.remove(item)
                bank_left.remove(candidate)
                matched += 1
                break

    # Pass 2: same amount, dates within the window; take the closest date.
    for item in list(ledger_left):
        options = [
            c for c in bank_left
            if c.amount == item.amount and abs((c.day - item.day).days) <= DATE_WINDOW_DAYS
        ]
        if options:
            best = min(options, key=lambda c: abs((c.day - item.day).days))
            ledger_left.remove(item)
            bank_left.remove(best)
            matched += 1

    # Pass 3: transposed digits. Same digits, difference divisible by 9, same direction.
    transpositions: list[Transposition] = []
    for item in list(ledger_left):
        for candidate in list(bank_left):
            diff = abs(candidate.amount - item.amount)
            if (
                item.amount * candidate.amount > 0
                and item.amount != candidate.amount
                and _digits(item.amount) == _digits(candidate.amount)
                and int(diff * 100) % 9 == 0
                and abs((candidate.day - item.day).days) <= TRANSPOSITION_WINDOW_DAYS
            ):
                transpositions.append(Transposition(item, candidate))
                ledger_left.remove(item)
                bank_left.remove(candidate)
                break
    return ledger_left, bank_left, transpositions, matched


def _journal_for(item: Item, entity: str, as_of: date) -> dict:
    """The entry that would record a bank-only item in the ledger."""
    if item.amount < 0:
        debit, credit, note = "6020", "1010", "Expense on the statement not in the ledger"
    else:
        debit, credit = "1010", "1100"
        note = "Receipt on the statement not in the ledger; apply to a customer"
    return {
        "entity": entity,
        "date": as_of.isoformat(),
        "debit": debit,
        "credit": credit,
        "amount": str(abs(item.amount)),
        "reference": item.ref or item.description,
        "note": note,
    }


def reconcile(
    conn: sqlite3.Connection,
    entity: str,
    period: str,
    stale_days: int = 90,
) -> RecResult:
    _, end = period_bounds(period)
    balance = conn.execute(
        "SELECT * FROM bank_statement_balance WHERE entity_code = ? AND period = ?",
        (entity, period),
    ).fetchone()
    first = conn.execute(
        "SELECT opening_minor FROM bank_statement_balance WHERE entity_code = ? ORDER BY period LIMIT 1",
        (entity,),
    ).fetchone()
    if balance is None or first is None:
        return RecResult(entity, period, Decimal(0), Decimal(0), Decimal(0), Decimal(0), 0,
                         stale_days=stale_days, has_statement=False)

    all_ledger = _ledger_lines(conn, entity, end)
    opening_lines = [i for i in all_ledger if i.ref == OPENING_REF]
    ledger = [i for i in all_ledger if i.ref != OPENING_REF]
    bank = _bank_lines(conn, entity, end)

    ledger_opening = sum((i.amount for i in opening_lines), Decimal(0))
    ledger_closing = ledger_opening + sum((i.amount for i in ledger), Decimal(0))
    bank_closing = from_minor(balance["closing_minor"])

    outstanding, unrecorded, transpositions, matched = match(ledger, bank)
    for item in outstanding + unrecorded:
        item.age_days = (end - item.day).days
    outstanding_net = sum((i.amount for i in outstanding), Decimal(0))
    unrecorded_net = sum((i.amount for i in unrecorded), Decimal(0))
    trans_ledger = sum((t.ledger.amount for t in transpositions), Decimal(0))
    trans_bank = sum((t.bank.amount for t in transpositions), Decimal(0))

    # bank = ledger - ledger-only items + bank-only items (transposed pairs count on both sides)
    explained_bank = (
        ledger_closing - outstanding_net - trans_ledger + unrecorded_net + trans_bank
    )
    proof = bank_closing - explained_bank

    journals = [_journal_for(i, entity, end) for i in unrecorded]
    for t in transpositions:
        counter = "2010" if t.ledger.amount < 0 else "1100"
        journals.append({
            "entity": entity,
            "date": end.isoformat(),
            "debit": "1010" if t.difference > 0 else counter,
            "credit": counter if t.difference > 0 else "1010",
            "amount": str(abs(t.difference)),
            "reference": t.ledger.ref,
            "note": f"Correct the ledger amount {t.ledger.amount} to {t.bank.amount} (digits transposed)",
        })

    return RecResult(
        entity=entity,
        period=period,
        ledger_opening=ledger_opening,
        ledger_closing=ledger_closing,
        bank_opening=from_minor(balance["opening_minor"]),
        bank_closing=bank_closing,
        matched=matched,
        outstanding=outstanding,
        unrecorded=unrecorded,
        transpositions=transpositions,
        proof_difference=proof,
        stale_days=stale_days,
        preparer=balance["preparer"],
        reviewer=balance["reviewer"],
        journals=journals,
    )
