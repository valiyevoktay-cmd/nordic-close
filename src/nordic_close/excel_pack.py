"""Excel reporting pack. Subtotals are live formulas so a reader can follow the arithmetic."""

from __future__ import annotations

import io
from decimal import Decimal

import xlsxwriter

from . import reports
from .consolidate import Consolidator
from .controls.gate import Assessment
from .money import q2

NUM = '#,##0;[Red]-#,##0;"-"'
PCT = "0.0%"


def _f(value: Decimal | None) -> float | None:
    return None if value is None else float(q2(value))


def build_pack(consolidator: Consolidator, assessment: Assessment, period: str) -> bytes:
    buffer = io.BytesIO()
    # Text from the ledger must never be read as a formula or link.
    options = {"in_memory": True, "strings_to_formulas": False, "strings_to_urls": False}
    wb = xlsxwriter.Workbook(buffer, options)
    wb.set_properties({"title": f"Reporting pack {period}", "author": "nordic-close"})
    cur = consolidator.presentation

    title = wb.add_format({"bold": True, "font_size": 14})
    head = wb.add_format({"bold": True, "bg_color": "#1F3A5F", "font_color": "#FFFFFF", "border": 1})
    label = wb.add_format({})
    bold = wb.add_format({"bold": True, "top": 1})
    num = wb.add_format({"num_format": NUM})
    num_bold = wb.add_format({"num_format": NUM, "bold": True, "top": 1})
    pct = wb.add_format({"num_format": PCT})
    good = wb.add_format({"bg_color": "#D9EAD3"})
    bad = wb.add_format({"bg_color": "#F4CCCC"})
    wrap = wb.add_format({"text_wrap": True, "valign": "top"})

    # ---- Summary
    ws = wb.add_worksheet("Summary")
    ws.write(0, 0, f"Reporting pack {period} ({cur})", title)
    ws.write(2, 0, "Close status", bold)
    ws.write(2, 1, assessment.status.upper(), good if assessment.status == "ready" else bad)
    ws.write(4, 0, "Control", head)
    ws.write(4, 1, "Result", head)
    ws.write(4, 2, "Reason", head)
    for i, g in enumerate(assessment.gates, start=5):
        ws.write(i, 0, g.label, label)
        ws.write(i, 1, g.status, good if g.status == "pass" else bad)
        ws.write(i, 2, g.reason, wrap)
    start = 5 + len(assessment.gates) + 2
    ws.write(start, 0, "Key figures", head)
    ws.write(start, 1, "Value", head)
    for i, k in enumerate(reports.kpis(consolidator, period), start=start + 1):
        ws.write(i, 0, k["label"], label)
        if k["value"] is None:
            ws.write(i, 1, "n/a")
        elif k["unit"] == "percent":
            ws.write_number(i, 1, float(k["value"]), pct)
        elif k["unit"] in {"times", "days"}:
            ws.write_number(i, 1, float(k["value"]), wb.add_format({"num_format": "0.00"}))
        else:
            ws.write_number(i, 1, _f(k["value"]), num)
    ws.set_column(0, 0, 38)
    ws.set_column(1, 1, 16)
    ws.set_column(2, 2, 90)

    # ---- Income statement
    ws = wb.add_worksheet("Income statement")
    ws.write(0, 0, f"Consolidated income statement ({cur})", title)
    for col, text in enumerate(["", "Month", "Prior month", "Year to date"]):
        ws.write(2, col, text, head)
    rows = reports.income_statement(consolidator, period)
    pos = {r.key: 3 + i for i, r in enumerate(rows)}  # zero-based row index

    def cell(col: str, key: str) -> str:
        return f"{col}{pos[key] + 1}"

    formulas = {
        "gross_profit": ["revenue", "cogs"],
        "ebitda": ["gross_profit", "personnel", "other_opex"],
        "ebit": ["ebitda", "depreciation"],
        "pbt": ["ebit", "finance_income", "finance_costs", "fx_differences", "ic_fx_residual",
                "ic_pl_suspense"],
        "net_income": ["pbt", "income_tax"],
    }
    for r in rows:
        row = pos[r.key]
        ws.write(row, 0, r.label, bold if r.subtotal else label)
        for col, name in ((1, "month"), (2, "prior_month"), (3, "ytd")):
            value = r.values[name]
            letter = "BCD"[col - 1]
            if value is None:
                ws.write(row, col, "n/a", num_bold if r.subtotal else num)
            elif r.key in formulas:
                expr = "=" + "+".join(cell(letter, k) for k in formulas[r.key])
                ws.write_formula(row, col, expr, num_bold, _f(value))
            else:
                ws.write_number(row, col, _f(value), num)
    ws.set_column(0, 0, 40)
    ws.set_column(1, 3, 18)
    ws.freeze_panes(3, 1)

    # ---- Balance sheet
    ws = wb.add_worksheet("Balance sheet")
    ws.write(0, 0, f"Consolidated balance sheet ({cur}) at {period}", title)
    for col, text in enumerate(["", "Closing", "Prior closing"]):
        ws.write(2, col, text, head)
    bs = reports.balance_sheet(consolidator, period)
    row = 3
    positions: dict[str, int] = {}
    sections = [
        ("assets", "total_assets", "Assets"),
        ("equity", "total_equity", "Equity"),
        ("liabilities", "total_liabilities", "Liabilities"),
    ]
    for section, total_key, heading in sections:
        ws.write(row, 0, heading, bold)
        row += 1
        first = row
        for r in bs[section]:
            ws.write(row, 0, r.label, label)
            ws.write_number(row, 1, _f(r.values["closing"]), num)
            if r.values["prior_closing"] is not None:
                ws.write_number(row, 2, _f(r.values["prior_closing"]), num)
            row += 1
        total = bs[total_key]
        ws.write(row, 0, total.label, bold)
        for col, name, letter in ((1, "closing", "B"), (2, "prior_closing", "C")):
            if total.values[name] is None:
                continue
            ws.write_formula(row, col, f"=SUM({letter}{first + 1}:{letter}{row})", num_bold,
                             _f(total.values[name]))
        positions[total_key] = row
        row += 2
    ws.write(row, 0, "Check: assets less equity and liabilities", label)
    ws.write_formula(
        row, 1,
        f"=B{positions['total_assets'] + 1}-B{positions['total_equity'] + 1}"
        f"-B{positions['total_liabilities'] + 1}",
        num, _f(bs["check"]),
    )
    ws.set_column(0, 0, 44)
    ws.set_column(1, 2, 18)

    # ---- Entities
    ws = wb.add_worksheet("Entities")
    ws.write(0, 0, f"Contribution by entity ({cur})", title)
    heads = ["Entity", "Currency", "Closing rate", "Average rate", "Revenue (local)",
             f"Revenue ({cur})", "Net income (local)", f"Net income ({cur})",
             f"Net income YTD ({cur})", f"Net assets ({cur})", "Translation reserve"]
    for col, text in enumerate(heads):
        ws.write(2, col, text, head)
    for i, e in enumerate(reports.entity_view(consolidator, period), start=3):
        ws.write(i, 0, f"{e['entity']} {e['name']}")
        ws.write(i, 1, e["currency"])
        ws.write_number(i, 2, float(e["closing_rate"]), wb.add_format({"num_format": "0.0000"}))
        ws.write_number(i, 3, float(e["average_rate"]), wb.add_format({"num_format": "0.0000"}))
        for col, key in enumerate(["revenue_local", "revenue_group", "net_income_local",
                                   "net_income_group", "net_income_ytd_group",
                                   "net_assets_group", "translation_reserve"], start=4):
            ws.write_number(i, col, _f(e[key]), num)
    ws.set_column(0, 0, 24)
    ws.set_column(1, 10, 18)

    # ---- Intercompany
    ws = wb.add_worksheet("Intercompany")
    ws.write(0, 0, "Intercompany comparison (EUR)", title)
    for col, text in enumerate(["Seller", "Buyer", "Receivable", "Payable", "Balance difference",
                                "Revenue YTD", "Cost YTD", "P&L difference", "Result"]):
        ws.write(2, col, text, head)
    for i, p in enumerate(consolidator.state(period).pairs, start=3):
        ws.write(i, 0, p.seller)
        ws.write(i, 1, p.buyer)
        for col, v in enumerate([p.receivable_eur, p.payable_eur, p.bs_diff_eur,
                                 p.revenue_eur_ytd, p.cogs_eur_ytd, p.pl_diff_eur], start=2):
            ws.write_number(i, col, _f(v), num)
        ws.write(i, 8, "match" if p.matches else "difference", good if p.matches else bad)
    ws.set_column(0, 8, 18)

    # ---- Reconciliations
    ws = wb.add_worksheet("Reconciliations")
    ws.write(0, 0, f"Bank reconciliations at {period}", title)
    for col, text in enumerate(["Entity", "Status", "Ledger balance", "Bank balance",
                                "Proof difference", "Outstanding items", "Unrecorded items",
                                "Transpositions", "Preparer", "Reviewer"]):
        ws.write(2, col, text, head)
    for i, r in enumerate(assessment.bank, start=3):
        ws.write(i, 0, r.entity)
        ws.write(i, 1, r.status.replace("_", " "), good if r.passes else bad)
        ws.write_number(i, 2, _f(r.ledger_closing), num)
        ws.write_number(i, 3, _f(r.bank_closing), num)
        ws.write_number(i, 4, _f(r.proof_difference), num)
        ws.write_number(i, 5, len(r.outstanding))
        ws.write_number(i, 6, len(r.unrecorded))
        ws.write_number(i, 7, len(r.transpositions))
        ws.write(i, 8, r.preparer)
        ws.write(i, 9, r.reviewer)
    ws.set_column(0, 9, 18)

    # ---- Variance
    ws = wb.add_worksheet("Variance")
    ws.write(0, 0, f"Actual against budget, month ({cur})", title)
    v = reports.variance(consolidator, period, "month")
    for col, text in enumerate(["Line", "Actual", "Budget", "Variance", "Operating effect",
                                "FX effect"]):
        ws.write(2, col, text, head)
    for i, r in enumerate(v["rows"], start=3):
        ws.write(i, 0, r["label"])
        ws.write_number(i, 1, _f(r["actual"]), num)
        ws.write_number(i, 2, _f(r["budget"]), num)
        ws.write_formula(i, 3, f"=B{i + 1}-C{i + 1}", num, _f(r["variance"]))
        ws.write_number(i, 4, _f(r["operating_effect"]), num)
        ws.write_formula(i, 5, f"=D{i + 1}-E{i + 1}", num, _f(r["fx_effect"]))
    ws.set_column(0, 0, 32)
    ws.set_column(1, 5, 18)
    ws.write(len(v["rows"]) + 4, 0,
             "Budget is translated at the rate on the opening date. Positive variance is favourable.")

    # ---- FX
    ws = wb.add_worksheet("FX rates")
    ws.write(0, 0, f"Rates used, local currency to {cur}", title)
    for col, text in enumerate(["Entity", "Currency", "Closing rate", "Closing rate date",
                                "Average rate", "Days in average", "Opening rate", "Opening date"]):
        ws.write(2, col, text, head)
    for i, e in enumerate(consolidator.state(period).entities.values(), start=3):
        r = e.rates
        ws.write(i, 0, e.entity.code)
        ws.write(i, 1, r.currency)
        ws.write_number(i, 2, float(r.closing), wb.add_format({"num_format": "0.000000"}))
        ws.write(i, 3, r.closing_date.isoformat())
        ws.write_number(i, 4, float(r.average), wb.add_format({"num_format": "0.000000"}))
        ws.write_number(i, 5, r.average_days)
        ws.write_number(i, 6, float(r.opening), wb.add_format({"num_format": "0.000000"}))
        ws.write(i, 7, r.opening_date.isoformat())
    ws.set_column(0, 7, 18)
    ws.write(len(consolidator.state(period).entities) + 4, 0,
             "Source: ECB euro reference rates. Cross rates are derived through EUR.")

    wb.close()
    return buffer.getvalue()
