"""Generate the synthetic sample ledgers for Demo Nordic Group.

The group, its entities and every amount are made up. The only real data is the ECB exchange
rate file in data/fx, which is used to value intercompany invoices and settlements.

Run from the repository root:

    python scripts/generate_sample_data.py

The output is deterministic: the same seed gives the same files.
"""

from __future__ import annotations

import csv
import json
import random
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

from nordic_close.fx import FxTable

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "sample"
FX_CSV = ROOT / "data" / "fx" / "ecb_eur_rates.csv"

D = Decimal
CENT = D("0.01")
MONTHS = list(range(1, 11))  # January to October 2026
YEAR = 2026
SEASON = [0.94, 0.95, 1.02, 1.00, 1.03, 1.02, 0.88, 0.92, 1.04, 1.06]


def q(x: float | Decimal | str) -> Decimal:
    return D(str(x)).quantize(CENT, rounding=ROUND_HALF_UP)


GROUP_ACCOUNTS = [
    ("1010", "Cash and bank", "ASSET", "BS", "cash", 10, 0),
    ("1100", "Trade receivables", "ASSET", "BS", "trade_receivables", 20, 0),
    ("1150", "Intercompany receivables", "ASSET", "BS", "ic_receivables", 25, 1),
    ("1190", "Unreconciled intercompany difference", "ASSET", "BS", "ic_suspense", 27, 0),
    ("1200", "Inventory", "ASSET", "BS", "inventory", 30, 0),
    ("1300", "Prepaid expenses and other current assets", "ASSET", "BS", "other_current_assets", 40, 0),
    ("1510", "Property, plant and equipment at cost", "ASSET", "BS", "ppe", 50, 0),
    ("1519", "Accumulated depreciation", "ASSET", "BS", "ppe", 55, 0),
    ("2010", "Trade payables", "LIABILITY", "BS", "trade_payables", 60, 0),
    ("2050", "Intercompany payables", "LIABILITY", "BS", "ic_payables", 65, 1),
    ("2100", "Accrued expenses", "LIABILITY", "BS", "accrued_expenses", 70, 0),
    ("2150", "Income tax payable", "LIABILITY", "BS", "tax_payable", 80, 0),
    ("2200", "Long-term borrowings", "LIABILITY", "BS", "borrowings_lt", 90, 0),
    ("2210", "Short-term borrowings", "LIABILITY", "BS", "borrowings_st", 95, 0),
    ("3010", "Share capital", "EQUITY", "BS", "share_capital", 100, 0),
    ("3050", "Retained earnings brought forward", "EQUITY", "BS", "retained_earnings", 110, 0),
    ("3090", "Translation reserve", "EQUITY", "BS", "translation_reserve", 120, 0),
    ("4010", "Revenue, external", "INCOME", "PL", "revenue", 200, 0),
    ("4020", "Revenue, intercompany", "INCOME", "PL", "ic_revenue", 210, 1),
    ("5010", "Cost of goods sold, external", "EXPENSE", "PL", "cogs", 220, 0),
    ("5020", "Cost of goods sold, intercompany", "EXPENSE", "PL", "ic_cogs", 230, 1),
    ("6010", "Personnel costs", "EXPENSE", "PL", "personnel", 240, 0),
    ("6020", "Other operating expenses", "EXPENSE", "PL", "other_opex", 250, 0),
    ("6030", "Depreciation", "EXPENSE", "PL", "depreciation", 260, 0),
    ("7010", "Finance income", "INCOME", "PL", "finance_income", 270, 0),
    ("7020", "Finance costs", "EXPENSE", "PL", "finance_costs", 280, 0),
    ("7030", "Intercompany FX residual (consolidation)", "EXPENSE", "PL", "ic_fx_residual", 285, 0),
    ("7040", "Foreign exchange differences", "EXPENSE", "PL", "fx_differences", 290, 0),
    ("7090", "Unreconciled intercompany difference (P&L)", "EXPENSE", "PL", "ic_pl_suspense", 295, 0),
    ("8010", "Income tax", "EXPENSE", "PL", "income_tax", 300, 0),
]

