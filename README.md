# nordic-close

A month-end close and consolidation engine for a group with entities in Sweden, Norway,
Denmark and Finland. It takes four local ledgers in four currencies and produces one
consolidated set of numbers in a single presentation currency, together with a plain answer
to the question a controller actually has at the end of the month: can we sign off, and if
not, what is in the way?

The sample group (Demo Nordic Group) is made up. The exchange rates are the real ECB euro
reference rates, so the translation runs on real market data.

## What it does

- Loads journals, mappings, budget, bank statements and a close plan from CSV into SQLite.
  A bad file is rejected as a whole and the database stays as it was.
- Translates each entity under IAS 21: assets and liabilities at the closing rate, income and
  expenses at the monthly average, equity at the opening rate. The difference goes to a
  translation reserve, and an independent roll-forward proves that the reserve is right.
- Compares intercompany balances per pair in EUR, suggests the journal that fixes a
  difference, and eliminates the rest.
- Reconciles each bank account to the statement in three passes (reference and amount,
  amount within a date window, digit transpositions) and checks that the proof closes to zero.
- Reviews month-on-month movements and rejects explanations such as "timing" that name no
  amount.
- Checks the close plan: order of tasks, critical path, slack, owner load and reviewer
  separation.
- Produces the income statement, balance sheet, KPIs, entity contribution and budget
  variance split into an operating and an FX effect.
- Runs eight gates and returns `ready` or `blocked`, stores the run with a hash of its
  inputs, and can lock a period once it is ready.

## Quick start

Requires Python 3.11 or newer.

```
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"

nordic-close init                # load data/sample into data/nordic_close.db
nordic-close close --period 2026-09
nordic-close close --period 2026-10
nordic-close serve               # http://127.0.0.1:8000
```

September closes clean. October is blocked on purpose, because the sample data contains the
kind of problems a real close has:

```
2026-10: BLOCKED  (run 1)
  [ok  ] Ledger integrity
  [ok  ] Exchange rates
  [ok  ] Translation proof
  [FAIL] Intercompany agreement: SE -> DK differs by EUR 120,000.00
  [FAIL] Bank reconciliations: DK: open items; FI: open items
  [ok  ] Flux explanations
  [ok  ] Close calendar
  [ok  ] Consolidated statements foot
```

The exit code is 0 for ready, 4 for blocked and 2 for a period that does not exist, so the
command can be used in a script or a scheduled job. The web view shows the same result with
the detail behind each gate, and the Excel pack (`nordic-close export --period 2026-10`)
contains the statements with live subtotal formulas.

## Using your own data

Copy `data/sample` and replace the files. The columns are described in
[docs/schema.md](docs/schema.md). Point the tool at the folder with `NORDIC_CLOSE_DATA` or
`nordic-close init --data path/to/folder`. To refresh the exchange rates from the ECB, run
`nordic-close fx-update`; it is the only command that uses the network.

Settings are environment variables, listed with their defaults in `.env.example`.

## API

The web view is a thin client of the REST API. Routes, parameters and error codes are in
[docs/api.md](docs/api.md); interactive documentation is served at `/api/docs`. Set
`NORDIC_CLOSE_API_KEY` to require an `X-API-Key` header on every route except
`/api/health`.

## Design in one paragraph

Amounts are integer minor units in the database and `Decimal` in code; no floats. Everything
a report needs is derived from journal lines, so any number can be traced back to a journal.
Rates are looked up, never typed in. The same inputs give the same outputs, which is checked
by hashing both. More detail and the reasons behind the main decisions are in
[docs/architecture.md](docs/architecture.md), and the requirements the tests are written
against are in [docs/spec.md](docs/spec.md).

## Tests

```
pytest            # runs with coverage; fails below 85%
ruff check .
```

The suite has 106 tests and covers about 98% of the package. The translation, intercompany
and reconciliation tests use small hand-calculated cases, so a wrong formula fails a test
rather than just changing a number.

## Limits

This is a reporting engine, not a ledger. It assumes wholly owned subsidiaries and does not
do deferred tax, hedge accounting, minority interests or a cash flow statement. See the
out-of-scope list in the spec.

## License

MIT. See [LICENSE](LICENSE).
