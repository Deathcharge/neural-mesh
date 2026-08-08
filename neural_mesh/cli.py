"""Command-line entry point for neural-mesh."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Sequence
from decimal import Decimal
from pathlib import Path

from . import __version__
from .consensus import CallableProvider, ConsensusConfig, ConsensusEngine, ProviderResponse
from .evaluation import (
    ComparisonPolicy,
    compare_reports,
    load_evaluation_report,
    load_replay_suite,
)


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
    evaluate = subparsers.add_parser(
        "evaluate",
        help="run a versioned replay suite and enforce its quality gates",
    )
    evaluate.add_argument("suite", help="path to a neural-mesh replay-suite JSON file")
    evaluate.add_argument(
        "--output",
        default="-",
        help="report path, or '-' for stdout (default: stdout)",
    )
    evaluate.add_argument(
        "--compact",
        action="store_true",
        help="write compact JSON instead of indented JSON",
    )

    compare = subparsers.add_parser(
        "compare",
        help="compare a candidate evaluation report with a baseline",
    )
    compare.add_argument("baseline", help="path to the baseline evaluation report")
    compare.add_argument("candidate", help="path to the candidate evaluation report")
    compare.add_argument(
        "--output",
        default="-",
        help="comparison path, or '-' for stdout (default: stdout)",
    )
    compare.add_argument("--max-pass-rate-drop", type=float, default=0.0)
    compare.add_argument("--max-agreement-drop", type=float, default=0.0)
    compare.add_argument("--max-failure-rate-increase", type=float, default=0.0)
    compare.add_argument("--max-cost-increase-usd")
    compare.add_argument("--max-p95-duration-increase-ms", type=int)
    compare.add_argument("--max-regressed-cases", type=int, default=0)
    return parser


async def _evaluate_replay(suite_path: str, output: str, *, compact: bool) -> int:
    suite = load_replay_suite(suite_path)
    report = await suite.build_runner().run(suite.name, suite.cases)
    _write_json(report.to_dict(), output, compact=compact)
    return 0 if report.passed else 1


def _compare_reports(arguments: argparse.Namespace) -> int:
    raw_cost = arguments.max_cost_increase_usd
    policy = ComparisonPolicy(
        maximum_pass_rate_drop=arguments.max_pass_rate_drop,
        maximum_agreement_drop=arguments.max_agreement_drop,
        maximum_failure_rate_increase=arguments.max_failure_rate_increase,
        maximum_cost_increase_usd=None if raw_cost is None else Decimal(raw_cost),
        maximum_p95_duration_increase_ms=arguments.max_p95_duration_increase_ms,
        maximum_regressed_cases=arguments.max_regressed_cases,
    )
    comparison = compare_reports(
        load_evaluation_report(arguments.baseline),
        load_evaluation_report(arguments.candidate),
        policy,
    )
    _write_json(comparison.to_dict(), arguments.output, compact=False)
    return 0 if comparison.passed else 1


def _write_json(value: dict[str, object], destination: str, *, compact: bool) -> None:
    serialized = json.dumps(
        value,
        indent=None if compact else 2,
        separators=(",", ":") if compact else None,
    )
    if destination == "-":
        print(serialized)
        return
    Path(destination).write_text(f"{serialized}\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """Run the neural-mesh command-line interface."""

    parser = _build_parser()
    arguments = parser.parse_args(list(argv) if argv is not None else None)
    try:
        if arguments.version:
            print(__version__)
            return 0
        if arguments.command == "demo":
            print(json.dumps(asyncio.run(_demo()), indent=2))
            return 0
        if arguments.command == "evaluate":
            return asyncio.run(
                _evaluate_replay(
                    arguments.suite,
                    arguments.output,
                    compact=arguments.compact,
                )
            )
        if arguments.command == "compare":
            return _compare_reports(arguments)
        parser.print_help()
        return 0
    except (OSError, TypeError, ValueError) as error:
        print(f"neural-mesh: error: {error}", file=sys.stderr)
        return 2


__all__ = ["main"]