ENTITY_DEFS = {
    "SE": dict(name="Demo Sverige AB", cur="SEK", country="SE", parent=1, tax=D("0.206")),
    "NO": dict(name="Demo Norge AS", cur="NOK", country="NO", parent=0, tax=D("0.22")),
    "DK": dict(name="Demo Danmark ApS", cur="DKK", country="DK", parent=0, tax=D("0.22")),
    "FI": dict(name="Demo Suomi Oy", cur="EUR", country="FI", parent=0, tax=D("0.20")),
}

# group account -> (local code, local name). Swedish and Norwegian codes follow the national
# charts loosely; Danish and Finnish codes are illustrative.
LOCAL = {
    "SE": {
        "1010": ("1930", "Företagskonto"), "1100": ("1510", "Kundfordringar"),
        "1150": ("1660", "Fordringar hos koncernföretag"), "1200": ("1460", "Lager"),
        "1300": ("1790", "Förutbetalda kostnader"), "1510": ("1220", "Inventarier"),
        "1519": ("1229", "Ackumulerade avskrivningar"), "2010": ("2440", "Leverantörsskulder"),
        "2050": ("2460", "Skulder till koncernföretag"), "2100": ("2990", "Upplupna kostnader"),
        "2150": ("2510", "Skatteskulder"), "2200": ("2350", "Långfristiga skulder"),
        "2210": ("2393", "Kortfristiga skulder"), "3010": ("2081", "Aktiekapital"),
        "3050": ("2091", "Balanserad vinst"), "4010": ("3010", "Försäljning varor"),
        "4020": ("3071", "Koncerninterna intäkter"), "5010": ("4010", "Inköp varor"),
        "5020": ("4050", "Koncerninterna inköp"), "6010": ("7010", "Löner"),
        "6020": ("5010", "Övriga externa kostnader"), "6030": ("7830", "Avskrivningar"),
        "7010": ("8310", "Ränteintäkter"), "7020": ("8410", "Räntekostnader"),
        "7040": ("8330", "Valutakursdifferenser"), "8010": ("8910", "Skatt på årets resultat"),
    },
    "NO": {
        "1010": ("1920", "Bankinnskudd"), "1100": ("1500", "Kundefordringer"),
        "1150": ("1560", "Fordringer på konsernselskap"), "1200": ("1400", "Varelager"),
        "1300": ("1700", "Forskuddsbetalte kostnader"), "1510": ("1200", "Maskiner og anlegg"),
        "1519": ("1299", "Akkumulerte avskrivninger"), "2010": ("2400", "Leverandørgjeld"),
        "2050": ("2460", "Gjeld til konsernselskap"), "2100": ("2900", "Påløpte kostnader"),
        "2150": ("2500", "Betalbar skatt"), "2200": ("2200", "Langsiktig gjeld"),
        "2210": ("2300", "Kortsiktig gjeld"), "3010": ("2000", "Aksjekapital"),
        "3050": ("2050", "Annen egenkapital"), "4010": ("3000", "Salgsinntekt"),
        "4020": ("3100", "Salgsinntekt konsern"), "5010": ("4000", "Varekostnad"),
        "5020": ("4010", "Varekjøp konsern"), "6010": ("5000", "Lønn"),
        "6020": ("6300", "Annen driftskostnad"), "6030": ("6000", "Avskrivning"),
        "7010": ("8050", "Renteinntekt"), "7020": ("8150", "Rentekostnad"),
        "7040": ("8060", "Valutagevinst og -tap"), "8010": ("8300", "Skattekostnad"),
    },
    "DK": {
        "1010": ("5600", "Bank"), "1100": ("5000", "Debitorer"),
        "1150": ("5100", "Tilgodehavender hos koncernselskaber"), "1200": ("3000", "Varelager"),
        "1300": ("5400", "Periodeafgrænsningsposter"), "1510": ("4000", "Driftsmateriel"),
        "1519": ("4090", "Akkumulerede afskrivninger"), "2010": ("7000", "Kreditorer"),
        "2050": ("7100", "Gæld til koncernselskaber"), "2100": ("7300", "Skyldige omkostninger"),
        "2150": ("7400", "Skyldig selskabsskat"), "2200": ("8000", "Langfristet gæld"),
        "2210": ("8100", "Kortfristet gæld"), "3010": ("9000", "Selskabskapital"),
        "3050": ("9100", "Overført resultat"), "4010": ("1000", "Omsætning"),
        "4020": ("1100", "Omsætning koncern"), "5010": ("2000", "Vareforbrug"),
        "5020": ("2100", "Vareforbrug koncern"), "6010": ("2500", "Lønomkostninger"),
        "6020": ("2600", "Andre driftsomkostninger"), "6030": ("2700", "Afskrivninger"),
        "7010": ("3500", "Renteindtægter"), "7020": ("3600", "Renteudgifter"),
        "7040": ("3700", "Kursreguleringer"), "8010": ("3900", "Skat af årets resultat"),
    },
    "FI": {
        "1010": ("1910", "Pankkitilit"), "1100": ("1700", "Myyntisaamiset"),
        "1150": ("1780", "Saamiset konserniyrityksiltä"), "1200": ("1400", "Vaihto-omaisuus"),
        "1300": ("1800", "Siirtosaamiset"), "1510": ("1100", "Koneet ja kalusto"),
        "1519": ("1190", "Kertyneet poistot"), "2010": ("2870", "Ostovelat"),
        "2050": ("2880", "Velat konserniyrityksille"), "2100": ("2900", "Siirtovelat"),
        "2150": ("2920", "Tuloverovelka"), "2200": ("2500", "Pitkäaikaiset lainat"),
        "2210": ("2600", "Lyhytaikaiset lainat"), "3010": ("2000", "Osakepääoma"),
        "3050": ("2100", "Edellisten tilikausien voitto"), "4010": ("3000", "Liikevaihto"),
        "4020": ("3100", "Konsernimyynti"), "5010": ("4000", "Ostot"),
        "5020": ("4100", "Konserniostot"), "6010": ("6000", "Palkat"),
        "6020": ("7000", "Muut liiketoiminnan kulut"), "6030": ("6500", "Poistot"),
        "7010": ("9500", "Korkotuotot"), "7020": ("9600", "Korkokulut"),
        "7040": ("9700", "Kurssierot"), "8010": ("9900", "Tuloverot"),
    },
}

