"""The sign-off gate: run every control for a period and say ready or blocked, with reasons."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path

from .. import reports
from ..config import Settings
from ..consolidate import Consolidator, translation_proof
from ..fx import RateError, period_bounds
from ..ledger import load_entities
from ..money import fmt, from_minor
from . import bank_rec, calendar, flux

TOLERANCE = Decimal("0.01")
MAX_RATE_AGE_DAYS = 5

GATE_LABELS = {
    "ledger_integrity": "Ledger integrity",
    "fx_rates": "Exchange rates",
    "translation": "Translation proof",
    "intercompany": "Intercompany agreement",
    "bank_reconciliation": "Bank reconciliations",
    "flux": "Flux explanations",
    "close_plan": "Close calendar",
    "consolidation_foots": "Consolidated statements foot",
}


@dataclass
class Gate:
    name: str
    status: str
    reason: str = ""
    detail: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return GATE_LABELS[self.name]


@dataclass
class Assessment:
    period: str
    status: str
    gates: list[Gate]
    bank: list[bank_rec.RecResult]
    flux_items: list[flux.FluxItem]
    plan: calendar.Plan | None
    plan_error: str


def _gate(name: str, problems: list[str], detail: dict | None = None) -> Gate:
    return Gate(name, "fail" if problems else "pass", "; ".join(problems), detail or {})


def ledger_integrity(conn: sqlite3.Connection) -> Gate:
    problems: list[str] = []
    bad = conn.execute(
        """
        SELECT journal_id, SUM(debit_minor) - SUM(credit_minor) AS diff
        FROM journal_line GROUP BY journal_id HAVING diff != 0 LIMIT 5
        """
    ).fetchall()
    for r in bad:
        problems.append(f"journal {r['journal_id']} is unbalanced by {from_minor(r['diff'])}")
    per_entity = {}
    for r in conn.execute(
        """
        SELECT j.entity_code AS entity, SUM(l.debit_minor) - SUM(l.credit_minor) AS diff
        FROM journal_line l JOIN journal j ON j.journal_id = l.journal_id
        GROUP BY j.entity_code
        """
    ):
        per_entity[r["entity"]] = str(from_minor(r["diff"]))
        if r["diff"] != 0:
            problems.append(f"entity {r['entity']} trial balance is out by {from_minor(r['diff'])}")
    return _gate("ledger_integrity", problems, {"entity_sums": per_entity})


def fx_gate(consolidator: Consolidator, period: str) -> Gate:
    problems: list[str] = []
    detail: dict = {}
    _, end = period_bounds(period)
    for entity in consolidator.data.entities:
        cur = entity.currency
        if cur == consolidator.presentation:
            continue
        try:
            closing = consolidator.fx.closing(cur, consolidator.presentation, end)
            average = consolidator.fx.average(cur, consolidator.presentation, period)
        except RateError as exc:
            problems.append(f"{cur}: {exc}")
            continue
        detail[cur] = {
            "closing_rate": str(closing.rate),
            "closing_rate_date": closing.rate_date.isoformat(),
            "closing_age_days": closing.age_days,
            "average_rate": str(average.rate),
            "average_days_used": average.days_used,
        }
        if closing.age_days > MAX_RATE_AGE_DAYS:
            problems.append(
                f"{cur}: closing rate is {closing.age_days} days older than month end"
            )
    return _gate("fx_rates", problems, detail)


def translation_gate(consolidator: Consolidator, period: str) -> Gate:
    state = consolidator.state(period)
    problems: list[str] = []
    proofs = {}
    for code, te in state.entities.items():
        diff = translation_proof(te, consolidator.data.accounts, consolidator.data.moves[code])
        proofs[code] = str(diff)
        if abs(diff) > TOLERANCE:
            problems.append(f"{code}: reserve proof differs by {fmt(diff)}")
    if abs(state.raw_tb_sum) > TOLERANCE:
        problems.append(f"consolidated trial balance is out by {fmt(state.raw_tb_sum)}")
    return _gate("translation", problems, {"reserve_proof_difference": proofs})


def intercompany_gate(consolidator: Consolidator, period: str) -> Gate:
    state = consolidator.state(period)
    problems = []
    pairs = []
    for p in state.pairs:
        pairs.append(
            {
                "seller": p.seller,
                "buyer": p.buyer,
                "receivable_eur": str(p.receivable_eur),
                "payable_eur": str(p.payable_eur),
                "bs_difference_eur": str(p.bs_diff_eur),
                "revenue_eur_ytd": str(p.revenue_eur_ytd),
                "cogs_eur_ytd": str(p.cogs_eur_ytd),
                "pl_difference_eur": str(p.pl_diff_eur),
                "matches": p.matches,
                "suggested_journal": p.suggestion,
            }
        )
        if not p.matches:
            problems.append(f"{p.seller} -> {p.buyer} differs by EUR {fmt(p.bs_diff_eur)}")
    return _gate("intercompany", problems, {"pairs": pairs})


def bank_gate(results: list[bank_rec.RecResult]) -> Gate:
    problems = []
    for r in results:
        if r.passes:
            continue
        if not r.segregation_ok and r.status in {"reconciled", "timing_only"}:
            problems.append(f"{r.entity}: preparer and reviewer must be two different people")
        else:
            problems.append(f"{r.entity}: {r.status.replace('_', ' ')}")
    return _gate("bank_reconciliation", problems)


def flux_gate(items: list[flux.FluxItem]) -> Gate:
    bad = [i for i in items if i.material and not i.ok]
    problems = [f"{i.key} ({'; '.join(i.problems)})" for i in bad]
    detail = {"material": sum(1 for i in items if i.material), "unexplained": len(bad)}
    reason = []
    if bad:
        reason.append(f"{len(bad)} material line(s) without a valid explanation")
    return Gate("flux", "fail" if bad else "pass", "; ".join(reason + problems[:3]), detail)


def plan_gate(plan: calendar.Plan | None, error: str) -> Gate:
    if plan is None:
        return _gate("close_plan", [error or "no close plan"])
    return _gate(
        "close_plan",
        plan.problems,
        {"finish_workday": plan.finish, "target_workday": plan.target_workday,
         "critical_path": plan.critical_path},
    )


def foots_gate(consolidator: Consolidator, period: str) -> Gate:
    bs = reports.balance_sheet(consolidator, period)
    problems = []
    if bs["check"] != 0:
        problems.append(f"assets differ from equity and liabilities by {fmt(bs['check'])}")
    state = consolidator.state(period)
    if state.tb_sum() != 0:
        problems.append(f"rounded trial balance is out by {fmt(state.tb_sum())}")
    return _gate("consolidation_foots", problems, {"balance_sheet_check": str(bs["check"])})


def load_explanations(conn: sqlite3.Connection, period: str) -> dict[str, dict[str, str]]:
    rows = conn.execute("SELECT * FROM explanation WHERE period = ?", (period,))
    return {
        r["line_key"]: {"text": r["text"], "author": r["author"], "reviewer": r["reviewer"]}
        for r in rows
    }


def assess(
    conn: sqlite3.Connection, settings: Settings, consolidator: Consolidator, period: str
) -> Assessment:
    entities = load_entities(conn)
    bank_results = [bank_rec.reconcile(conn, e.code, period, settings.stale_days) for e in entities]
    items = flux.review(
        reports.flux_lines(consolidator, period),
        flux.FluxPolicy(settings.flux_pct, settings.flux_abs),
        load_explanations(conn, period),
    )
    plan_path = Path(settings.data_dir) / "close_plan.json"
    try:
        plan = calendar.load_plan(plan_path)
        plan_error = ""
    except calendar.PlanError as exc:
        plan, plan_error = None, str(exc)

    gates = [
        ledger_integrity(conn),
        fx_gate(consolidator, period),
        translation_gate(consolidator, period),
        intercompany_gate(consolidator, period),
        bank_gate(bank_results),
        flux_gate(items),
        plan_gate(plan, plan_error),
        foots_gate(consolidator, period),
    ]
    status = "ready" if all(g.status == "pass" for g in gates) else "blocked"
    return Assessment(period, status, gates, bank_results, items, plan, plan_error)
