# Production use cases

`neural-mesh` is most useful when disagreement itself is evidence and a system must be allowed to
abstain. It should sit inside an application policy boundary, not replace factual verification,
authorization, or human accountability.

## 1. Model or provider migration gate

Record representative responses from the current and proposed provider/model set. Keep the accepted
report as an immutable baseline, then replay and compare before changing production routing.

Gate on case pass rate, agreement, provider failures, p95 duration, cost completeness, cost delta,
and regressed case count. Tag cases by capability or customer journey so a failed report identifies
where to investigate without exposing content. This catches wording/quorum drift and operational
regressions; it does not prove the new answers are true.

```bash
neural-mesh evaluate migration-candidate.json --output candidate.json
neural-mesh compare baseline.json candidate.json \
  --max-agreement-drop 0.02 \
  --max-cost-increase-usd 0.10 \
  --max-p95-duration-increase-ms 250 \
  --max-regressed-cases 0
```

## 2. Structured policy decision contract

Use a council where the application expects a JSON object but will execute no action until its own
schema validation and authorization pass. `required_json_keys` catches missing contract fields;
`forbidden_substrings` catches known prohibited disclosure/action language; quorum absence routes to
manual review.

The checked-in fixture is executable:

```bash
neural-mesh evaluate examples/structured_policy_replay.json
```

This is appropriate for triage, classification, or recommendation boundaries. It is not sufficient
for access control: required keys say nothing about value types, allowed values, signatures,
permissions, or business invariants. Validate those downstream with a real schema and policy engine.

## 3. Reliability and disagreement monitoring

Attach a `ConsensusObserver` and aggregate only bounded event attributes. Alert on increasing
`neural_mesh.providers.timeouts`, invalid responses, weak/insufficient agreement, incomplete usage,
or persistence failures. Use the task digest only when its privacy/cardinality tradeoff is acceptable.

The observer event is an operational signal, not a transcript. Keep prompt/response diagnostics in a
separate, explicitly authorized system if they are needed at all. A spike in agreement can also be
suspicious when providers share training data, routing infrastructure, or copied output.

## 4. Support and incident-response recommendation

Ask independent providers for a bounded recommendation, require known safety language (for example,
human escalation or idempotency), forbid dangerous instructions (such as unbounded retry), and expose
all provider outcomes to the operator. The application should render “no quorum” distinctly from a
provider outage and from a policy assertion failure.

The checked-in support fixture demonstrates this workflow without credentials:

```bash
neural-mesh evaluate examples/support_policy_replay.json
```

## Deployment checklist

- Define who owns prompts, recorded datasets, baselines, scorer policy, provider configuration, and
  human escalation.
- Authorize provider/model selection server-side; never accept it directly from an untrusted client.
- Configure outer council bounds and SDK-level retries/timeouts together, then load-test worst-case
  fan-out and cost.
- Treat replay suites as sensitive source data and reports/events as pseudonymous operational data.
- Require cost completeness when cost is a gate; missing cost is not zero.
- Exercise no-quorum, partial outage, cancellation, malformed response, scorer failure, observer
  timeout, usage-lock timeout, and disk-full behavior.
- Pin and verify the exact wheel plus the consumer contract in the consuming repository.
- Preserve a rollback version, baseline artifact, and provider-routing configuration.

## Poor fits

Do not use majority agreement alone to diagnose, prescribe, approve payments, make employment or
credit decisions, control physical systems, or authorize irreversible actions. Correlated models can
agree confidently and be wrong. These domains need independent evidence, qualified review,
regulatory controls, and application-specific safety cases.