# Local-currency monthly profile per entity.
PROFILE = {
    "SE": dict(ext=D("30000000"), cogs_pct=D("0.56"), pers=D("0.17"), opex=D("0.12"),
               ppe=D("120000000"), int_rate=D("0.041"), capex=D("2000000")),
    "NO": dict(ext=D("22000000"), other_cogs=D("5900000"), pers=D("0.16"), opex=D("0.11"),
               ppe=D("60000000"), int_rate=D("0.054"), capex=D("1200000")),
    "DK": dict(ext=D("14000000"), other_cogs=D("5000000"), pers=D("0.17"), opex=D("0.11"),
               ppe=D("40000000"), int_rate=D("0.038"), capex=D("800000")),
    "FI": dict(ext=D("2000000"), other_cogs=D("850000"), pers=D("0.16"), opex=D("0.11"),
               ppe=D("4500000"), int_rate=D("0.039"), capex=D("100000")),
}

OPENING = {  # local currency, 1 January 2026
    "SE": dict(cash=45e6, ar=31e6, inv=36e6, other=6e6, ppe=120e6, dep=48e6, ap=28e6, accr=9e6,
               tax=3e6, debt=60e6, share=20e6),
    "NO": dict(cash=30e6, ar=23e6, inv=25e6, other=4e6, ppe=60e6, dep=22e6, ap=8.5e6, accr=6e6,
               tax=2e6, debt=40e6, share=10e6),
    "DK": dict(cash=20e6, ar=14.5e6, inv=14e6, other=2e6, ppe=40e6, dep=15e6, ap=6.5e6, accr=4e6,
               tax=1e6, debt=25e6, share=6e6),
    "FI": dict(cash=2.5e6, ar=2.05e6, inv=1.9e6, other=0.3e6, ppe=4.5e6, dep=1.7e6, ap=1.1e6,
               accr=0.5e6, tax=0.1e6, debt=2.2e6, share=0.8e6),
}

IC_MONTHLY_EUR = {"NO": D("600000"), "DK": D("450000"), "FI": D("350000")}  # SE sells to these

PEOPLE = {  # preparer, reviewer per entity (fictional names)
    "SE": ("Anna Lund", "Erik Nyman"),
    "NO": ("Ingrid Haug", "Erik Nyman"),
    "DK": ("Mads Holm", "Erik Nyman"),
    "FI": ("Aino Virta", "Erik Nyman"),
}


