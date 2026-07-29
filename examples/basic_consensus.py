"""Offline, deterministic neural-mesh example."""

from __future__ import annotations

import asyncio
import json
from decimal import Decimal

from neural_mesh import CallableProvider, ConsensusConfig, ConsensusEngine, ProviderResponse


async def cautious_answer(prompt: str, max_tokens: int) -> ProviderResponse:
    """Return one deterministic stand-in for a real provider SDK call."""

    del prompt, max_tokens
    return ProviderResponse(
        content="Use bounded exponential backoff with jitter and an idempotency key.",
        model="offline-cautious",
        input_tokens=12,
        output_tokens=11,
        cost_usd=Decimal("0"),
    )


async def concise_answer(prompt: str, max_tokens: int) -> str:
    """Return a similar answer through the shorthand string interface."""

    del prompt, max_tokens
    return "Use bounded exponential backoff with jitter and an idempotency key."


async def dissenting_answer(prompt: str, max_tokens: int) -> str:
    """Return a deliberately unrelated answer to demonstrate a minority outcome."""

    del prompt, max_tokens
    return "Retry every request forever at a fixed interval."


async def main() -> None:
    """Run the complete adapter-to-consensus journey without API credentials."""

    council = ConsensusEngine(
        [
            CallableProvider("cautious", cautious_answer),
            CallableProvider("concise", concise_answer),
            CallableProvider("dissent", dissenting_answer),
        ],
        ConsensusConfig(
            max_providers=3,
            max_concurrency=2,
            max_tokens_per_provider=200,
            timeout_seconds=5,
        ),
    )
    result = await council.run(
        "retry-policy",
        "How should a production client retry a non-idempotent request?",
    )
    print(json.dumps(result.to_dict(), indent=2))


if __name__ == "__main__":
    asyncio.run(main())
