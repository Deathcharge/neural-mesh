# neural-mesh roadmap

This roadmap separates four gates: merge, release, publication, and flagship adoption. Passing one does not imply the next.

## Product boundary

Portfolio role: **reusable library or sdk**. Keep this as a small, independently versioned package. Samsarix Unified should consume it only through a public API adapter; private monorepo imports and copied implementations are out of scope.

Current disposition: the productized 0.2.0 baseline is on `main`. The next milestone is a competitive
evaluation and operations release; PyPI publication remains a separate owner-controlled gate.

## Competitive evaluation release

- Replayable suites with recorded multi-provider responses and deterministic expectations.
- Versioned, privacy-minimized reports with quality, agreement, provider-failure, latency, cost, and
  cost-completeness gates.
- Baseline comparison with aggregate and case-level regression budgets suitable for CI.
- Next: stable event hooks and OpenTelemetry-compatible GenAI attributes without a runtime telemetry
  dependency.
- Next: maintained provider adapter examples and a consumer-owned compatibility fixture.
- Next: safer multi-process persistence and operational runbooks for support, release validation,
  policy review, and model/router migration use cases.

## Stabilize the productized default

- Keep the default branch buildable from a clean checkout and preserve exact-head CI evidence.
- Keep Samsarix LLC branding, package identity, license metadata, and compatibility aliases internally consistent.
- Preserve the pre-productization default under a rollback ref before merging; do not delete legacy history.
- Review priority: validate the evaluation contract in one real consumer, then approve the BSL
  threshold, configure protected PyPI publishing, and validate a signed pre-release.

## Release candidate

- Build and install the wheel in a clean environment.
- Prove one real consumer and a versioned compatibility fixture.
- Publish only after package-name ownership, licensing, provenance, and rollback are recorded.

Current hardening backlog:

- No PyPI reservation/release, immutable tag, external adopter, or live provider integration evidence.
- Text-token Jaccard is explainable but semantically weak and can amplify correlated model error.
- Custom BSL/commercial threshold may make library adoption and package-index expectations unclear.
- Provider adapters can defeat cancellation, under-report cost, retry internally, or mishandle sensitive prompts.
- Local JSONL persistence now has bounded OS-level coordination and opt-in rotation; a remote/shared
  storage adapter and archive export remain future work. Provider names and task text remain
  intentionally unavailable for richer diagnostics.

## Samsarix adoption

- Define a public API, event, schema, artifact, or deployment contract before connecting to Samsarix Unified.
- Add a consumer-owned contract fixture covering authentication, privacy, limits, errors, and version compatibility.
- Make one implementation canonical; remove or freeze duplicate behavior only after parity and rollback are proven.
- Record an owner, support level, compatibility window, and measurable adoption signal.

## Completion evidence

A milestone is complete only when its exact commit, commands and results, artifact digest, consumer or deployment, and rollback path are recorded in a pull request or release record. README claims must not exceed that evidence.
