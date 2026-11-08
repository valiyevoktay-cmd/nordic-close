"""Settings read from environment variables. See .env.example for the list."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

CURRENCIES = ("SEK", "NOK", "DKK", "EUR")


@dataclass(frozen=True)
class Settings:
    db_path: Path
    data_dir: Path
    presentation_currency: str
    api_key: str
    flux_pct: Decimal
    flux_abs: Decimal
    ic_tolerance_eur: Decimal
    stale_days: int
    target_wd: int

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        e = os.environ if env is None else env
        currency = e.get("NORDIC_CLOSE_PRESENTATION_CURRENCY", "SEK").upper()
        if currency not in CURRENCIES:
            raise ValueError(f"presentation currency must be one of {CURRENCIES}, got {currency!r}")
        return cls(
            db_path=Path(e.get("NORDIC_CLOSE_DB", "data/nordic_close.db")),
            data_dir=Path(e.get("NORDIC_CLOSE_DATA", "data/sample")),
            presentation_currency=currency,
            api_key=e.get("NORDIC_CLOSE_API_KEY", ""),
            flux_pct=Decimal(e.get("NORDIC_CLOSE_FLUX_PCT", "10")),
            flux_abs=Decimal(e.get("NORDIC_CLOSE_FLUX_ABS", "1000000")),
            ic_tolerance_eur=Decimal(e.get("NORDIC_CLOSE_IC_TOLERANCE_EUR", "1.00")),
            stale_days=int(e.get("NORDIC_CLOSE_STALE_DAYS", "90")),
            target_wd=int(e.get("NORDIC_CLOSE_TARGET_WD", "5")),
        )
