# Productization record

Last updated: 2026-07-28

## Repository assessment

The repository was extracted from `helix-unified` at commit `ae6698b`. At the audited baseline it contained one 541-line Python module plus generic packaging and documentation templates. Its actual intent was multi-provider LLM response collection and consensus analysis. It was not a distributed neural-network topology, hosted service, or complete Helix application.

At the audited baseline (`4c3350577b09ceeda616528367f3e12973b9e2c2`):

- the worktree was clean on `main`, tracking `origin/main`;
- all nine non-Git files were reviewed, as were all five commits and locally visible branch refs;
- `neural_mesh.multi_ai_consensus` imported an undeclared `apps.backend.services.unified_llm` module from a sibling `helix-unified` checkout;
- the package declared an unavailable `helix-hub-shared` dependency;
- a wheel build reported success but included only metadata and no `neural_mesh` code;
- “consensus” measured the number of successful provider calls, not agreement between responses;
- the default constructor created `Helix/state/consensus_usage.jsonl` as an import-time integration assumption;
- the dependency manifests contained a broad 2023 application stack unrelated to this library;
- no tests, CI workflow, examples, package initializer, changelog, or real supplementary docs existed;
- README and contribution commands pointed to missing files and claimed absent CI, docs, examples, an MIT license, and production readiness.

## Chosen product

`neural-mesh` will be a dependency-light Python library for bounded, auditable multi-provider LLM councils. A developer supplies async provider adapters; the library validates a request budget, invokes them concurrently with per-provider timeouts and a concurrency cap, returns structured outcomes, groups textually similar answers using a documented deterministic algorithm, and reports agreement without claiming that agreement proves correctness.

The smallest useful wedge is orchestration and transparent agreement analysis, not another provider SDK. Existing tools such as LiteLLM already solve broad provider normalization, while council applications commonly add peer review and a chairman model. This package should compose with either approach by keeping adapters injectable and the core offline-testable.

### Target user and primary use case

The target user is a Python developer building an evaluation, decision-support, or quality-gating workflow that wants to compare several independently configured LLM calls without coupling application logic to a private Helix service.

Primary journey:

1. install the package;
2. wrap two or more async provider callables;
3. configure explicit provider, token, prompt-size, concurrency, timeout, and agreement limits;
4. call the council with a task label and prompt;
5. inspect structured successes, timeouts, failures, the winning agreement cluster, and usage totals;
6. optionally persist privacy-minimized JSONL usage summaries;
7. handle “insufficient responses” or “no quorum” without a fabricated answer.

### Independent reason to exist

The package is a small composable primitive rather than a copy of `helix-unified`: it owns no UI, accounts, billing, provider credentials, database, agent framework, or deployment. It can be evaluated entirely with deterministic local adapters and embedded into unrelated Python systems.

### Deliberate non-goals

- proving factual correctness or calibrated epistemic confidence;
- built-in vendor SDKs, credential loading, or provider-specific pricing tables;
- a web UI, hosted API, authentication, subscriptions, or cloud infrastructure;
- multi-round debate, model-as-judge synthesis, retrieval, or agent execution;
- automatic retries, which can amplify paid calls unless a provider adapter intentionally implements them;
- telemetry or persistence by default.

## Key product and architecture decisions

- Public types and a small provider protocol form the API; provider adapters remain application-owned.
- Runtime code uses only the Python standard library.
- Provider names are unique and fan-out is capped before any call starts.
- Prompt characters, output tokens per provider, provider count, concurrency, and time are bounded.
- Caller cancellation propagates and cancels pending provider tasks.
- Exception values are not returned or logged verbatim; outcomes expose stable error categories.
- Agreement uses normalized-token Jaccard similarity and deterministic complete-link clusters. The result exposes both cluster share and within-cluster similarity.
- A consensus answer is returned only when the configured minimum cluster size and ratio are met.
- Agreement is explicitly documented as a reproducibility/consistency signal, not a truth guarantee.
- Usage persistence is opt-in and stores a SHA-256 task identifier, aggregate counters, and timing/cost metadata, not prompts or response text.
- Modern `pyproject.toml` metadata is authoritative; legacy `setup.py` will be removed.
- The checked-in license text remains unchanged. Package metadata and docs will stop claiming MIT; publication remains blocked on owner/legal confirmation because the file names a different licensed work and contains custom terms.

## Assumptions

