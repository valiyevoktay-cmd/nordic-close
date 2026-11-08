"""SQLite connection and schema handling."""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path


def connect(path: Path | str) -> sqlite3.Connection:
    """Open a connection with foreign keys on and rows addressable by column name."""
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)  # one connection per request
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    sql = resources.files("nordic_close").joinpath("schema.sql").read_text(encoding="utf-8")
    conn.executescript(sql)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def reset(conn: sqlite3.Connection) -> None:
    """Drop every table and recreate the schema. Used by `init --force` and by tests."""
    tables = [
        r["name"]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    conn.execute("PRAGMA foreign_keys = OFF")
    for name in tables:
        conn.execute(f'DROP TABLE IF EXISTS "{name}"')  # noqa: S608 - names come from sqlite_master
    conn.commit()
    init_schema(conn)
