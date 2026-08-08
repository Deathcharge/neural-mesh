# Optional provider adapters

The default package has no provider SDK dependency. Install only what the application uses:

```bash
python -m pip install "neural-mesh[providers-openai]"
python -m pip install "neural-mesh[providers-anthropic]"
# or both
python -m pip install "neural-mesh[providers]"
```

The extras currently support the official OpenAI 2.x Responses API and Anthropic 0.x Messages API.
They are bounded below at the versions used to verify these contracts and below the next major
version. SDK upgrades still require the repository tests and the consumer contract fixture.

## Live council

```python
import asyncio

from neural_mesh import (
    AnthropicMessagesProvider,
    ConsensusConfig,
    ConsensusEngine,
    OpenAIResponsesProvider,
)


async def main() -> None:
    council = ConsensusEngine(
        [
            OpenAIResponsesProvider("your-openai-model"),
            AnthropicMessagesProvider("your-anthropic-model"),
        ],
        ConsensusConfig(max_providers=2, max_concurrency=2, timeout_seconds=30),
    )
    result = await council.run("policy-review", "Review this proposed policy.")
    print(result.agreement_level.value, result.consensus_text)


asyncio.run(main())
```

With no injected client, each adapter creates the official asynchronous SDK client. The SDK handles
its standard environment variables (`OPENAI_API_KEY` or `ANTHROPIC_API_KEY`) and configuration. To
set organization, project, base URL, transport, or SDK-level retry/timeout policy, configure the
official client yourself and inject it through `client=`. Never pass credentials through prompts,
provider names, or model names.

The OpenAI adapter sends `model`, `input`, and `max_output_tokens` to `responses.create` and reads
`output_text` plus response usage. The Anthropic adapter sends one user message with `model` and
`max_tokens`, joins returned text blocks, and reads response usage. Non-text Anthropic blocks are not
serialized into consensus text.

Both adapters report the response model and available input/output tokens. They do not calculate
cost: pricing tiers, caching, batch discounts, regional hosting, and negotiated rates change outside
the library. Therefore `cost_reporting_complete` remains false unless an application-owned adapter
returns cost. Cost gates expose missing data instead of treating it as zero.

The engine still owns outer timeout and fan-out bounds and never retries. Official SDKs may have their
own default retries or connection timeouts; configure those deliberately because they affect latency
and cost. Both SDK coroutines must cooperate with cancellation for the engine timeout to be effective.

## Contract fixture

[`contracts/consumer_contract_v1.json`](../contracts/consumer_contract_v1.json) records the Samsarix
consumer's required public symbols, artifact schema versions, outcome codes, and default-event privacy
boundary. CI executes the fixture. A consuming repository should copy or extend this contract on its
side and run it against the exact wheel it intends to deploy; this repository's fixture proves the
producer half, not a deployed consumer.