@dataclass
class Gen:
    fx: FxTable
    rng: random.Random
    rows: list[dict] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    state: dict[str, dict] = field(default_factory=dict)
    bank: list[dict] = field(default_factory=list)
    bank_lag: dict[tuple[str, str], int] = field(default_factory=dict)
    budget: list[dict] = field(default_factory=list)
    events: dict[str, dict] = field(default_factory=dict)

    def rate(self, cur: str, day: date) -> Decimal:
        """Units of ``cur`` per EUR on ``day`` (previous business day if needed)."""
        return self.fx.closing("EUR", cur, day).rate

    def journal(self, ent: str, day: date, desc: str, lines: list[dict]) -> None:
        self.counters[ent] = self.counters.get(ent, 0) + 1
        jid = f"{ent}-{day.year}-{day.month:02d}-{self.counters[ent]:05d}"
        total = sum((l["amount"] for l in lines), D(0))
        if total != 0:
            raise AssertionError(f"{jid} is unbalanced by {total}")
        cash_code = LOCAL[ent]["1010"][0]
        for n, l in enumerate(lines, start=1):
            amount = l["amount"]
            local = LOCAL[ent][l["acc"]][0]
            self.rows.append({
                "journal_id": jid, "entity_code": ent, "booking_date": day.isoformat(),
                "description": desc, "line_no": n, "local_code": local,
                "debit": f"{amount:.2f}" if amount > 0 else "",
                "credit": f"{-amount:.2f}" if amount < 0 else "",
                "ref": l.get("ref", ""), "memo": l.get("memo", ""),
                "ic_partner": l.get("partner", ""),
                "ic_eur": f"{l['eur']:.2f}" if "eur" in l else "",
            })
            if local == cash_code:
                self.bank.append({"entity": ent, "date": day, "amount": amount,
                                  "ref": l.get("ref", ""), "desc": desc})


def build() -> Gen:
    fx = FxTable.from_rows(
        (r["rate_date"], r["currency"], r["per_eur"])
        for r in csv.DictReader(FX_CSV.open(encoding="utf-8"))
    )
    g = Gen(fx=fx, rng=random.Random(2026))

    # Opening balances.
    for ent, o in OPENING.items():
        day = date(YEAR, 1, 1)
        assets = o["cash"] + o["ar"] + o["inv"] + o["other"] + o["ppe"] - o["dep"]
        liabilities = o["ap"] + o["accr"] + o["tax"] + o["debt"]
        retained = assets - liabilities - o["share"]
        lines = [
            dict(acc="1010", amount=q(o["cash"])), dict(acc="1100", amount=q(o["ar"])),
            dict(acc="1200", amount=q(o["inv"])), dict(acc="1300", amount=q(o["other"])),
            dict(acc="1510", amount=q(o["ppe"])), dict(acc="1519", amount=-q(o["dep"])),
            dict(acc="2010", amount=-q(o["ap"])), dict(acc="2100", amount=-q(o["accr"])),
            dict(acc="2150", amount=-q(o["tax"])), dict(acc="2200", amount=-q(o["debt"])),
            dict(acc="3010", amount=-q(o["share"])), dict(acc="3050", amount=-q(retained)),
        ]
        for l in lines:
            if l["acc"] == "1010":
                l["ref"] = "OPENING"
        g.journal(ent, day, "Opening balances", lines)
        g.state[ent] = dict(cash=q(o["cash"]), ar=q(o["ar"]), ap=q(o["ap"]), tax=q(o["tax"]),
                            ic_open=[])
    # The opening cash line is a balance, not a bank movement.
    g.bank = [b for b in g.bank if b["ref"] != "OPENING"]

    for i, month in enumerate(MONTHS):
        for ent in ("SE", "NO", "DK", "FI"):
            month_entity(g, ent, month, i)
    return g


