"""Write the sample flux explanations (data/sample/explanations.csv).

The explanations are authored from the figures the engine computes and from the drivers that
are built into the synthetic ledgers (seasonality, the one-off Norwegian order, quarter-end tax
payments, the late-booked Danish invoice). Run after generate_sample_data.py.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path

from generate_sample_data import SEASON
from nordic_close import db, ingest, reports
from nordic_close.consolidate import Consolidator, LedgerData
from nordic_close.controls import flux
from nordic_close.fx import FxTable

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sample" / "explanations.csv"
AUTHOR, REVIEWER = "Nina Berg", "Karin Roos"


def m(x: Decimal) -> str:
    """Amount in SEK millions with one decimal."""
    return f"{x / 1_000_000:,.1f}m"


def by_entity(c: Consolidator, period: str, prev: str, getter) -> str:
    cur, old = c.state(period), c.state(prev)
    parts = []
    for code in cur.entities:
        delta = getter(cur.entities[code]) - getter(old.entities[code])
        parts.append(f"{code} {'+' if delta >= 0 else '-'}{m(abs(delta))}")
    return ", ".join(parts)


def season(month: int) -> str:
    return f"{SEASON[month - 1]:.2f}"


def key_is_cost(key: str) -> bool:
    return key in {"is.cogs", "is.personnel", "is.other_opex", "is.income_tax"}


def explain(c: Consolidator, period: str, item: flux.FluxItem) -> str:
    prev = f"{period[:4]}-{int(period[5:]) - 1:02d}"
    month = int(period[5:])
    cur_i, old_i = season(month), season(month - 1)
    # Income statement costs are negative numbers, so a more negative value is a higher cost.
    is_cost = key_is_cost(item.key)
    sign = "rose" if (item.change >= 0) != is_cost else "fell"
    amount = f"SEK {m(abs(item.change))}"
    key = item.key

    if key in {"is.revenue", "is.cogs", "is.personnel", "is.other_opex"}:
        text = (
            f"{item.label} {sign} by {amount}: invoicing and purchasing follow the seasonal "
            f"volume profile, index {cur_i} in {period} against {old_i} in {prev}."
        )
        if period == "2026-10" and key in {"is.revenue", "is.cogs"}:
            text += (
                " Includes the one-off Norwegian order ORDER-NO-1042 of EUR 900,000 booked on "
                "15 October, with cost of goods at 60% of its value."
            )
        return text
    if key == "bs.trade_receivables":
        text = (
            f"Receivables {sign} by {amount}: customers pay 97% of the prior month's balance, so "
            f"the balance follows invoicing, volume index {cur_i} against {old_i}."
        )
        if period == "2026-10":
            text += " Includes ORDER-NO-1042 (EUR 900,000), not yet collected at 31 October."
        return text
    if key == "bs.trade_payables":
        return (
            f"Payables {sign} by {amount}: suppliers are paid 97% of the prior month's balance, "
            f"so the balance follows purchases, volume index {cur_i} against {old_i}."
        )
    if key == "bs.tax_payable":
        detail = by_entity(c, period, prev, lambda te: te.bs.get("2150", Decimal(0)) * -1)
        if month in (3, 6, 9):
            quarter_end = {3: "March", 6: "June", 9: "September"}[month]
            why = "entities paid the tax accrued for the quarter on 20 " + quarter_end
        else:
            why = "one more month of tax was accrued at the statutory rates of 20.0% to 22.0%"
        return f"Tax payable {sign} by {amount}: {why}. By entity: {detail}."
    if key == "bs.translation_reserve":
        cur, old = c.state(period), c.state(prev)
        moves = []
        for code, te in cur.entities.items():
            if te.entity.currency == c.presentation:
                continue
            a, b = old.entities[code].rates.closing, te.rates.closing
            moves.append(f"{te.entity.currency}/{c.presentation} {a:.4f} to {b:.4f}")
        detail = by_entity(c, period, prev, lambda te: -te.cta)
        return (
            f"Translation reserve {sign} by {amount} on closing rate moves ({'; '.join(moves)}). "
            f"By entity: {detail}."
        )
    if key == "bs.cash":
        detail = by_entity(c, period, prev, lambda te: te.bs.get("1010", Decimal(0)))
        why = (
            "profit after tax and collections exceeded supplier, payroll and tax payments"
            if item.change >= 0
            else "supplier, payroll, tax and capex payments exceeded collections and profit after tax"
        )
        return f"Cash {sign} by {amount}: {why}. By entity: {detail}."
    if key == "is.income_tax":
        return (
            f"Income tax {sign} by {amount}: tax is accrued at the statutory rates of 20.0% to "
            f"22.0% on profit before tax, which moved with the Norwegian order and volume index {cur_i}."
        )
    if key in {"is.ic_pl_suspense", "bs.ic_suspense"}:
        return (
            "Difference of EUR 120,000 on the SE to DK pair: invoice ICINV-SE-DK-2026-10-29 was "
            "booked by SE on 29 October and not yet by DK. DK books it in November."
        )
    return ""


def main() -> int:
    conn = db.connect(":memory:")
    db.init_schema(conn)
    ingest.load_dataset(conn, ROOT / "data" / "sample")
    c = Consolidator(LedgerData.load(conn), FxTable.from_db(conn), "SEK")
    policy = flux.FluxPolicy(Decimal(10), Decimal(1000000))
    rows = []
    for period in c.data.periods[1:]:
        for item in flux.review(reports.flux_lines(c, period), policy, {}):
            if item.material:
                text = explain(c, period, item)
                if not text:
                    raise SystemExit(f"no explanation template for {period} {item.key}")
                rows.append(dict(period=period, line_key=item.key, text=text,
                                 author=AUTHOR, reviewer=REVIEWER))
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["period", "line_key", "text", "author", "reviewer"],
                                lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} explanations")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
