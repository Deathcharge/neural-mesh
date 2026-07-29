# Contributing to neural-mesh

Thank you for helping improve the standalone consensus library. Changes should keep the core small,
provider-agnostic, dependency-light, and honest about what textual agreement can establish.

## Setup

Use Python 3.10 through 3.13 in a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate  # Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e . -r requirements-dev.txt
```

## Required checks

Run these commands before opening a pull request:

```bash
python -m ruff format --check neural_mesh tests examples
python -m ruff check neural_mesh tests examples
python -m mypy neural_mesh tests examples
python -m pytest
python -m build
python -m twine check dist/*
```

Tests enforce at least 95% branch-aware coverage. Add focused tests for success, no-quorum, timeout,
error, cancellation, input-boundary, privacy, or packaging behavior changed by your patch.

## Design rules

- Keep provider SDKs and credentials outside the core package. Optional maintained adapters need a
  demonstrated user need and should be isolated extras.
- Validate request limits before starting provider calls.
- Never swallow caller cancellation or log raw prompts, responses, credentials, or external exception
  text.
- Do not add automatic retries without explicit attempt, time, and cost budgets.
- Preserve the distinction between provider success, textual agreement, and factual correctness.
- Keep public types fully annotated and update the offline example when the primary journey changes.
- Treat `neural_mesh.multi_ai_consensus` as a compatibility import; new APIs belong in the package root
  and must be called out in `CHANGELOG.md`.
- Avoid new runtime dependencies unless the value clearly outweighs install, security, and maintenance
  cost.

## Pull requests

Keep changes focused and explain:

1. the user problem and expected behavior;
2. compatibility or migration impact;
3. security, privacy, reliability, and provider-cost effects;
4. exact verification commands and outcomes;
5. documentation updated with the implementation.

Use conventional commit prefixes such as `feat:`, `fix:`, `docs:`, `test:`, and `chore:` when practical.
Do not commit credentials, `.env` files, generated distributions, coverage output, or local usage logs.

## Reporting issues

Public bug reports should include the Python version, platform, minimal provider adapter, configuration,
expected result, and redacted outcome. Never paste API keys, private prompts, model responses, or raw
provider exceptions.

For a suspected vulnerability, follow [SECURITY.md](SECURITY.md) and email
`support@samsarix.com`. Avoid a public exploit report until Samsarix has acknowledged it.

## License-sensitive changes

`Samsarix LLC` is the current licensor and package author. Do not modify licensing terms, package
ownership, trademarks, or publication configuration without explicit owner approval. Branding-only
changes must still keep package metadata, `LICENSE`, README, and release documentation synchronized.
