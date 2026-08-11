# Competitive landscape and product wedge

Research checked on August 8, 2026. This is product planning, not a claim of feature parity.

## Table stakes established by current tools

- [Braintrust](https://www.braintrust.dev/docs/evaluate) structures evaluation around a dataset,
  task, and scorers; experiments are immutable and intended for comparison and CI.
- [LangSmith](https://docs.langchain.com/langsmith/evaluation) supports offline and online
  evaluation, datasets, evaluators, experiments, and baseline analysis.
- [DeepEval](https://deepeval.com/docs/introduction) emphasizes local-first pytest integration,
  datasets/goldens, CI, and a large metric catalog.
- [Promptfoo](https://www.promptfoo.dev/docs/configuration/expected-outputs/) exposes configurable
  assertions, thresholds, and named metrics for repeatable prompt/model tests.
- [LiteLLM](https://docs.litellm.ai/) focuses on a unified provider interface plus routing, proxy,
  cost, budgets, and observability across model providers.
- [OpenTelemetry semantic conventions](https://opentelemetry.io/docs/specs/semconv/) provide a
  vendor-neutral vocabulary for interoperable telemetry. GenAI conventions evolve in the dedicated
  [semantic-conventions repository](https://github.com/open-telemetry/semantic-conventions/releases).

## neural-mesh wedge

The credible niche is not another general-purpose LLM evaluation platform or provider proxy. It is a
small, dependency-free council primitive plus a reproducible release gate where disagreement,
abstention, provider failure, cost completeness, and bounded execution are first-class outcomes.

```text
recorded responses -> deterministic council -> scorers -> privacy-minimized artifact
                   -> policy gates -> baseline regression decision
```

Differentiation to preserve:

- no hidden model call, telemetry, credential loading, persistence, or retry;
- explicit quorum/abstention instead of always synthesizing an answer;
- every selected provider represented as success, timeout, invalid response, or redacted error;
- fail-closed cost completeness and hard total-call/concurrency/input bounds;
- credential-free replay in local development and untrusted CI; and
- stable artifacts that omit prompt and response content by default.

## Priority sequence

1. Replay suites, scorers, immutable reports, policy gates, and baseline comparisons.
2. Dependency-free event hooks with an OpenTelemetry-compatible attribute mapping.
3. Maintained adapter examples for common provider SDKs and a consumer contract fixture.
4. Remote/shared persistence adapters and archive export tooling beyond local coordinated rotation.
5. Pluggable semantic similarity and judge scorers with explicit trust, cost, and prompt-disclosure
   boundaries.
6. Only after evidence: multi-round review/debate and optional synthesis strategies.

This sequence keeps provider routing and hosted experiment management outside the core; those markets
already have mature offerings. Integrate with them through stable events and artifacts instead of
recreating their control planes.
