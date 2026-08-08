# Replay evaluation and regression gates

`neural-mesh` turns recorded multi-provider responses into a repeatable experiment and CI gate. This
is useful when changing prompts, providers, routing, quorum thresholds, or application policy: the
same cases replay without API keys, network variability, or model charges.

## Run a suite

```bash
neural-mesh evaluate examples/support_policy_replay.json --output candidate.json
```

The input uses schema `neural-mesh-replay-suite/v1`. Each case defines an id, task, prompt, optional
exact text, required/forbidden substrings, required top-level JSON keys, tags, and at least two
recorded provider responses. A structured response can include model, input/output tokens, and cost;
a string is shorthand for just the response content.

Suite policy can require a minimum case pass rate and mean agreement ratio; maximum provider failure
rate, p95 duration, and total reported cost; and complete cost reporting so missing cost cannot
silently pass a budget. Execution is bounded by `max_cases`, `max_case_concurrency`, and
`max_total_provider_calls`, in addition to the consensus engine limits. The loader rejects suite files
over 50 MB and reports over 100 MB.

## Compare with a baseline

Keep an accepted report as an immutable build artifact or reviewed repository fixture, then run:

```bash
neural-mesh compare baseline.json candidate.json \
  --max-pass-rate-drop 0 \
  --max-agreement-drop 0.02 \
  --max-failure-rate-increase 0 \
  --max-cost-increase-usd 0.10 \
  --max-p95-duration-increase-ms 250 \
  --max-regressed-cases 0
```

Comparison requires the same case ids. It reports regressed and improved cases plus every exceeded
budget. Cost comparison fails closed as unverifiable when either report has incomplete cost data.

| Exit | Meaning |
| ---: | --- |
| `0` | Valid report or comparison; all configured gates passed. |
| `1` | Valid report or comparison; one or more gates failed. |
| `2` | Invalid input, unsupported schema, or operational error. |

## Privacy boundary

Reports use schema `neural-mesh-evaluation/v1`. They contain case/task/tag identifiers, scorer
results, timings, provider outcome counts, agreement, reported aggregate cost, completeness flags,
and a SHA-256 digest of selected consensus text. They omit prompts, responses, provider exception
messages, and model identifiers.

Replay suites are not privacy-minimized: they contain prompts, expected answers, and recorded model
outputs. Treat them as sensitive source data, review them before committing, and prefer synthetic or
redacted fixtures. A consensus digest is pseudonymous, not anonymous; predictable answers can be
guessed and hashed. Reports still need appropriate access control.

Agreement is a consistency signal, not factual correctness. Exact-match and substring scorers are
deterministic assertions, not semantic or safety judges. High-stakes use requires independent domain
validation and human escalation rules.

## Python API

For live or application-defined evaluation, construct `EvaluationRunner` with a `ConsensusEngine`,
optional `Scorer` implementations, `EvaluationPolicy`, and `EvaluationRunConfig`. Custom scorers
receive an `EvaluationCase` and `ConsensusResult` and return an `EvaluationScore`. Exceptions and
malformed scorer results are isolated as a redacted `scorer_error`.

Built-in scorers are `ConsensusReachedScorer`, `ExactMatchScorer`, `ContainsScorer`,
`ExcludesScorer`, and `JsonObjectScorer`. The last two enforce forbidden text and valid JSON objects
with required top-level keys without invoking a judge model. Report and replay schemas are versioned;
readers reject unknown versions rather than guessing at compatibility. Preserve accepted baselines
immutably and regenerate them only through review.
