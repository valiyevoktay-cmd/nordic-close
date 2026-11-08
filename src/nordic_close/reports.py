"""Reporting views built on the consolidated state.

Sign conventions in the reports: the income statement shows what each line does to profit, so
income is positive and costs are negative. The balance sheet shows assets as positive and
liabilities and equity as positive.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .consolidate import Consolidator
from .fx import period_bounds
from .ledger import previous_period
from .money import mul_rate, q2
from .translate import year_months

ZERO = Decimal(0)

IS_LINES = [
    ("revenue", "Revenue"),
    ("cogs", "Cost of goods sold"),
    ("gross_profit", "Gross profit"),
    ("personnel", "Personnel costs"),
    ("other_opex", "Other operating expenses"),
    ("ebitda", "EBITDA"),
    ("depreciation", "Depreciation"),
    ("ebit", "Operating profit (EBIT)"),
    ("finance_income", "Finance income"),
    ("finance_costs", "Finance costs"),
    ("fx_differences", "Foreign exchange differences"),
    ("ic_fx_residual", "Intercompany FX residual"),
    ("ic_pl_suspense", "Unreconciled intercompany difference"),
    ("pbt", "Profit before tax"),
    ("income_tax", "Income tax"),
    ("net_income", "Net income"),
]
IS_SUBTOTALS = {"gross_profit", "ebitda", "ebit", "pbt", "net_income"}

BS_ASSETS = [
    ("cash", "Cash and bank"),
    ("trade_receivables", "Trade receivables"),
    ("ic_receivables", "Intercompany receivables"),
    ("ic_suspense", "Unreconciled intercompany difference"),
    ("inventory", "Inventory"),
    ("other_current_assets", "Prepaid expenses and other current assets"),
    ("ppe", "Property, plant and equipment, net"),
]
BS_EQUITY = [
    ("share_capital", "Share capital"),
    ("retained_earnings", "Retained earnings brought forward"),
    ("translation_reserve", "Translation reserve"),
    ("profit_for_period", "Profit for the year to date"),
]
BS_LIABILITIES = [
    ("trade_payables", "Trade payables"),
    ("ic_payables", "Intercompany payables"),
    ("accrued_expenses", "Accrued expenses"),
    ("tax_payable", "Income tax payable"),
    ("borrowings_st", "Short-term borrowings"),
    ("borrowings_lt", "Long-term borrowings"),
]

VARIANCE_ACCOUNTS = [
    ("revenue", "Revenue", "4010"),
    ("cogs", "Cost of goods sold", "5010"),
    ("personnel", "Personnel costs", "6010"),
    ("other_opex", "Other operating expenses", "6020"),
    ("depreciation", "Depreciation", "6030"),
    ("finance_costs", "Finance costs", "7020"),
]


@dataclass
class Row:
    key: str
    label: str
    values: dict[str, Decimal | None]
    subtotal: bool = False


def _by_line(balances: dict[str, Decimal], consolidator: Consolidator) -> dict[str, Decimal]:
    out: dict[str, Decimal] = {}
    for code, amount in balances.items():
        line = consolidator.data.accounts[code].report_line
        out[line] = out.get(line, ZERO) + amount
    return out


def _is_column(consolidator: Consolidator, pl: dict[str, Decimal]) -> dict[str, Decimal]:
    """Turn debit-positive P&L balances into income statement lines (income +, cost -)."""
    raw = {k: -v for k, v in _by_line(pl, consolidator).items()}
    g = raw.get
    out = {k: g(k, ZERO) for k, _ in IS_LINES}
    out["revenue"] = g("revenue", ZERO) + g("ic_revenue", ZERO)
    out["cogs"] = g("cogs", ZERO) + g("ic_cogs", ZERO)
    out["gross_profit"] = out["revenue"] + out["cogs"]
    out["ebitda"] = out["gross_profit"] + out["personnel"] + out["other_opex"]
    out["ebit"] = out["ebitda"] + out["depreciation"]
    out["pbt"] = (
        out["ebit"]
        + out["finance_income"]
        + out["finance_costs"]
        + out["fx_differences"]
        + out["ic_fx_residual"]
        + out["ic_pl_suspense"]
    )
    out["net_income"] = out["pbt"] + out["income_tax"]
    return out


def _prior(consolidator: Consolidator, period: str) -> str | None:
    prev = previous_period(period)
    return prev if prev in consolidator.data.periods else None


def income_statement(consolidator: Consolidator, period: str) -> list[Row]:
    month = _is_column(consolidator, consolidator.pl_month(period))
    ytd = _is_column(consolidator, consolidator.state(period).pl_ytd)
    prev = _prior(consolidator, period)
    prior = _is_column(consolidator, consolidator.pl_month(prev)) if prev else None
    rows = []
    for key, label in IS_LINES:
        rows.append(
            Row(
                key,
                label,
                {
                    "month": month[key],
                    "prior_month": prior[key] if prior else None,
                    "ytd": ytd[key],
                },
                subtotal=key in IS_SUBTOTALS,
            )
        )
    return rows


def _bs_column(consolidator: Consolidator, period: str) -> dict[str, Decimal]:
    state = consolidator.state(period)
    raw = _by_line(state.bs, consolidator)
    ytd = _is_column(consolidator, state.pl_ytd)
    out = {k: raw.get(k, ZERO) for k, _ in BS_ASSETS}
    out.update({k: -raw.get(k, ZERO) for k, _ in BS_EQUITY + BS_LIABILITIES})
    out["profit_for_period"] = ytd["net_income"]
    out["total_assets"] = sum((out[k] for k, _ in BS_ASSETS), ZERO)
    out["total_equity"] = sum((out[k] for k, _ in BS_EQUITY), ZERO)
    out["total_liabilities"] = sum((out[k] for k, _ in BS_LIABILITIES), ZERO)
    out["total_equity_liabilities"] = out["total_equity"] + out["total_liabilities"]
    out["check"] = out["total_assets"] - out["total_equity_liabilities"]
    return out


def balance_sheet(consolidator: Consolidator, period: str) -> dict:
    current = _bs_column(consolidator, period)
    prev = _prior(consolidator, period)
    prior = _bs_column(consolidator, prev) if prev else None

    def rows(spec: list[tuple[str, str]]) -> list[Row]:
        return [
            Row(k, label, {"closing": current[k], "prior_closing": prior[k] if prior else None})
            for k, label in spec
        ]

    def total(key: str, label: str) -> Row:
        return Row(
            key,
            label,
            {"closing": current[key], "prior_closing": prior[key] if prior else None},
            subtotal=True,
        )

    return {
        "assets": rows(BS_ASSETS),
        "total_assets": total("total_assets", "Total assets"),
        "equity": rows(BS_EQUITY),
        "total_equity": total("total_equity", "Total equity"),
        "liabilities": rows(BS_LIABILITIES),
        "total_liabilities": total("total_liabilities", "Total liabilities"),
        "total_equity_liabilities": total("total_equity_liabilities", "Total equity and liabilities"),
        "check": current["check"],
    }


def flux_lines(consolidator: Consolidator, period: str) -> list[dict]:
    """Base lines (no subtotals) with current and prior values for the flux review."""
    prev = _prior(consolidator, period)
    if prev is None:
        return []
    cur_is = _is_column(consolidator, consolidator.pl_month(period))
    pri_is = _is_column(consolidator, consolidator.pl_month(prev))
    cur_bs = _bs_column(consolidator, period)
    pri_bs = _bs_column(consolidator, prev)
    lines = []
    for key, label in IS_LINES:
        if key in IS_SUBTOTALS:
            continue
        lines.append({"key": f"is.{key}", "label": label, "current": cur_is[key], "prior": pri_is[key]})
    for key, label in BS_ASSETS + BS_EQUITY + BS_LIABILITIES:
        if key == "profit_for_period":
            continue  # covered by the income statement lines
        lines.append({"key": f"bs.{key}", "label": label, "current": cur_bs[key], "prior": pri_bs[key]})
    return lines


def kpis(consolidator: Consolidator, period: str) -> list[dict]:
    ytd = _is_column(consolidator, consolidator.state(period).pl_ytd)
    month = _is_column(consolidator, consolidator.pl_month(period))
    bs = _bs_column(consolidator, period)
    start = date(int(period[:4]), 1, 1)
    _, end = period_bounds(period)
    days = (end - start).days + 1
    months = len(year_months(period))

    def ratio(a: Decimal, b: Decimal) -> Decimal | None:
        return None if b == 0 else a / b

    current_assets = (
        bs["cash"] + bs["trade_receivables"] + bs["inventory"]
        + bs["other_current_assets"] + bs["ic_suspense"]
    )
    current_liabilities = (
        bs["trade_payables"] + bs["accrued_expenses"] + bs["tax_payable"] + bs["borrowings_st"]
    )
    debt = bs["borrowings_lt"] + bs["borrowings_st"]
    annualised_ebitda = ytd["ebitda"] * 12 / months
    dso = ratio(bs["trade_receivables"] * days, ytd["revenue"])
    items = [
        ("revenue_month", "Revenue, month", month["revenue"], "amount"),
        ("ebitda_month", "EBITDA, month", month["ebitda"], "amount"),
        ("net_income_month", "Net income, month", month["net_income"], "amount"),
        ("revenue_ytd", "Revenue, year to date", ytd["revenue"], "amount"),
        ("ebitda_ytd", "EBITDA, year to date", ytd["ebitda"], "amount"),
        ("gross_margin", "Gross margin", ratio(ytd["gross_profit"], ytd["revenue"]), "percent"),
        ("ebitda_margin", "EBITDA margin", ratio(ytd["ebitda"], ytd["revenue"]), "percent"),
        ("net_margin", "Net margin", ratio(ytd["net_income"], ytd["revenue"]), "percent"),
        ("current_ratio", "Current ratio", ratio(current_assets, current_liabilities), "times"),
        ("net_debt", "Net debt", debt - bs["cash"], "amount"),
        (
            "net_debt_ebitda",
            "Net debt / annualised EBITDA",
            ratio(debt - bs["cash"], annualised_ebitda),
            "times",
        ),
        ("equity_ratio", "Equity ratio", ratio(bs["total_equity"], bs["total_assets"]), "percent"),
        ("dso", "Days sales outstanding", dso, "days"),
    ]
    return [{"key": k, "label": label, "value": v, "unit": u} for k, label, v, u in items]


def entity_view(consolidator: Consolidator, period: str) -> list[dict]:
    state = consolidator.state(period)
    out = []
    for code, te in state.entities.items():
        loc_rev = -(te.local_pl_month.get("4010", ZERO) + te.local_pl_month.get("4020", ZERO))
        loc_net = -sum(te.local_pl_month.values(), ZERO)
        grp_rev = -(te.pl_month.get("4010", ZERO) + te.pl_month.get("4020", ZERO))
        grp_net = -sum(te.pl_month.values(), ZERO)
        grp_ytd = -sum(te.pl_ytd.values(), ZERO)
        net_assets = sum(
            (v for c, v in te.bs.items() if consolidator.data.accounts[c].kind != "EQUITY"),
            ZERO,
        )
        out.append(
            {
                "entity": code,
                "name": te.entity.name,
                "currency": te.entity.currency,
                "closing_rate": te.rates.closing,
                "closing_rate_date": te.rates.closing_date.isoformat(),
                "average_rate": te.rates.average,
                "average_rate_days": te.rates.average_days,
                "revenue_local": loc_rev,
                "revenue_group": grp_rev,
                "net_income_local": loc_net,
                "net_income_group": grp_net,
                "net_income_ytd_group": grp_ytd,
                "net_assets_group": net_assets,
                "translation_reserve": -te.cta,
            }
        )
    return out


def variance(consolidator: Consolidator, period: str, view: str = "month") -> dict:
    """Actual against budget, with the gap split into an operating effect and an FX effect.

    Budget is translated at the rate on the opening date (the budget rate). Actual is translated
    at the monthly average rates. Operating effect = (actual - budget) in local currency at the
    budget rate. FX effect = actual in local currency x (average rate - budget rate). The two
    always add up to the total variance.
    """
    months = [period] if view == "month" else year_months(period)
    data = consolidator.data
    state = consolidator.state(period)
    rows = []
    for key, label, code in VARIANCE_ACCOUNTS:
        sign = Decimal(1) if data.accounts[code].kind == "INCOME" else Decimal(-1)
        actual_g = budget_g = operating = ZERO
        for entity in data.entities:
            te = state.entities[entity.code]
            budget_rate = te.rates.opening
            for m in months:
                actual_local = -data.moves[entity.code].get((m, code), ZERO)
                budget_local = data.budget.get((entity.code, m, code), ZERO) * sign
                avg = te.monthly_average[m]
                actual_g += mul_rate(actual_local, avg)
                budget_g += mul_rate(budget_local, budget_rate)
                operating += mul_rate(actual_local - budget_local, budget_rate)
        actual_g, budget_g, operating = q2(actual_g), q2(budget_g), q2(operating)
        total = actual_g - budget_g
        rows.append(
            {
                "key": key,
                "label": label,
                "actual": actual_g,
                "budget": budget_g,
                "variance": total,
                "operating_effect": operating,
                "fx_effect": total - operating,
                "favourable": total >= 0,
            }
        )
    totals = {
        k: sum((r[k] for r in rows), ZERO)
        for k in ("actual", "budget", "variance", "operating_effect", "fx_effect")
    }
    return {"period": period, "view": view, "rows": rows, "total": totals}
