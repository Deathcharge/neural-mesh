# neural-mesh

`neural-mesh` is a Python library and CI toolkit for asking several application-supplied AI providers
the same question, measuring whether their responses agree, and preventing quality, reliability,
latency, or cost regressions. It bounds fan-out, concurrency, output tokens, prompt/response size,
provider latency, evaluation size, and total provider calls; returns every success, timeout, invalid
response, and redacted failure; and only selects a consensus answer when a configured textual quorum
exists.

It is for developers building evaluation, decision-support, or quality-gating workflows. It is not a
provider SDK, hosted Samsarix service, agent framework, or claim that majority agreement is factually
correct.

Status: **0.2.0 release candidate**. The core journey, tests, typing, CI, and distribution checks are
implemented. Samsarix LLC has confirmed the current company and license identity. Public package
publication is still gated on claiming the package name, configuring the protected PyPI trusted
publisher, and passing the hosted release workflow.

## Fastest successful path

Prerequisites: Python 3.10 through 3.13. The runtime has no third-party dependencies.

```bash
git clone https://github.com/Deathcharge/neural-mesh.git
cd neural-mesh
python -m venv .venv
```

Activate the environment:

```bash
# macOS/Linux
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1
```

Install and run the offline demo from the installed package:

```bash
python -m pip install .
neural-mesh --version
neural-mesh demo
```

`python -m neural_mesh demo` is equivalent. The demo uses deterministic local callables—no API keys,
network calls, or paid models. It returns three provider outcomes, groups the two matching retry
recommendations, and selects their answer with `moderate` agreement. The source-level
`examples/basic_consensus.py` shows the same adapter pattern in one file.

Run the checked-in, credential-free release gate:

```bash
neural-mesh evaluate examples/support_policy_replay.json --output report.json
neural-mesh compare baseline.json report.json
```

Both commands return `0` when gates pass, `1` when a valid result violates policy, and `2` for invalid
input or an operational error. The replay file contains recorded responses and expectations; the
generated report contains identifiers, scores, aggregate measurements, and a consensus digest, but
not prompts or response text. See [docs/EVALUATION.md](docs/EVALUATION.md) for the workflow and threat
model.

## Installed CLI

```text
usage: neural-mesh [-h] [--version] {demo,evaluate,compare} ...
```

The CLI never loads credentials or contacts a model provider. `demo` proves the installed artifact,
`evaluate` executes deterministic recorded provider responses, and `compare` enforces a regression
budget against an immutable baseline. Live provider calls remain explicit application code through
the Python API.

## Minimal integration

Adapters own provider SDKs, credentials, pricing, and any deliberate retry policy. A callable receives
the prompt and maximum output-token request, then returns either a string or `ProviderResponse`.

```python
import asyncio
from decimal import Decimal

from neural_mesh import (
    CallableProvider,
    ConsensusConfig,
    ConsensusEngine,
    ProviderResponse,
)


async def provider_a(prompt: str, max_tokens: int) -> ProviderResponse:
    # Replace this body with your configured provider SDK call.
    return ProviderResponse(
        content="Use an idempotency key and bounded backoff with jitter.",
        model="provider-a-model",
        input_tokens=14,
        output_tokens=10,
        cost_usd=Decimal("0.0021"),
    )


async def provider_b(prompt: str, max_tokens: int) -> str:
    return "Use an idempotency key and bounded backoff with jitter."


async def main() -> None:
    council = ConsensusEngine(
        [
            CallableProvider("provider-a", provider_a),
            CallableProvider("provider-b", provider_b),
        ],
        ConsensusConfig(
            max_providers=2,
            max_concurrency=2,
            timeout_seconds=20,
            max_tokens_per_provider=500,
        ),
    )
    result = await council.run("retry-policy", "How should this client retry?")

    if result.consensus_text is None:
        print("No textual quorum; inspect result.outcomes")
    else:
        print(result.agreement_level.value, result.consensus_text)


asyncio.run(main())
```

Provider classes may implement the same protocol directly:

