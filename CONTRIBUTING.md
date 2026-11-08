# Working on this repository

## Getting started

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
nordic-close init
pytest
```

A good first read is `docs/spec.md` (what the system must do and which test proves it), then
`docs/architecture.md` (how the modules fit together). Every test that implements an
acceptance criterion has its ID in a comment, for example `# AC-11`.

## Where things are

- `src/nordic_close/` holds the package. Business rules live in `translate`, `intercompany`,
  `consolidate`, `reports` and `controls/`. `api`, `cli` and `web/` only deliver results.
- `data/sample/` is the synthetic dataset. Regenerate it with
  `python scripts/generate_sample_data.py` and `python scripts/write_explanations.py`; the
  generator is seeded, so the output is stable.
- `tests/conftest.py` has helpers for building a tiny dataset in a temporary folder, which is
  the easiest way to test a new rule.

## Rules for changes

1. Write or update the requirement in `docs/spec.md` first if behaviour changes.
2. Add a failing test, then the change. For anything that touches the maths, use a case you
   can calculate by hand.
3. Money is `Decimal` in code and integer minor units in storage. No floats.
4. Run `ruff check .` and `pytest` before committing. Coverage must stay at or above 85%.
5. Use conventional commit messages (`feat:`, `fix:`, `test:`, `docs:`, `chore:`, `ci:`).
6. Add a line to `CHANGELOG.md` under Unreleased.

## Release checklist

See `docs/release.md`.
