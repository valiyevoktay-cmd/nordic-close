# Specification: Nordic month-end close and consolidated reporting

Status: Approved for implementation
Author: Oktay Valiyev
Version: 1.0

## 1. Context

A group with subsidiaries in Sweden, Norway, Denmark and Finland keeps its books in four
currencies (SEK, NOK, DKK, EUR) and has to publish one set of numbers in a single
presentation currency every month. Doing that by hand in spreadsheets means the same few
problems come back each close:

- exchange-rate lookups and cross rates are done by hand and are hard to reproduce;
- intercompany balances do not agree and nobody can say why;
- bank reconciliations are marked "done" without a proof that the difference is zero;
- large month-on-month movements are explained with phrases such as "timing";
- the reporting pack cannot be rebuilt from the inputs if someone questions a number.

This project is a small, deterministic close-and-consolidation engine for that problem. It
reads entity ledgers, translates them to the presentation currency under IAS 21, eliminates
intercompany items, runs the close controls, and produces a reporting pack (API, web view
and Excel file) together with a clear "ready to sign off / blocked" answer.

The sample ledgers are synthetic (a made-up group called Demo Nordic Group). The exchange
rates are the real ECB euro reference rates, so the translation maths runs on real market
data.

## 2. Functional requirements

| ID | Requirement |
|---|---|
| FR-1 | The system MUST load entity ledgers (journal lines in local currency), a group chart of accounts and per-entity account mappings from CSV into a SQLite database. |
| FR-2 | Loading MUST reject: an unbalanced journal, an unmapped local account, an unknown entity, a journal id reused with a different entity or date, a repeated line number inside a journal, a journal dated in a locked period, a line with both debit and credit set, an amount with more than two decimals, an intercompany account line without `ic_partner` and `ic_eur`, and those fields on any other account. A rejected load MUST leave the database unchanged. |
| FR-3 | The system MUST derive a trial balance per entity and period from journal lines. Each entity trial balance MUST sum to zero. |
| FR-4 | The system MUST store ECB euro reference rates and derive cross rates to the presentation currency. For each period it MUST compute an average rate (arithmetic mean of the daily rates in the month) and a closing rate (last rate on or before month end). |
| FR-5 | The system MUST fail the FX gate if the closing rate is more than 5 calendar days older than month end, or if a month has no rates at all. |
| FR-6 | The system MUST translate each entity to the presentation currency under IAS 21: assets and liabilities at the closing rate, income and expenses at the monthly average rate, opening equity at the rate on the opening date, with the difference recognised as a translation reserve. The translated trial balance MUST sum to zero within 0.01. |
| FR-7 | The system MUST compare intercompany balances and intercompany revenue/cost per counterparty pair in the transaction currency (EUR) and report every difference. It MUST post elimination entries and show any remaining FX residual on its own line. |
| FR-8 | The system MUST reconcile each entity's bank account to the statement for the period by matching on reference, then on amount within a date window, then by detecting digit transpositions. It MUST produce a proof that closes to zero, list reconciling items with age, and propose the journal needed for each item that is not a timing difference. |
| FR-9 | The system MUST flag a consolidated line as a material fluctuation when both the absolute and the percentage change exceed policy thresholds, when it changes sign, or when it appears or disappears. A sign change or an appearance only counts when the movement is above the absolute threshold. A flagged line MUST have an explanation that names an amount and is not boilerplate. |
| FR-10 | The system MUST read a close plan (tasks, owners, durations, predecessors, reviewers), order tasks by dependency, compute the critical path and slack, and check that no task lacks an owner and no reviewer is also the preparer. |
| FR-11 | The system MUST produce a consolidated income statement (month and year to date), a balance sheet, KPIs, an entity contribution view, and a budget variance split into an operating effect and an FX translation effect. |
| FR-12 | The system MUST produce a close status of `ready` or `blocked`, with one reason per failed gate, and store every run with a hash of its inputs. Running again on the same inputs MUST give the same outputs. |
| FR-13 | The system MUST export the reporting pack as an Excel workbook. |
| FR-14 | The system MUST expose the results through a REST API and a browser view. Only the close run and the export write anything. |
| FR-15 | The system MUST record every close run and every export in an audit log. |

## 3. Non-functional requirements

| ID | Requirement |
|---|---|
| NFR-1 | Money MUST be held as integer minor units in storage and as `Decimal` in code. Floats MUST NOT be used for amounts or rates. |
| NFR-2 | A full close of the sample data (4 entities, 10 months) MUST complete in under 2 seconds on a laptop. Measured: about 0.1 s. |
| NFR-3 | The API MUST answer read requests in under 200 ms at p95 on the sample data. Measured: 20-30 ms. |
| NFR-4 | Nothing may call the network at run time except the explicit `fx-update` command. |
| NFR-5 | The repository MUST contain no secrets. Configuration comes from environment variables listed in `.env.example`. |
| NFR-6 | If `NORDIC_CLOSE_API_KEY` is set, every API route except `/api/health` MUST require it in the `X-API-Key` header. |
| NFR-7 | The web view MUST work with the keyboard, label every table, and meet WCAG AA contrast in light and dark mode. |
| NFR-8 | Automated test coverage of the `nordic_close` package MUST be at least 85%. |
| NFR-9 | All SQL MUST use bound parameters. |

