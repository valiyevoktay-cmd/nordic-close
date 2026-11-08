# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-08

First release.

### Added

- Loader for ledgers, mappings, budget, bank statements and close plan with all-or-nothing
  validation.
- Exchange rates from the ECB with cross rates, monthly average and closing rate.
- IAS 21 translation with an independent proof of the translation reserve.
- Intercompany comparison in EUR, suggested journals and eliminations.
- Three-pass bank reconciliation with a zero-difference proof.
- Flux review with explanation linting.
- Close calendar with critical path, slack and reviewer separation.
- Eight close gates, stored runs with input and output hashes, audit log and period lock.
- Income statement, balance sheet, KPIs, entity view and budget variance.
- REST API, command line, browser view and Excel reporting pack.
- Sample data for four entities and ten months of 2026.
