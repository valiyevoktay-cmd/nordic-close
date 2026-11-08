"""Application service: the one place that ties the engine, the controls and the run history."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from . import db, reports
from .config import Settings
from .consolidate import Consolidator, LedgerData
from .controls import gate
from .controls.calendar import PlanError, load_plan
from .fx import FxTable

PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

HASHED_TABLES = (
    ("entity", "code"),
    ("group_account", "code"),
    ("account_map", "entity_code, local_code"),
    ("journal", "journal_id"),
    ("journal_line", "id"),
    ("fx_rate", "rate_date, currency"),
    ("budget_line", "entity_code, period, group_code"),
    ("bank_statement_line", "id"),
    ("bank_statement_balance", "entity_code, period"),
    ("explanation", "period, line_key"),
)


class PeriodError(ValueError):
    """The period string is malformed."""


class NoDataError(LookupError):
    """The period has no ledger data."""


def jsonable(value):
    """Convert Decimals, dates and dataclasses into JSON-safe values. Decimals become strings."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [jsonable(v) for v in value]
    return value


def canonical(value) -> str:
    return json.dumps(jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


class CloseService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._cached: tuple[str, Consolidator] | None = None

    # -- plumbing ---------------------------------------------------------------------------
    def connect(self) -> sqlite3.Connection:
        return db.connect(self.settings.db_path)

    def check_period(self, period: str) -> str:
        if not PERIOD_RE.match(period or ""):
            raise PeriodError("period must look like 2026-10")
        return period

    def input_hash(self, conn: sqlite3.Connection) -> str:
        digest = hashlib.sha256()
        for table, order in HASHED_TABLES:
            digest.update(table.encode())
            for row in conn.execute(f"SELECT * FROM {table} ORDER BY {order}"):  # noqa: S608
                digest.update(repr(tuple(row)).encode())
        plan = Path(self.settings.data_dir) / "close_plan.json"
        digest.update(plan.read_bytes() if plan.exists() else b"")
        s = self.settings
        digest.update(
            repr(
                (s.presentation_currency, s.flux_pct, s.flux_abs, s.ic_tolerance_eur, s.stale_days)
            ).encode()
        )
        return digest.hexdigest()

    def consolidator(self, conn: sqlite3.Connection) -> Consolidator:
        key = self.input_hash(conn)
        with self._lock:
            if self._cached and self._cached[0] == key:
                return self._cached[1]
            c = Consolidator(
                LedgerData.load(conn),
                FxTable.from_db(conn),
                self.settings.presentation_currency,
                self.settings.ic_tolerance_eur,
            )
            self._cached = (key, c)
            return c

    def require(self, conn: sqlite3.Connection, period: str) -> Consolidator:
        self.check_period(period)
        c = self.consolidator(conn)
        if period not in c.data.periods:
            raise NoDataError(f"no data for {period}")
        return c

    # -- reads ------------------------------------------------------------------------------
    def periods(self, conn: sqlite3.Connection) -> list[dict]:
        c = self.consolidator(conn)
        locked = {r["period"] for r in conn.execute("SELECT period FROM period_lock")}
        last = {
            r["period"]: r["status"]
            for r in conn.execute(
                "SELECT period, status FROM close_run WHERE run_id IN "
                "(SELECT MAX(run_id) FROM close_run GROUP BY period)"
            )
        }
        return [
            {"period": p, "locked": p in locked, "last_run_status": last.get(p)}
            for p in c.data.periods
        ]

    def assess(self, conn: sqlite3.Connection, period: str) -> gate.Assessment:
        c = self.require(conn, period)
        return gate.assess(conn, self.settings, c, period)

    def plan(self):
        try:
            return load_plan(Path(self.settings.data_dir) / "close_plan.json")
        except PlanError:
            return None

    def figures(self, conn: sqlite3.Connection, period: str) -> dict:
        c = self.require(conn, period)
        return {
            "income_statement": reports.income_statement(c, period),
            "balance_sheet": reports.balance_sheet(c, period),
            "kpis": reports.kpis(c, period),
            "entities": reports.entity_view(c, period),
        }

    # -- writes -----------------------------------------------------------------------------
    def audit(self, conn: sqlite3.Connection, action: str, period: str | None, actor: str,
              detail: str = "") -> None:
        with conn:
            conn.execute(
                "INSERT INTO audit_log (at, action, period, actor, detail) VALUES (?,?,?,?,?)",
                (now_iso(), action, period, actor, detail),
            )

    def output_hash(self, assessment: gate.Assessment, figures: dict) -> str:
        payload = {
            "status": assessment.status,
            "gates": [(g.name, g.status, g.reason) for g in assessment.gates],
            "income_statement": figures["income_statement"],
            "balance_sheet": figures["balance_sheet"],
        }
        return hashlib.sha256(canonical(payload).encode()).hexdigest()

    def run_close(self, conn: sqlite3.Connection, period: str, actor: str = "api") -> dict:
        assessment = self.assess(conn, period)
        figures = self.figures(conn, period)
        input_hash = self.input_hash(conn)
        output_hash = self.output_hash(assessment, figures)
        net_income = next(
            r for r in figures["income_statement"] if r.key == "net_income"
        ).values["ytd"]
        summary = {
            "net_income_ytd": str(net_income),
            "failed_gates": [g.name for g in assessment.gates if g.status == "fail"],
        }
        started = now_iso()
        with conn:
            cur = conn.execute(
                "INSERT INTO close_run (period, status, input_hash, output_hash, "
                "presentation_currency, started_at, summary_json) VALUES (?,?,?,?,?,?,?)",
                (period, assessment.status, input_hash, output_hash,
                 self.settings.presentation_currency, started, canonical(summary)),
            )
            run_id = cur.lastrowid
            conn.executemany(
                "INSERT INTO close_run_gate (run_id, gate, status, reason, detail_json) "
                "VALUES (?,?,?,?,?)",
                [
                    (run_id, g.name, g.status, g.reason, canonical(g.detail))
                    for g in assessment.gates
                ],
            )
        self.audit(conn, "close_run", period, actor,
                   f"run {run_id}: {assessment.status}, input {input_hash[:12]}")
        return {
            "run_id": run_id,
            "period": period,
            "status": assessment.status,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "started_at": started,
            "gates": [
                {"gate": g.name, "label": g.label, "status": g.status, "reason": g.reason}
                for g in assessment.gates
            ],
        }

    def runs(self, conn: sqlite3.Connection, period: str | None = None) -> list[dict]:
        if period is not None:
            self.check_period(period)
        rows = conn.execute(
            "SELECT * FROM close_run WHERE (? IS NULL OR period = ?) ORDER BY run_id DESC LIMIT 50",
            (period, period),
        ).fetchall()
        out = []
        for r in rows:
            gates = conn.execute(
                "SELECT gate, status, reason FROM close_run_gate WHERE run_id = ? ORDER BY rowid",
                (r["run_id"],),
            ).fetchall()
            out.append(
                {
                    "run_id": r["run_id"],
                    "period": r["period"],
                    "status": r["status"],
                    "input_hash": r["input_hash"],
                    "output_hash": r["output_hash"],
                    "started_at": r["started_at"],
                    "gates": [dict(g) for g in gates],
                }
            )
        return out

    def lock(self, conn: sqlite3.Connection, period: str, actor: str) -> None:
        self.check_period(period)
        with conn:
            conn.execute(
                "INSERT OR REPLACE INTO period_lock VALUES (?,?,?)", (period, now_iso(), actor)
            )
        self.audit(conn, "period_lock", period, actor)