- Provider adapters can return their model name, token totals, and cost when known; unknown usage remains explicit rather than estimated by the library.
- A developer who passes a provider adapter intends to authorize calls to that provider.
- Text similarity is useful for short, directly comparable answers; custom semantic or judge-based aggregation may be added later as a pluggable strategy.
- Python 3.10+ is an acceptable compatibility floor. The original `>=3.9` claim was already inconsistent with its use of PEP 604 union syntax, and Python 3.9 is end-of-life.

## Baseline command results

Commands were run on Windows with Python 3.11.9 before product changes.

| Command | Actual baseline result |
| --- | --- |
| `python -m venv %TEMP%\neural-mesh-baseline-4c3350577b09\venv` | Passed. |
| `<venv-python> -m pip install -r requirements.txt` | Failed: `anthropic==0.7.10` has no matching distribution. |
| `<venv-python> -m pip install --dry-run .` | Failed: no distribution exists for `helix-hub-shared>=0.1.0`. |
| `python -c "import neural_mesh.multi_ai_consensus"` | Passed only because the developer environment resolved `apps.backend.services.unified_llm` from a sibling `helix-unified` checkout; this is not standalone behavior. |
| `pytest tests/ -v --cov=src` | Failed: `tests/` does not exist; zero tests collected. |
| `python -m black --check src tests` | Failed: `src` does not exist. |
| `python -m mypy src` | Failed: `src` does not exist. |
| `python -m compileall -q neural_mesh` | Passed syntax compilation. |
| `python -m build` | Exited 0 with deprecation warnings, but the wheel contained only `.dist-info` metadata and no runtime package. |

There was no start command, service entry point, or deployment configuration to run.

## Findings and priorities

### P0 — release and primary-journey blockers

- [x] Remove the private cross-repository import and unavailable package dependency.
- [x] Ship importable package code in sdist and wheel artifacts.
- [x] Replace success-count labeling with actual, transparent agreement analysis.
- [x] Provide a complete provider-adapter journey with deterministic local examples.
- [x] Add tests for the primary journey, failures, timeouts, cancellation, validation, and distribution shape.
- [x] Replace false installation, CI, docs, maturity, and license claims.

### P1 — serious usefulness, reliability, security, and maintenance gaps

- [x] Bound provider count, prompt size, token requests, concurrency, and timeout.
- [x] Propagate cancellation and clean up pending tasks.
- [x] Redact provider exceptions and make stable error contracts.
- [x] Make persistence opt-in, privacy-minimized, streaming, and tolerant of malformed records.
- [x] Add deterministic lint, type-check, test, build, and artifact-smoke commands in CI.
- [x] Consolidate package metadata and remove unrelated dependencies/tooling.
- [x] Document trust boundaries, operating-cost formula, limitations, and no-retry behavior.

### P2 — valuable follow-on work

- [ ] Pluggable similarity/aggregation strategies, including embeddings or an explicitly configured judge.
- [ ] Maintained optional adapters for major provider routers, only if users validate demand.
- [ ] Cross-process locking/rotation for high-volume JSONL persistence.
- [ ] Benchmarks and property-based testing for clustering stability.
- [ ] Signed releases and package publication automation after owner approval.

## Implementation checklist

- [x] Protect the clean worktree and create a productization branch.
- [x] Inventory and review every repository file and local commit.
- [x] Run and record baseline commands.
- [x] Complete a sealed repository-wide security scan with explicit coverage.
- [x] Perform bounded current research on comparable councils, provider abstraction, packaging, and debate limitations.
- [x] Implement the bounded standalone library and compatibility import.
- [x] Add runnable offline examples and comprehensive tests.
- [x] Modernize packaging and dependency declarations.
- [x] Add CI and dependency maintenance configuration.
- [x] Rewrite README and contribution guidance.
- [x] Build and inspect both distribution artifacts from a clean environment.
- [x] Install the wheel outside the source tree and run the documented example.
- [x] Perform adversarial final review and close locally actionable findings.

## Release acceptance criteria

- a fresh Python 3.10+ environment installs the wheel without private or undeclared dependencies;
- the public package import works outside the repository;
- the offline example reproduces the documented primary journey;
- no provider call starts for invalid or over-budget configuration;
- timeouts, ordinary provider errors, no quorum, insufficient responses, and cancellation are covered by tests;
- persisted usage contains no prompt, response, exception text, or credential;
- lint, formatting, type checking, tests with coverage, build, metadata validation, and wheel smoke installation pass;
- the wheel and sdist contain the package, typing marker, README, and license;
- CI executes meaningful checks on supported Python versions and Windows;
- documentation states current maturity and license/publication blockers honestly.