## 4. Acceptance criteria

| ID | Given | When | Then | Covers |
|---|---|---|---|---|
| AC-1 | a journal whose debits differ from credits by 0.01 | it is loaded | the load fails, the message names the journal, and no rows are written | FR-2 |
| AC-2 | a journal line on an account with no mapping | it is loaded | the load fails and names the entity and account | FR-2 |
| AC-3 | the sample data | trial balances are derived | every entity sums to zero in every period | FR-3 |
| AC-4 | daily rates for a month | the average rate is requested | it equals the arithmetic mean of that month's daily rates | FR-4 |
| AC-5 | a closing date that falls on a weekend | the closing rate is requested | the rate of the previous business day is used | FR-4 |
| AC-6 | no rate within 5 days of month end | the FX gate runs | the gate fails and says which currency | FR-5 |
| AC-7 | a NOK entity with known balances and known rates | it is translated | assets and liabilities use the closing rate, profit uses the monthly averages and the reserve equals the hand-computed value | FR-6 |
| AC-8 | any translated entity | the translated trial balance is summed | the total is within 0.01 | FR-6 |
| AC-9 | a counterparty books an invoice one month late | the intercompany check runs | the pair shows the exact EUR difference and a suggested journal | FR-7 |
| AC-10 | matching intercompany balances | eliminations are posted | receivable and payable are zero after elimination | FR-7 |
| AC-11 | a GL amount of 47,520.00 against a bank amount of 47,250.00 | the reconciliation runs | the pair is reported as a transposition with a difference of 270.00 | FR-8 |
| AC-12 | a bank fee on the statement and not in the ledger | the reconciliation runs | the item is listed as needing a journal and the entity is not reconciled | FR-8 |
| AC-13 | a revenue line moving by 15% and more than the absolute threshold with no explanation | flux runs | the line is flagged and the gate blocks | FR-9 |
| AC-14 | an explanation of "timing" | flux runs | the explanation is rejected as boilerplate | FR-9 |
| AC-15 | two tasks in a cycle | the plan is loaded | loading fails and names the cycle | FR-10 |
| AC-16 | a task whose reviewer is its owner | the plan is checked | the plan is blocked | FR-10 |
| AC-17 | actual and budget for a line | the variance is computed | operating effect plus FX effect equals total variance | FR-11 |
| AC-18 | one failed gate | the close runs | the status is `blocked` and the reason names the gate | FR-12 |
| AC-19 | the same inputs | the close is run twice | both runs have the same input hash and the same output hash | FR-12 |
| AC-20 | `NORDIC_CLOSE_API_KEY` set and no header | any protected route is called | the response is 401 | NFR-6 |

## 5. Edge cases

| ID | Case | Expected behaviour |
|---|---|---|
| EC-1 | A period with no journals for one entity | The entity is reported as having no activity; the close continues. |
| EC-2 | The presentation currency equals an entity currency | The rate is exactly 1 and no translation reserve arises. |
| EC-3 | Rates are missing for one day inside a month | The average uses the days that exist; the gate reports the number of days used. |
| EC-4 | A flux line with a zero prior balance | The line is treated as appearing; percentage change is shown as n/a. |
| EC-5 | A reconciliation where opening balances differ | The proof does not close to zero; the item is reported as an opening difference and blocks sign-off. |
| EC-6 | Two bank lines with the same amount and reference | Each statement line is matched at most once. |
| EC-7 | An export requested for a period with no data | The API returns 404. |
| EC-8 | A request for a period in the wrong format | The API returns 422. |

## 6. Data model

See `docs/schema.md` for the full model and the ER diagram. Core entities: entity, group
account, account mapping, journal, journal line, FX rate, budget line, bank statement line,
explanation, close task, close run, audit log.

## 7. API contract

See `docs/api.md`.

## 8. Out of scope

| Item | Reason |
|---|---|
| ERP connectors | The input contract is CSV; connectors are a separate piece of work. |
| Multi-user login and roles | The tool is single-tenant. An optional API key is the only access control. |
| Cash flow statement | Needs a cash-flow classification of every account; listed on the roadmap. |
| Non-controlling interests, equity method, step acquisitions | All subsidiaries are 100% owned. |
| Deferred tax, hyperinflation, hedge accounting | Not needed for the Nordic currencies. |
| Revaluation of foreign-currency monetary items in the entity books | Entity ledgers are taken as booked. The effect on intercompany balances is shown as a separate residual line. |
| Elimination of unrealised profit in inventory | The sample data assumes intercompany goods are sold on in the same month. |
