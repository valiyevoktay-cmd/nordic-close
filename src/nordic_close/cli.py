"""Command line interface."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from contextlib import closing
from datetime import date
from pathlib import Path

from . import __version__, db, ecb, excel_pack, ingest
from .config import Settings
from .service import CloseService, NoDataError, PeriodError, jsonable


def _settings(args: argparse.Namespace) -> Settings:
    return Settings.from_env()


def cmd_init(args: argparse.Namespace) -> int:
    settings = _settings(args)
    with closing(db.connect(settings.db_path)) as conn:
        return _init(conn, args, settings)


def _init(conn: sqlite3.Connection, args: argparse.Namespace, settings: Settings) -> int:
    if args.force:
        db.reset(conn)
    else:
        db.init_schema(conn)
    fx_csv = Path(args.fx) if args.fx else None
    try:
        summary = ingest.load_dataset(conn, Path(args.data or settings.data_dir), fx_csv)
    except ingest.IngestError as exc:
        print(f"load failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"loaded {summary.entities} entities, {summary.journals} journals "
        f"({summary.lines} lines), {summary.fx_rates} rates into {settings.db_path}"
    )
    return 0


def cmd_close(args: argparse.Namespace) -> int:
    settings = _settings(args)
    service = CloseService(settings)
    try:
        with closing(service.connect()) as conn:
            result = service.run_close(conn, args.period, actor="cli")
            if args.lock and result["status"] == "ready":
                service.lock(conn, args.period, actor="cli")
    except (PeriodError, NoDataError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    if args.json:
        print(json.dumps(jsonable(result), indent=2))
    else:
        print(f"{result['period']}: {result['status'].upper()}  (run {result['run_id']})")
        for g in result["gates"]:
            mark = "ok  " if g["status"] == "pass" else "FAIL"
            print(f"  [{mark}] {g['label']}" + (f": {g['reason']}" if g["reason"] else ""))
    return 0 if result["status"] == "ready" else 4


def cmd_export(args: argparse.Namespace) -> int:
    settings = _settings(args)
    service = CloseService(settings)
    out = Path(args.out or f"reporting-pack-{args.period}.xlsx")
    try:
        with closing(service.connect()) as conn:
            c = service.require(conn, args.period)
            data = excel_pack.build_pack(c, service.assess(conn, args.period), args.period)
            out.write_bytes(data)
            service.audit(conn, "export", args.period, "cli", str(out))
    except (PeriodError, NoDataError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    print(f"wrote {out} ({len(data):,} bytes)")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .api import create_app

    uvicorn.run(create_app(_settings(args)), host=args.host, port=args.port, log_level="info")
    return 0


def cmd_fx_update(args: argparse.Namespace) -> int:
    out = Path(args.out)
    count = ecb.write_rates_csv(out, args.start, args.end)
    print(f"wrote {count} rates to {out}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nordic-close", description=__doc__)
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init", help="create the database and load the input files")
    p.add_argument("--data", help="folder with the input CSV files")
    p.add_argument("--fx", help="ECB rates CSV (default: ../fx/ecb_eur_rates.csv next to --data)")
    p.add_argument("--force", action="store_true", help="drop all tables first, history included")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("close", help="run the close for a period")
    p.add_argument("--period", required=True, help="YYYY-MM")
    p.add_argument("--lock", action="store_true", help="lock the period if the close is ready")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_close)

    p = sub.add_parser("export", help="write the Excel reporting pack")
    p.add_argument("--period", required=True)
    p.add_argument("--out")
    p.set_defaults(func=cmd_export)

    p = sub.add_parser("serve", help="start the API and web view")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.set_defaults(func=cmd_serve)

    p = sub.add_parser("fx-update", help="download ECB rates (uses the network)")
    p.add_argument("--start", default="2025-12-01")
    p.add_argument("--end", default=date.today().isoformat())
    p.add_argument("--out", default="data/fx/ecb_eur_rates.csv")
    p.set_defaults(func=cmd_fx_update)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
