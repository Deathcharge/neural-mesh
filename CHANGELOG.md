# Changelog

All notable changes are recorded here. This project follows semantic versioning after the first
independently installable release.

## Unreleased

### Added

- Replayable, credential-free evaluation suites with deterministic built-in scorers.
- Privacy-minimized, versioned evaluation reports and configurable quality, reliability, latency,
  cost, and cost-completeness gates.
- Baseline comparison with case-level and aggregate regression budgets.
- `neural-mesh evaluate` and `neural-mesh compare` commands with CI-friendly exit codes.
- Opt-in, bounded consensus observers with privacy-minimized OpenTelemetry-compatible event
  attributes and isolated sink failures.

## 0.2.0 - 2026-07-28

### Added

- Provider-agnostic async consensus engine with explicit request bounds.
- Deterministic normalized-token agreement clustering and quorum handling.
- Structured success, error, invalid-response, and timeout outcomes.
- Optional privacy-minimized bounded JSONL usage persistence.
- Typed public package, runnable offline example, tests, and CI/release verification.
- Installed `neural-mesh` CLI with version reporting and a credential-free demo.
- Security policy and trusted-publishing release workflow.

### Changed

- Replaced the private `helix-unified` provider dependency with an injectable provider protocol.
- Corrected consensus semantics: successful calls no longer count as agreement.
- Consolidated package metadata in `pyproject.toml` and removed unrelated runtime dependencies.
- Set the supported Python range to 3.10 through 3.13.
- Updated current company, support, package, and license identity to Samsarix LLC.

### Fixed

- Classify `asyncio.wait_for` timeouts correctly on Python 3.10 as well as newer Python versions.

### Removed

- Nonfunctional provider-specific compatibility clients whose API keys were ignored.
- Undocumented writes to `Helix/state/consensus_usage.jsonl`.

## 0.1.0 - 2026-06-16

- Initial source extraction from `helix-unified`; not independently installable.
