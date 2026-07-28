# Changelog

All notable changes are recorded here. This project follows semantic versioning after the first
independently installable release.

## 0.2.0 - 2026-07-28

### Added

- Provider-agnostic async consensus engine with explicit request bounds.
- Deterministic normalized-token agreement clustering and quorum handling.
- Structured success, error, invalid-response, and timeout outcomes.
- Optional privacy-minimized bounded JSONL usage persistence.
- Typed public package, runnable offline example, tests, and CI/release verification.

### Changed

- Replaced the private `helix-unified` provider dependency with an injectable provider protocol.
- Corrected consensus semantics: successful calls no longer count as agreement.
- Consolidated package metadata in `pyproject.toml` and removed unrelated runtime dependencies.
- Set the supported Python range to 3.10 through 3.13.

### Removed

- Nonfunctional provider-specific compatibility clients whose API keys were ignored.
- Undocumented writes to `Helix/state/consensus_usage.jsonl`.

## 0.1.0 - 2026-06-16

- Initial source extraction from `helix-unified`; not independently installable.