def month_entity(g: Gen, ent: str, month: int, i: int) -> None:
    p = PROFILE[ent]
    cur = ENTITY_DEFS[ent]["cur"]
    st = g.state[ent]
    tag = f"{YEAR}-{month:02d}"
    noise = D(str(1 + g.rng.uniform(-0.02, 0.02)))
    season = D(str(SEASON[i]))
    ext_rev = q(p["ext"] * season * noise)

    pl_lines_total = D(0)  # profit before tax, credit-positive, built from lines below

    def post(day: date, desc: str, lines: list[dict]) -> None:
        nonlocal pl_lines_total
        for l in lines:
            if l["acc"] in {a[0] for a in GROUP_ACCOUNTS if a[3] == "PL"}:
                pl_lines_total += -l["amount"]
        g.journal(ent, day, desc, lines)

    ap_open = st["ap"]
    # --- Collections of last month's receivables (before this month's sales are added).
    collect = q(st["ar"] * D("0.97"))
    for day_n, share in ((12, D("0.6")), (26, D("0.4"))):
        amt = q(collect * share) if day_n == 12 else collect - q(collect * D("0.6"))
        post(date(YEAR, month, day_n), "Customer receipts", [
            dict(acc="1010", amount=amt, ref=f"RCPT-{ent}-{tag}-{day_n}"),
            dict(acc="1100", amount=-amt),
        ])
        st["ar"] -= amt

    # --- External sales.
    sales_days = ((5, D("0.35")), (14, D("0.35")), (25, D("0.30")))
    booked = D(0)
    for k, (day_n, share) in enumerate(sales_days, start=1):
        amt = q(ext_rev * share) if k < 3 else ext_rev - booked
        booked += amt
        post(date(YEAR, month, day_n), "Sales invoices", [
            dict(acc="1100", amount=amt, ref=f"INV-{ent}-{tag}-{k}"),
            dict(acc="4010", amount=-amt),
        ])
        st["ar"] += amt
    event_rev = D(0)
    if ent == "NO" and month == 10:
        eur = D("900000")
        event_rev = q(eur * g.rate("NOK", date(YEAR, 10, 15)))
        post(date(YEAR, 10, 15), "Large one-off order", [
            dict(acc="1100", amount=event_rev, ref="ORDER-NO-1042"),
            dict(acc="4010", amount=-event_rev),
        ])
        st["ar"] += event_rev
        g.events["NO-order"] = dict(eur=eur, local=event_rev)
    total_ext = ext_rev + event_rev

    # --- Intercompany: SE sells to the others.
    ic_rev_local = D(0)
    if ent == "SE":
        for buyer, base in IC_MONTHLY_EUR.items():
            eur_total = q(base * D(str(1 + g.rng.uniform(-0.03, 0.03))))
            first = q(eur_total * D("0.55"))
            invoices = [(8, first), (22, eur_total - first)]
            if buyer == "DK" and month == 10:
                invoices.append((29, D("120000.00")))
            for day_n, eur in invoices:
                day = date(YEAR, month, day_n)
                ref = f"ICINV-SE-{buyer}-{tag}-{day_n:02d}"
                local_s = q(eur * g.rate("SEK", day))
                post(day, f"Intercompany invoice to {buyer}", [
                    dict(acc="1150", amount=local_s, ref=ref, partner=buyer, eur=eur),
                    dict(acc="4020", amount=-local_s, ref=ref, partner=buyer, eur=eur),
                ])
                ic_rev_local += local_s
                st["ic_open"].append(dict(partner=buyer, eur=eur, local=local_s, ref=ref))
                bcur = ENTITY_DEFS[buyer]["cur"]
                local_b = q(eur * g.rate(bcur, day))
                g.state[buyer].setdefault("ic_pending", []).append(
                    dict(day=day, seller="SE", eur=eur, local=local_b, ref=ref,
                         skip=(buyer == "DK" and day_n == 29 and month == 10))
                )
        g.events["DK-late-invoice"] = dict(eur=D("120000.00"), ref="ICINV-SE-DK-2026-10-29")
    # Buyers book their purchases (events carry the skip flag for a late-booked invoice).
    ic_cogs_local = D(0)
    for item in st.pop("ic_pending", []):
        if item["skip"]:
            continue
        post(item["day"], "Intercompany purchase from SE", [
            dict(acc="5020", amount=item["local"], ref=item["ref"], partner="SE", eur=item["eur"]),
            dict(acc="2050", amount=-item["local"], ref=item["ref"], partner="SE", eur=item["eur"]),
        ])
        ic_cogs_local += item["local"]
        st["ic_open"].append(dict(partner="SE", eur=item["eur"], local=item["local"],
                                  ref=item["ref"], payable=True))

    # --- Costs.
    if ent == "SE":
        cogs = q(p["cogs_pct"] * (total_ext + ic_rev_local))
    else:
        cogs = q(p["other_cogs"] * season * noise) + (q(event_rev * D("0.6")) if event_rev else D(0))
    post(date(YEAR, month, 18), "Supplier invoices, goods", [
        dict(acc="5010", amount=cogs), dict(acc="2010", amount=-cogs, ref=f"SUP-{ent}-{tag}-G"),
    ])
    st["ap"] += cogs
    opex = q(p["opex"] * p["ext"] * season)
    post(date(YEAR, month, 20), "Supplier invoices, overheads", [
        dict(acc="6020", amount=opex), dict(acc="2010", amount=-opex, ref=f"SUP-{ent}-{tag}-O"),
    ])
    st["ap"] += opex
    pers = q(p["pers"] * p["ext"] * season)
    post(date(YEAR, month, 25), "Payroll", [
        dict(acc="6010", amount=pers),
        dict(acc="1010", amount=-pers, ref=f"PAYROLL-{ent}-{tag}"),
    ])
    st["cash"] -= pers

    # --- Supplier payments.
    pay = q(ap_open * D("0.97"))
    extras = []
    if ent == "NO" and month == 10:
        cheque = D("125000.00")
        pay -= cheque
        extras.append((30, cheque, "CHQ-NO-1007"))
        g.events["NO-cheque"] = dict(amount=cheque, ref="CHQ-NO-1007")
    if ent == "FI" and month == 10:
        extras.append((20, D("47520.00"), "PAY-FI-2210"))
        g.events["FI-transposition"] = dict(gl=D("47520.00"), bank=D("47250.00"), ref="PAY-FI-2210")
    for day_n, amt, ref in [(15, pay, f"PAY-{ent}-{tag}")] + extras:
        post(date(YEAR, month, day_n), "Supplier payment", [
            dict(acc="2010", amount=amt), dict(acc="1010", amount=-amt, ref=ref),
        ])
        st["ap"] -= amt

    # --- Capex.
    capex = D(0)
    if month in (3, 6, 9):
        capex = p["capex"]
    if ent == "SE" and month == 10:
        capex = D("6500000")
        g.events["SE-capex"] = dict(amount=capex, ref="CAPEX-SE-0917")
    if capex:
        post(date(YEAR, month, 12), "Equipment purchase", [
            dict(acc="1510", amount=capex),
            dict(acc="1010", amount=-capex, ref="CAPEX-SE-0917" if ent == "SE" and month == 10
                 else f"CAPEX-{ent}-{tag}"),
        ])

    # --- Depreciation, interest.
    dep = q(p["ppe"] * D("0.0075"))
    post(date(YEAR, month, 28), "Depreciation", [
        dict(acc="6030", amount=dep), dict(acc="1519", amount=-dep),
    ])
    debt = q(OPENING[ent]["debt"])
    interest = q(debt * p["int_rate"] / 12)
    post(date(YEAR, month, 28), "Loan interest", [
        dict(acc="7020", amount=interest),
        dict(acc="1010", amount=-interest, ref=f"INTEREST-{ent}-{tag}"),
    ])
    income = q(D(str(OPENING[ent]["cash"])) * D("0.01") / 12)
    post(date(YEAR, month, 28), "Interest received", [
        dict(acc="1010", amount=income, ref=f"INTINC-{ent}-{tag}"), dict(acc="7010", amount=-income),
    ])

    # --- Settle last month's intercompany invoices.
    settle_day = date(YEAR, month, 28)
    still_open = []
    for inv in st["ic_open"]:
        inv_month = int(inv["ref"].split("-")[-2])
        if inv_month >= month:
            still_open.append(inv)
            continue
        eur = inv["eur"]
        booked = inv["local"]
        cash_local = q(eur * g.rate(cur, settle_day))
        diff = cash_local - booked
        ref = f"SETTLE-{inv['ref']}"
        if inv.get("payable"):  # we are the buyer: we pay out
            lines = [
                dict(acc="2050", amount=booked, ref=ref, partner=inv["partner"], eur=eur),
                dict(acc="1010", amount=-cash_local, ref=ref),
                dict(acc="7040", amount=cash_local - booked),
            ]
        else:  # we are the seller: we receive
            lines = [
                dict(acc="1010", amount=cash_local, ref=ref),
                dict(acc="1150", amount=-booked, ref=ref, partner=inv["partner"], eur=eur),
                dict(acc="7040", amount=-diff),
            ]
        lines = [l for l in lines if l["amount"] != 0]
        post(settle_day, "Intercompany settlement", lines)
    st["ic_open"] = still_open

    # --- Tax: pay at quarter ends, accrue every month.
    if month in (3, 6, 9):
        post(date(YEAR, month, 20), "Income tax payment", [
            dict(acc="2150", amount=st["tax"]),
            dict(acc="1010", amount=-st["tax"], ref=f"TAX-{ent}-{tag}"),
        ])
        st["tax"] = D(0)
    tax = q(max(pl_lines_total, D(0)) * ENTITY_DEFS[ent]["tax"])
    if tax:
        post(date(YEAR, month, 28), "Income tax accrual", [
            dict(acc="8010", amount=tax), dict(acc="2150", amount=-tax),
        ])
        st["tax"] += tax

    # --- Budget (no noise, no one-offs).
    plan_ext = q(p["ext"] * season)
    budget = {"4010": plan_ext}
    if ent == "SE":
        plan_ic = sum((v for v in IC_MONTHLY_EUR.values()), D(0)) * g.rate("SEK", date(YEAR - 1, 12, 31))
        budget["4020"] = q(plan_ic)
        budget["5010"] = q(p["cogs_pct"] * (plan_ext + plan_ic))
    else:
        plan_ic = IC_MONTHLY_EUR[ent] * g.rate(cur, date(YEAR - 1, 12, 31))
        budget["5020"] = q(plan_ic)
        budget["5010"] = q(p["other_cogs"] * season)
    budget["6010"] = pers
    budget["6020"] = opex
    budget["6030"] = dep
    budget["7020"] = interest
    for code, amount in budget.items():
        g.budget.append(dict(entity_code=ent, period=tag, group_code=code, amount=f"{amount:.2f}"))