```python
class MyProvider:
    @property
    def name(self) -> str:
        return "my-provider"

    async def complete(self, prompt: str, *, max_tokens: int) -> str:
        ...
```

Optional maintained adapters are available for the official OpenAI Responses and Anthropic Messages
SDKs:

```bash
python -m pip install "neural-mesh[providers]"
```

```python
from neural_mesh import AnthropicMessagesProvider, OpenAIResponsesProvider

providers = [
    OpenAIResponsesProvider("your-openai-model"),
    AnthropicMessagesProvider("your-anthropic-model"),
]
```

They load SDKs lazily, accept configured async clients through dependency injection, pass the engine's
output-token ceiling through to the official API, and report model/token metadata. They deliberately
do not hard-code mutable provider pricing. See [docs/PROVIDERS.md](docs/PROVIDERS.md).

## Result semantics

`ConsensusResult` separates call health from answer agreement:

| Field | Meaning |
| --- | --- |
| `outcomes` | One ordered `ProviderOutcome` per selected provider. |
| `providers_succeeded` | Providers that returned valid, non-empty, size-bounded text. |
| `agreement_level` | `unanimous`, `strong`, `moderate`, `weak`, `insufficient`, or `error`. |
| `agreement_ratio` | Winning cluster size divided by valid successful responses. |
| `average_similarity` | Mean pairwise Jaccard score inside the winning cluster; `0` for a singleton. |
| `winning_providers` | Providers in the deterministic winning cluster. |
| `consensus_text` | The cluster medoid response, or `None` when minimum cluster/ratio rules fail. |
| `reported_*_tokens` | Sum of reported token values; the completeness flag is true only when every selected provider completed and reported them. |
| `reported_cost_usd` | Sum of adapter-reported cost; the completeness flag is true only when every selected provider completed and reported it. |
| `usage_recorded` | Whether an optional usage store successfully recorded the run. |

Provider failures expose stable codes (`provider_error`, `timeout`, or `invalid_response`) instead of
raw exception text. The library never logs prompts, responses, exception messages, or credentials.
Caller cancellation propagates and pending provider tasks are cancelled.
Adapters must cooperate with `asyncio` cancellation. A provider coroutine that catches cancellation
and never returns can outlive the configured timeout; Python cannot forcibly terminate it. Network
timeouts and retry limits should also be configured inside the provider SDK or adapter.

`ConsensusEngine` is event-loop scoped. Its concurrency semaphore is shared across concurrent
`run()` calls on that engine, so request overlap cannot multiply the configured provider-call
concurrency. Create a separate engine per event loop rather than moving one engine between loops.

## How agreement is calculated

The default strategy is intentionally local, deterministic, explainable, and dependency-free:

1. normalize each valid response with Unicode NFKC and case folding;
2. extract unique Unicode word tokens;
3. calculate pairwise Jaccard similarity;
4. build complete-link clusters in provider order, so a response joins only when it meets the
   threshold against every existing cluster member;
5. choose the largest cluster, then highest within-cluster similarity, then earliest provider order;
6. select the cluster medoid only when minimum successful-response, cluster-size, and ratio rules pass.

This is a consistency signal. Paraphrases may score poorly; copied wording around a wrong answer may
score highly. Do not use agreement as factual confidence for medical, legal, financial, safety, or
other high-stakes decisions without independent validation. Research on multi-agent debate likewise
finds that outcomes depend on protocol and can be vulnerable to persuasion or convergence effects.

## Configuration and hard limits

| Setting | Default | Enforced range |
| --- | ---: | ---: |
| `max_providers` | 8 | 2–32 |
| `max_concurrency` | 4 | 1–32 |
| `timeout_seconds` | 30 | 0.01–600 |
| `max_tokens_per_provider` | 1,000 | 1–1,000,000 |
| `max_prompt_chars` | 100,000 | 1–1,000,000 |
| `max_response_chars` | 200,000 | 1–2,000,000 |
| `minimum_successful_responses` | 2 | 2–`max_providers` |
| `minimum_cluster_size` | 2 | 2–`max_providers` |
| `minimum_agreement_ratio` | 0.5 | 0–1 |
| `similarity_threshold` | 0.72 | 0–1 |