## Security, privacy, reliability, and cost record

The baseline standard security scan reviewed all nine files and produced no reportable vulnerability. It rejected a resource/cost candidate because this repository has no lower-privileged ingress, and suppressed a raw-exception logging candidate because sensitive exception contents and an untrusted log reader were not evidenced. Both are retained as P1 product hardening because downstream services could create those boundaries.

The implemented cost ceiling is structural rather than price-table based: at most `max_providers × max_tokens_per_provider` output tokens can be requested per council run, plus a bounded prompt sent once to each selected provider. Actual currency cost is the sum of adapter-reported costs; when adapters cannot report cost, the result says it is incomplete. The library performs zero automatic retries.

The final diff-focused security review read all 21 changed or directly supporting files. It found and closed two hardening gaps during review: GitHub Actions are now pinned to immutable revisions with checkout credential persistence disabled, and usage files use descriptor-based regular-file/symlink checks with owner-only creation mode. No technically plausible reportable candidate survived, so validation and attack-path phases were not applicable.

## Final verification evidence

Verification was run from a clean Python 3.11 environment using the pinned top-level tools in `requirements-dev.txt`. Because this Windows installation's normal `venv` bootstrap hung inside `ensurepip`, the successful clean environment was created with `--without-pip` and populated through pip's `--python` target mode.

| Check | Final result |
| --- | --- |
| Ruff format and lint | Passed with Ruff 0.9.10. |
| Strict mypy | Passed with mypy 1.14.1 across all eight source/test/example files. |
| Pytest with branch coverage | 38 passed, 1 Windows capability skip; 97.71% coverage, above the 95% gate. |
| Dependency consistency | No broken requirements in the clean development environment or either wheel-smoke environment. |
| YAML configuration | Both CI and Dependabot files parsed successfully. |
| Isolated distribution build | Built `neural_mesh-0.2.0.tar.gz` and `neural_mesh-0.2.0-py3-none-any.whl` from the sdist. |
| Metadata validation | Twine passed both artifacts; metadata declares Python 3.10+, the custom license expression, and no required runtime dependency. |
| Artifact inspection | Wheel contains all five runtime/typing files and the license; sdist also contains tests, example, changelog, contributor guide, and productization record. |
| External wheel smoke | Clean Python 3.11 and 3.13 environments imported version 0.2.0 from `site-packages` and ran the offline example with the expected 2-of-3 moderate agreement. |

The GitHub Actions matrix itself was authored and statically validated but was not executed on GitHub from this local workspace. Python 3.10 and 3.12 therefore remain CI-enforced compatibility claims rather than locally executed evidence in this audit.

## Distribution and sustainability

The realistic distribution path is a pure-Python wheel and source distribution built from GitHub Actions, then published to a Python package index only after the owner confirms the package name, license terms, and release credentials. The core should remain small and dependency-free. Sustainability can come from paid support, integration work, or commercially licensed Helix offerings under owner-approved terms; the library should not invent subscriptions or usage billing.

## Owner-, legal-, credential-, and production-blocked work

- Confirm that the repository `LICENSE` intentionally applies to `neural-mesh`; it currently names “Helix Licensing System,” uses “Helix Collective” as licensor, and contains custom production-use thresholds.
- Confirm ownership of the `neural-mesh` package name on the intended package index.
- Supply package-index trusted-publishing configuration or release credentials.
- Decide whether public release tags should be signed and which maintainers may publish.

## Known risks

- Textual similarity can miss semantically equivalent answers and can group shared wording around a wrong answer.
- Provider-reported token and cost metadata may be absent or inaccurate.
- A provider coroutine that suppresses `asyncio` cancellation can outlive the configured timeout.
- A downstream application can still create privacy, authorization, rate-limit, or spend risks if it forwards untrusted input or configures unsafe adapters.
- Hashed task labels are pseudonymous rather than anonymous and may be guessable when labels have low entropy.
- JSONL rotation and coordination across store instances or processes remain application responsibilities.
- The product has not yet been validated with external users; “viable” means coherent and testable, not product-market fit.