def bank_files(g: Gen) -> tuple[list[dict], list[dict]]:
    """Derive bank statements from cash lines, then inject the October differences."""
    lines: list[dict] = []
    rng = random.Random(7)
    for b in sorted(g.bank, key=lambda x: (x["entity"], x["date"], x["ref"])):
        ent, day, amount, ref = b["entity"], b["date"], b["amount"], b["ref"]
        # October exceptions.
        if ref == "CHQ-NO-1007":
            continue  # cleared in November
        if ref == "PAY-FI-2210":
            amount = D("-47250.00")  # the bank shows the correct amount; the ledger was keyed wrong
        statement_day = day
        bank_ref = ref
        if rng.random() < 0.30:
            bank_ref = ""  # some bank lines carry no usable reference
        if rng.random() < 0.25 and day.day <= 26 and amount < 0:
            statement_day = day + timedelta(days=rng.choice([1, 2]))
        lines.append(dict(entity_code=ent, statement_date=statement_day.isoformat(), ref=bank_ref,
                          amount=f"{amount:.2f}", description=b["desc"]))
    lines.append(dict(entity_code="DK", statement_date="2026-10-31", ref="BANKFEE-OCT",
                      amount="-1850.00", description="Bank fees October"))

    balances: list[dict] = []
    for ent, o in OPENING.items():
        running = q(o["cash"])
        for m in MONTHS:
            tag = f"{YEAR}-{m:02d}"
            month_sum = sum(
                (D(l["amount"]) for l in lines
                 if l["entity_code"] == ent and l["statement_date"].startswith(tag)),
                D(0),
            )
            closing = running + month_sum
            balances.append(dict(entity_code=ent, period=tag, opening=f"{running:.2f}",
                                 closing=f"{closing:.2f}", preparer=PEOPLE[ent][0],
                                 reviewer=PEOPLE[ent][1]))
            running = closing
    return lines, balances


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


