# Release checklist

Run these from a clean checkout before tagging.

| Check | Command | Expect |
|---|---|---|
| Lint | `ruff check .` | no findings |
| Tests and coverage | `pytest` | all pass, coverage at least 85% |
| Dependencies | `pip-audit --skip-editable` | no known vulnerabilities |
| Sample close | `nordic-close init --force && nordic-close close --period 2026-09` | exit code 0 |
| Blocked case | `nordic-close close --period 2026-10` | exit code 4 with two failing gates (intercompany, bank) |
| Secrets | search the tree for keys, tokens and `.env` files | none committed |
| Changelog | `CHANGELOG.md` has an entry for the version | yes |
| Version | `pyproject.toml` and `src/nordic_close/__init__.py` agree | yes |

Then tag the commit (`git tag v0.1.0`) and push the tag.