Invalid configuration, prompt, provider selection, or token overrides fail before any provider task
starts. `provider_names=` can choose a unique configured subset for a request; `max_tokens=` can only
lower the configured per-provider ceiling.

The maximum requested output volume for one run is:

```text
selected providers × requested max tokens per provider
```

The prompt is additionally sent once to each selected provider. Currency cost depends on provider
pricing and is therefore adapter-reported. No automatic retries occur, so one council run starts at
most one call per selected provider. If an adapter retries internally, its limits and cost belong to
that adapter and should be documented by the application.

For cancellation-cooperative adapters, the approximate worst-case provider-call time for one run is
`ceil(selected providers / max_concurrency) × timeout_seconds`, plus scheduling and local processing.
The timeout clock starts when a call acquires a concurrency slot, not while it is queued.

## Optional local usage history

Persistence is off by default. To opt into a bounded JSONL file:

```python
from neural_mesh import ConsensusEngine, JsonlUsageStore

store = JsonlUsageStore(
    "./state/neural-mesh-usage.jsonl",
    max_file_bytes=10_000_000,
    max_backup_files=3,
    lock_timeout_seconds=5,
)
council = ConsensusEngine(providers, usage_store=store)

result = await council.run("release-gate", prompt)
statistics = await store.statistics(max_records=50_000)
```

Records contain a SHA-256 task identifier, timestamp, agreement label, aggregate provider/token/cost
counters, completeness flags, and duration. They do not contain task text, prompts, responses,
provider exception text, provider names, or credentials. Writes are durable by default; file size and
record-read count are bounded. Malformed records are counted and skipped. Independent store instances
and processes coordinate through a permanent sibling `.lock` file with a bounded OS advisory lock.
Optional numbered rotation occurs under that lock; with the default `max_backup_files=0`, a full store
still fails closed. Statistics cover the active file only. The task hash is a stable pseudonymous
identifier, not anonymization: short or predictable task labels may be recoverable by guessing, so
the active, lock, and backup files should all be access-restricted. See
[docs/PERSISTENCE.md](docs/PERSISTENCE.md) for filesystem and recovery boundaries.

## Opt-in observability

Pass asynchronous observer sinks to `ConsensusEngine` to receive a privacy-minimized completion event
for each council run. The event exposes OpenTelemetry-compatible GenAI operation/workflow/token
attributes plus bounded `neural_mesh.*` agreement, provider-outcome, cost, and persistence fields.
The package does not install an observability SDK or configure global telemetry.

```python
class Observer:
    async def record(self, event):
        await my_sink.write(event.to_dict())


council = ConsensusEngine(providers, observers=[Observer()], observer_timeout_seconds=1.0)
```

Observer failures and timeouts are isolated from the consensus result; caller cancellation still
propagates. Events omit prompt/response content and provider/model names. See
[docs/OBSERVABILITY.md](docs/OBSERVABILITY.md) for the event schema, OpenTelemetry mapping, privacy,
and cardinality guidance.

## Architecture

- `neural_mesh.consensus`: public provider protocol, validation, async orchestration, outcomes,
  clustering, quorum, and usage-record creation.
- `neural_mesh.adapters`: optional official OpenAI Responses and Anthropic Messages SDK adapters.
- `neural_mesh.usage`: optional bounded JSONL store and streaming statistics.
- `neural_mesh.evaluation`: replay suites, deterministic scorers, privacy-minimized reports, gates,
  and baseline comparisons.
- `neural_mesh.observability`: application-owned completion-event protocol and standard-compatible
  attributes.
- `neural_mesh.cli`: installed demo, evaluation, and comparison commands.
- `neural_mesh.multi_ai_consensus`: compatibility aliases for the original extraction module path.
- `examples/basic_consensus.py`: credential-free end-to-end consensus path.
- `examples/support_policy_replay.json`: executable evaluation and CI-gate fixture.
- `contracts/consumer_contract_v1.json`: executable producer-side compatibility contract for a
  Samsarix consumer.
