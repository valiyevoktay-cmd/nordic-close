# Architecture

## Layout

```
CSV files ──► ingest ──► SQLite ──► ledger ──► translate ──► intercompany ──► consolidate
                                       │                                         │
                                       └──────────────► controls ◄───────────────┘
                                                  (bank_rec, flux, calendar, gate)
                                                              │
                                    service ◄─────────────────┘
                                    │    │
                                  api  cli ──► excel_pack
                                    │
                                web view
```

| Module | Responsibility |
|---|---|
| `ingest` | Validate every input file, then write everything in one transaction. |
| `ledger` | Entities, accounts, movements and trial balances straight from journal lines. |
| `fx` | Cross rates, monthly average, closing rate with look-back. |
| `translate` | IAS 21 translation of one entity and the independent reserve proof. |
| `intercompany` | Pair comparison in EUR, suggested journals, eliminations. |
| `consolidate` | Combines translated entities and eliminations into group figures per period. |
| `reports` | Statements, KPIs, entity view and budget variance. |
| `controls/*` | Bank reconciliation, flux review, close calendar and the gate that combines them. |
| `service` | One place that API and CLI both call: runs, hashes, audit log, period lock. |
| `api`, `cli`, `excel_pack`, `web/` | Delivery. No business rules live here. |

Dependencies point downwards in the diagram. `reports` and `controls` never write to the
database; only `ingest` and `service` do.

## Decisions

**Integer minor units and `Decimal`.** Floats drift, and a consolidation has to foot to the
cent. Amounts are stored as integers, rates as text, and all arithmetic is `Decimal`. The
final consolidated trial balance is rounded to two decimals with the translation reserve as
the plug, and the rounding amount is small by construction.

**Cross rates from EUR.** The ECB publishes rates against the euro only. The rate from
currency A to currency B is `per_eur(B) / per_eur(A)`, computed per day and then averaged.
Averaging daily cross rates is not the same as dividing two averages, and the first is what
a controller would calculate by hand.

**Closing rate with look-back, and a gate on its age.** Month end is often a weekend, so the
last rate on or before that day is used. If that rate is more than five days old the FX gate
fails instead of silently using stale data.

**Translation reserve as the balancing figure, proved separately.** Making the reserve the
plug guarantees the trial balance foots, which proves nothing by itself. The proof rebuilds
the reserve from raw movements and monthly profits and compares it with the plug.

**Intercompany compared in EUR, eliminated in the presentation currency.** Both sides of a
pair carry the EUR amount of the original transaction, so an agreed pair compares exactly
even though the two ledgers are in different currencies. Genuine differences are parked in
suspense accounts (1190 balance sheet, 7090 income statement) so the statements still foot,
and the gate blocks until they are cleared. The pure FX residual has its own line (7030).

**Bank reconciliation over cumulative history.** A month-only view cannot prove the closing
balance. Matching runs over all history, and the proof is `ledger - bank = outstanding
items - unrecorded items`, which must be zero. Each statement line is matched once.

**Flux explanations are linted.** A threshold alone produces explanations such as "timing".
The review requires an amount in the text, a minimum length, no boilerplate and a reviewer
who is not the author.

**Gates, not warnings.** Each control returns pass or fail with one reason. The close is
`ready` only if all eight pass. This keeps the answer unambiguous and the history easy to
read.

**Deterministic runs.** The input hash covers all tables, the close plan and the settings.
The output hash covers the figures. Two runs on the same inputs must agree, and a test
checks it.

**SQLite and a thin web layer.** One file, no server, and a reviewer can open it with any
SQL tool. The web view is plain HTML, CSS and JavaScript with a strict content security
policy, so there is no build step and no inline script.

## Security notes

- All SQL values are bound parameters. The few statements that interpolate a table name use
  fixed lists or names read from `sqlite_master`.
- The API key check uses `secrets.compare_digest`.
- Responses carry a content security policy, `nosniff`, `no-referrer` and `X-Frame-Options`.
- The browser view writes to the page with `textContent` only.
- Text from the ledger is written to Excel as plain strings, never as formulas or links.
- Nothing connects to the network except `fx-update`, which only talks to the ECB over HTTPS.