CLOSE_PLAN = {
    "period": "2026-10",
    "target_workday": 5,
    "first_workday": "2026-11-02",
    "tasks": [
        {"id": "fx", "name": "Load ECB rates and check the closing rate", "owner": "Anna Lund",
         "reviewer": "Erik Nyman", "days": 0.5, "after": [], "key_control": True},
        {"id": "cutoff", "name": "Entity cut-off and sub-ledger close", "owner": "Mads Holm",
         "reviewer": "Erik Nyman", "days": 1.0, "after": [], "key_control": True},
        {"id": "bank", "name": "Bank reconciliations", "owner": "Ingrid Haug",
         "reviewer": "Erik Nyman", "days": 1.0, "after": ["cutoff"], "key_control": True},
        {"id": "accruals", "name": "Accruals and prepayments", "owner": "Aino Virta",
         "reviewer": "Anna Lund", "days": 1.0, "after": ["cutoff"], "key_control": False},
        {"id": "ic_confirm", "name": "Intercompany confirmation", "owner": "Nina Berg",
         "reviewer": "Anna Lund", "days": 1.0, "after": ["cutoff"], "key_control": True},
        {"id": "translate", "name": "Translate entities to SEK", "owner": "Anna Lund",
         "reviewer": "Erik Nyman", "days": 0.5, "after": ["fx", "bank", "accruals"], "key_control": True},
        {"id": "eliminate", "name": "Eliminate intercompany", "owner": "Anna Lund",
         "reviewer": "Erik Nyman", "days": 0.5, "after": ["translate", "ic_confirm"], "key_control": True},
        {"id": "flux", "name": "Flux review and explanations", "owner": "Nina Berg",
         "reviewer": "Karin Roos", "days": 1.0, "after": ["eliminate"], "key_control": True},
        {"id": "pack", "name": "Reporting pack", "owner": "Anna Lund",
         "reviewer": "Karin Roos", "days": 0.5, "after": ["flux"], "key_control": False},
        {"id": "signoff", "name": "CFO sign-off", "owner": "Karin Roos",
         "reviewer": "Lars Ek", "days": 0.5, "after": ["pack"], "key_control": True},
    ],
}


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    g = build()
    bank_lines, balances = bank_files(g)

    write_csv(OUT / "entities.csv",
              ["code", "name", "currency", "country", "is_parent", "ownership_pct"],
              [dict(code=c, name=e["name"], currency=e["cur"], country=e["country"],
                    is_parent=e["parent"], ownership_pct=100) for c, e in ENTITY_DEFS.items()])
    write_csv(OUT / "group_accounts.csv",
              ["code", "name", "kind", "statement", "report_line", "sort_order", "is_intercompany"],
              [dict(zip(["code", "name", "kind", "statement", "report_line", "sort_order",
                         "is_intercompany"], a, strict=True)) for a in GROUP_ACCOUNTS])
    write_csv(OUT / "account_map.csv", ["entity_code", "local_code", "local_name", "group_code"],
              [dict(entity_code=e, local_code=v[0], local_name=v[1], group_code=k)
               for e, m in LOCAL.items() for k, v in m.items()])
    write_csv(OUT / "journals.csv",
              ["journal_id", "entity_code", "booking_date", "description", "line_no", "local_code",
               "debit", "credit", "ref", "memo", "ic_partner", "ic_eur"], g.rows)
    write_csv(OUT / "budget.csv", ["entity_code", "period", "group_code", "amount"], g.budget)
    write_csv(OUT / "bank_statements.csv",
              ["entity_code", "statement_date", "ref", "amount", "description"], bank_lines)
    write_csv(OUT / "bank_balances.csv",
              ["entity_code", "period", "opening", "closing", "preparer", "reviewer"], balances)
    if not (OUT / "explanations.csv").exists():
        write_csv(OUT / "explanations.csv",
                  ["period", "line_key", "text", "author", "reviewer"], [])
    (OUT / "close_plan.json").write_text(json.dumps(CLOSE_PLAN, indent=2) + "\n", encoding="utf-8")

    cash = {}
    for ent in ENTITY_DEFS:
        code = LOCAL[ent]["1010"][0]
        balance = D(0)
        low = None
        for r in g.rows:
            if r["entity_code"] == ent and r["local_code"] == code:
                balance += D(r["debit"] or 0) - D(r["credit"] or 0)
                low = balance if low is None else min(low, balance)
        cash[ent] = (str(balance), str(low))
    print(f"journals: {len({r['journal_id'] for r in g.rows})}, lines: {len(g.rows)}")
    print("cash (closing, lowest):", cash)
    print("events:", {k: {a: str(b) for a, b in v.items()} for k, v in g.events.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