- `tests/`: behavior, failure, cancellation, persistence, privacy, typing, and public-import coverage.

The library intentionally does not import the legacy `helix-unified` or `helix-hub-shared` projects,
provider SDKs, dotenv, or application frameworks.

## Development and verification

Install the pinned top-level development toolchain:

```bash
python -m pip install -e . -r requirements-dev.txt
```

Run the same checks protected by CI:

```bash
python -m ruff format --check neural_mesh tests examples
python -m ruff check neural_mesh tests examples
python -m mypy neural_mesh tests examples
python -m pytest
python -m build
python -m twine check dist/*
```

`pytest` enforces branch-aware coverage of at least 95%. CI runs the checks on Python 3.10–3.13 and
also exercises Windows. The build job inspects the artifact, installs the wheel into a clean virtual
environment outside the checkout, imports the public package, and runs both installed CLI forms. The
separate release workflow repeats the gates, builds without publishing credentials, attests the
distributions in an isolated job, and publishes through a protected PyPI environment.

See [CONTRIBUTING.md](CONTRIBUTING.md) for change guidance,
[docs/EVALUATION.md](docs/EVALUATION.md) for evaluation adoption,
[docs/COMPETITIVE_LANDSCAPE.md](docs/COMPETITIVE_LANDSCAPE.md) for product positioning, and
[docs/PRODUCTIZATION.md](docs/PRODUCTIZATION.md) for baseline evidence. Maintainers should follow
[docs/RELEASING.md](docs/RELEASING.md).

## Security and privacy

- Keep API keys inside provider adapters or their SDK configuration; never put them in prompts or
  provider names.
- Treat prompts and responses as data disclosed to every selected provider according to that
  provider's terms and retention policy.
- Do not forward untrusted provider lists, token limits, storage paths, or adapter configuration from
  a public route without server-side authorization and tighter application limits.
- The package performs no telemetry, credential loading, networking of its own, or persistence by
  default.
- Report suspected vulnerabilities privately according to [SECURITY.md](SECURITY.md), normally at
  `support@samsarix.com`; do not include secrets or private prompt data in public issues.

The baseline repository-wide security review covered every file and found no reportable vulnerability.
It still drove bounded fan-out, error redaction, privacy-minimized opt-in storage, and explicit trust
boundary documentation in this release candidate.
The final diff-focused review covered all 21 changed or directly supporting files and found no
surviving reportable vulnerability after CI credential/supply-chain and usage-file hardening.

## Limitations and roadmap

- Text similarity is not semantic equivalence or truth validation.
- Provider-reported usage may be incomplete or inaccurate; completeness flags must be checked.
- Timeouts cannot forcibly terminate provider adapters that suppress `asyncio` cancellation.
- JSONL locking relies on local-filesystem OS advisory locks; network/distributed filesystems require
  a shared external store or lock proven for that filesystem.
- There are no maintained built-in provider/router adapters yet; application-owned adapters keep the
  first release independently testable and avoid forcing a provider stack.
- Multi-round debate, peer review, judge synthesis, and pluggable similarity strategies are possible
  follow-ons, not part of the first credible release.

## License, company, and publication status

`neural-mesh` is maintained by Samsarix LLC. General and commercial licensing inquiries go to
`contact@samsarix.com`; private security reports go to `support@samsarix.com`.

The checked-in [LICENSE](LICENSE) contains custom Business Source License 1.1 terms for `neural-mesh`.
It permits the uses described there, applies a commercial-license or fee requirement above the stated
production-use threshold, and changes to Apache License 2.0 on June 16, 2027. It is not an MIT or
present-day OSI-approved open-source license. Package metadata uses
`LicenseRef-Samsarix-BSL-1.1` so distribution metadata does not make a false license claim.

The release workflow is ready for PyPI trusted publishing but cannot reserve the package name or
configure external accounts. Follow [docs/RELEASING.md](docs/RELEASING.md) before publishing. Until a
release appears on the package index, install from a reviewed checkout or verified wheel and use it
only under the checked-in license.
