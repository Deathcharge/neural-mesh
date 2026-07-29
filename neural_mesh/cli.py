"""Command-line entry point for neural-mesh."""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Sequence
from decimal import Decimal

from . import __version__
from .consensus import CallableProvider, ConsensusConfig, ConsensusEngine, ProviderResponse


async def _cautious_answer(prompt: str, max_tokens: int) -> ProviderResponse:
    del prompt, max_tokens
    return ProviderResponse(
        content="Use bounded exponential backoff with jitter and an idempotency key.",
        model="offline-cautious",
        input_tokens=12,
        output_tokens=11,
        cost_usd=Decimal("0"),
    )


async def _concise_answer(prompt: str, max_tokens: int) -> str:
    del prompt, max_tokens
    return "Use bounded exponential backoff with jitter and an idempotency key."


async def _dissenting_answer(prompt: str, max_tokens: int) -> str:
    del prompt, max_tokens
    return "Retry every request forever at a fixed interval."


async def _demo() -> dict[str, object]:
    council = ConsensusEngine(
        [
            CallableProvider("cautious", _cautious_answer),
            CallableProvider("concise", _concise_answer),
            CallableProvider("dissent", _dissenting_answer),
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
    return result.to_dict()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="neural-mesh",
        description="Bounded textual consensus across application-supplied AI providers.",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="print the installed neural-mesh version and exit",
    )
    subparsers = parser.add_subparsers(dest="command")
    subparsers.add_parser(
        "demo",
        help="run a credential-free, deterministic three-provider consensus demo",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the neural-mesh command-line interface."""

    parser = _build_parser()
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    if arguments.version:
        print(__version__)
        return 0
    if arguments.command == "demo":
        print(json.dumps(asyncio.run(_demo()), indent=2))
        return 0
    parser.print_help()
    return 0


__all__ = ["main"]
