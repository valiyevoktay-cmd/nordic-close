"""Download ECB euro reference rates. This is the only module that uses the network.

The ECB publishes rates as units of foreign currency per 1 EUR on TARGET business days.
"""

from __future__ import annotations

import csv
import io
import urllib.parse
import urllib.request
from pathlib import Path

BASE = "https://data-api.ecb.europa.eu/service/data/EXR"
DEFAULT_CURRENCIES = ("SEK", "NOK", "DKK")


def fetch_series(currency: str, start: str, end: str) -> list[tuple[str, str]]:
    query = urllib.parse.urlencode({"startPeriod": start, "endPeriod": end, "format": "csvdata"})
    url = f"{BASE}/D.{currency}.EUR.SP00.A?{query}"
    if not url.startswith("https://"):
        raise ValueError("refusing to fetch over a non-HTTPS URL")
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed HTTPS host
        text = response.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(text))
    return [(row["TIME_PERIOD"], row["OBS_VALUE"]) for row in reader if row["OBS_VALUE"]]


def write_rates_csv(
    path: Path, start: str, end: str, currencies: tuple[str, ...] = DEFAULT_CURRENCIES
) -> int:
    rows: list[tuple[str, str, str]] = []
    for currency in currencies:
        rows.extend((d, currency, v) for d, v in fetch_series(currency, start, end))
    rows.sort()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["rate_date", "currency", "per_eur", "source"])
        writer.writerows((d, c, v, "ECB") for d, c, v in rows)
    return len(rows)
