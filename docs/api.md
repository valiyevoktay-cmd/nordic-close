# API

Base path `/api`. All amounts are decimal strings in the presentation currency unless a field
says otherwise. Periods are `YYYY-MM`. If `NORDIC_CLOSE_API_KEY` is set, send it in the
`X-API-Key` header; only `/api/health` is open.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | Liveness and version |
| GET | `/api/periods` | Periods with data, lock state and last run status |
| GET | `/api/entities` | Entities and currencies |
| GET | `/api/reports/income-statement?period=` | Consolidated income statement with month, prior month and year-to-date columns |
| GET | `/api/reports/balance-sheet?period=` | Consolidated balance sheet with a zero check |
| GET | `/api/reports/kpis?period=` | Margins, leverage and liquidity ratios |
| GET | `/api/reports/entities?period=` | Contribution by entity in local and presentation currency |
| GET | `/api/reports/variance?period=&view=month\|ytd` | Actual against budget, split into operating and FX effect |
| GET | `/api/controls/summary?period=` | All gates with pass/fail and reason |
| GET | `/api/controls/intercompany?period=` | Pair comparison, differences, eliminations |
| GET | `/api/controls/reconciliations?period=` | Bank reconciliation per entity |
| GET | `/api/controls/flux?period=` | Material fluctuations and explanation status |
| GET | `/api/controls/calendar` | Close plan, critical path, owner load |
| GET | `/api/fx?period=` | Average and closing rates used |
| POST | `/api/close/runs` | Run the close for `{"period": "YYYY-MM"}` and store it |
| GET | `/api/close/runs?period=` | Run history |
| GET | `/api/export/pack.xlsx?period=` | Excel reporting pack |

## Errors

| Status | Meaning |
|---|---|
| 401 | API key required or wrong |
| 404 | No data for the period |
| 422 | Parameter in the wrong format |

Error bodies are `{"detail": "..."}`.

## Example

```
GET /api/controls/summary?period=2026-10

{
  "period": "2026-10",
  "status": "blocked",
  "gates": [
    {"gate": "ledger_integrity", "status": "pass", "reason": ""},
    {"gate": "intercompany", "status": "fail", "reason": "1 pair differs: SE -> DK EUR 120,000.00"}
  ]
}
```
