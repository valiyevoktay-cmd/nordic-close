"""REST API and static web view."""

from __future__ import annotations

import secrets
import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import __version__, excel_pack, reports
from .config import Settings
from .ledger import load_entities
from .service import CloseService, NoDataError, PeriodError, jsonable

PERIOD_PATTERN = r"^\d{4}-(0[1-9]|1[0-2])$"
Period = Annotated[str, Query(pattern=PERIOD_PATTERN, description="YYYY-MM")]
WEB_DIR = Path(__file__).parent / "web"

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


class RunRequest(BaseModel):
    period: str = Field(pattern=PERIOD_PATTERN, examples=["2026-10"])


def get_conn(request: Request) -> Iterator[sqlite3.Connection]:
    """One connection per request, closed afterwards."""
    settings: Settings = request.app.state.settings
    service: CloseService = request.app.state.service
    if str(settings.db_path) != ":memory:" and not Path(settings.db_path).exists():
        raise HTTPException(status_code=503, detail="database not initialised, run: nordic-close init")
    conn = service.connect()
    try:
        yield conn
    finally:
        conn.close()


Conn = Annotated[sqlite3.Connection, Depends(get_conn)]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    service = CloseService(settings)
    app = FastAPI(
        title="Nordic close", version=__version__,
        docs_url="/api/docs", openapi_url="/api/openapi.json",
    )
    app.state.service = service
    app.state.settings = settings

    @app.middleware("http")
    async def add_security_headers(request: Request, call_next):
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response

    @app.exception_handler(NoDataError)
    async def _no_data(_: Request, exc: NoDataError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(PeriodError)
    async def _bad_period(_: Request, exc: PeriodError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=422)

    def require_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        if settings.api_key and not (
            x_api_key and secrets.compare_digest(x_api_key, settings.api_key)
        ):
            raise HTTPException(status_code=401, detail="API key required")

    @app.get("/api/health")
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    api = APIRouter(prefix="/api", dependencies=[Depends(require_key)])

    @api.get("/periods")
    def periods(conn: Conn) -> list[dict]:
        return service.periods(conn)

    @api.get("/entities")
    def entities(conn: Conn) -> list[dict]:
        return [
            {"code": e.code, "name": e.name, "currency": e.currency, "country": e.country,
             "is_parent": e.is_parent}
            for e in load_entities(conn)
        ]

    @api.get("/reports/income-statement")
    def income_statement(conn: Conn, period: Period) -> dict:
        c = service.require(conn, period)
        return {"period": period, "currency": c.presentation,
                "rows": jsonable(reports.income_statement(c, period))}

    @api.get("/reports/balance-sheet")
    def balance_sheet(conn: Conn, period: Period) -> dict:
        c = service.require(conn, period)
        return {"period": period, "currency": c.presentation,
                **jsonable(reports.balance_sheet(c, period))}

    @api.get("/reports/kpis")
    def kpis(conn: Conn, period: Period) -> dict:
        c = service.require(conn, period)
        return {"period": period, "currency": c.presentation,
                "kpis": jsonable(reports.kpis(c, period))}

    @api.get("/reports/entities")
    def entity_report(conn: Conn, period: Period) -> dict:
        c = service.require(conn, period)
        return {"period": period, "currency": c.presentation,
                "entities": jsonable(reports.entity_view(c, period))}

    @api.get("/reports/variance")
    def variance(
        conn: Conn, period: Period,
        view: Annotated[str, Query(pattern="^(month|ytd)$")] = "month",
    ) -> dict:
        c = service.require(conn, period)
        return {"currency": c.presentation, **jsonable(reports.variance(c, period, view))}

    @api.get("/controls/summary")
    def summary(conn: Conn, period: Period) -> dict:
        a = service.assess(conn, period)
        return {
            "period": period,
            "status": a.status,
            "gates": [
                {"gate": g.name, "label": g.label, "status": g.status, "reason": g.reason}
                for g in a.gates
            ],
        }

    @api.get("/controls/intercompany")
    def intercompany(conn: Conn, period: Period) -> dict:
        a = service.assess(conn, period)
        c = service.require(conn, period)
        gate_ic = next(g for g in a.gates if g.name == "intercompany")
        elim = [
            {"seller": e.seller, "buyer": e.buyer, "balance_sheet": jsonable(e.bs_lines),
             "income_statement_ytd": jsonable(e.pl_lines)}
            for e in c.state(period).eliminations
        ]
        return {"period": period, "status": gate_ic.status, "pairs": gate_ic.detail["pairs"],
                "eliminations": elim, "currency": c.presentation}

    @api.get("/controls/reconciliations")
    def reconciliations(conn: Conn, period: Period) -> dict:
        a = service.assess(conn, period)

        def item(i) -> dict:
            return jsonable({"date": i.day, "ref": i.ref, "amount": i.amount,
                             "description": i.description, "age_days": i.age_days})

        return {
            "period": period,
            "reconciliations": [
                {
                    "entity": r.entity,
                    "status": r.status,
                    "passes": r.passes,
                    "ledger_closing": str(r.ledger_closing),
                    "bank_closing": str(r.bank_closing),
                    "proof_difference": str(r.proof_difference),
                    "matched": r.matched,
                    "preparer": r.preparer,
                    "reviewer": r.reviewer,
                    "outstanding": [item(i) for i in r.outstanding],
                    "unrecorded": [item(i) for i in r.unrecorded],
                    "transpositions": [
                        {"ledger": item(t.ledger), "bank": item(t.bank),
                         "difference": str(t.difference)} for t in r.transpositions
                    ],
                    "journals": r.journals,
                }
                for r in a.bank
            ],
        }

    @api.get("/controls/flux")
    def flux_review(conn: Conn, period: Period) -> dict:
        a = service.assess(conn, period)
        return {
            "period": period,
            "policy": {"percent": str(settings.flux_pct), "absolute": str(settings.flux_abs)},
            "items": [
                jsonable({
                    "key": i.key, "label": i.label, "current": i.current, "prior": i.prior,
                    "change": i.change, "change_pct": i.change_pct, "material": i.material,
                    "reasons": i.reasons, "explanation": i.explanation, "ok": i.ok,
                    "problems": i.problems,
                })
                for i in a.flux_items if i.material
            ],
            "lines_checked": len(a.flux_items),
        }

    @api.get("/controls/calendar")
    def calendar_view() -> dict:
        plan = service.plan()
        if plan is None:
            raise HTTPException(status_code=404, detail="no close plan")
        return jsonable({
            "period": plan.period,
            "target_workday": plan.target_workday,
            "finish_workday": plan.finish,
            "critical_path": plan.critical_path,
            "problems": plan.problems,
            "tasks": [
                {"id": t.id, "name": t.name, "owner": t.owner, "reviewer": t.reviewer,
                 "days": t.days, "after": t.after, "start": t.start, "finish": t.finish,
                 "slack": t.slack, "critical": t.critical, "key_control": t.key_control}
                for t in plan.tasks
            ],
        })

    @api.get("/fx")
    def fx(conn: Conn, period: Period) -> dict:
        c = service.require(conn, period)
        return {
            "period": period,
            "presentation_currency": c.presentation,
            "rates": [
                jsonable({
                    "entity": code, "currency": te.rates.currency,
                    "closing": te.rates.closing, "closing_date": te.rates.closing_date,
                    "average": te.rates.average, "average_days": te.rates.average_days,
                    "opening": te.rates.opening, "opening_date": te.rates.opening_date,
                })
                for code, te in c.state(period).entities.items()
            ],
        }

    @api.post("/close/runs", status_code=201)
    def run_close(conn: Conn, body: RunRequest) -> dict:
        return service.run_close(conn, body.period, actor="api")

    @api.get("/close/runs")
    def list_runs(
        conn: Conn, period: Annotated[str | None, Query(pattern=PERIOD_PATTERN)] = None,
    ) -> list[dict]:
        return service.runs(conn, period)

    @api.get("/export/pack.xlsx")
    def export_pack(conn: Conn, period: Period) -> Response:
        c = service.require(conn, period)
        a = service.assess(conn, period)
        data = excel_pack.build_pack(c, a, period)
        service.audit(conn, "export", period, "api", f"{len(data)} bytes")
        return Response(
            content=data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="reporting-pack-{period}.xlsx"'},
        )

    app.include_router(api)
    if WEB_DIR.exists():
        app.mount("/", StaticFiles(directory=WEB_DIR, html=True), name="web")
    return app
