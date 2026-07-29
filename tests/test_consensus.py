from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from decimal import Decimal
from typing import Any, cast

import pytest

from neural_mesh import (
    AgreementLevel,
    CallableProvider,
    ConsensusConfig,
    ConsensusEngine,
    ConsensusResult,
    ProviderResponse,
    ProviderStatus,
)
from neural_mesh.usage import UsageRecord


def provider(
    name: str,
    response: str | ProviderResponse,
) -> CallableProvider:
    async def complete(_prompt: str, _max_tokens: int) -> str | ProviderResponse:
        return response

    return CallableProvider(name, complete)


@pytest.mark.asyncio
async def test_consensus_selects_largest_cluster_and_reports_usage() -> None:
    response = ProviderResponse(
        "Use bounded exponential backoff with jitter.",
        model="alpha-model",
        input_tokens=10,
        output_tokens=7,
        cost_usd=Decimal("0.012"),
    )
    engine = ConsensusEngine(
        [
            provider("alpha", response),
            provider("beta", "Use bounded exponential backoff with jitter."),
            provider("gamma", "Never retry; discard every failed request."),
        ],
        ConsensusConfig(max_providers=3),
    )

    result = await engine.run("retry", "How should requests retry?")

    assert result.agreement_level is AgreementLevel.MODERATE
    assert result.agreement_ratio == pytest.approx(2 / 3)
    assert result.average_similarity == 1.0
    assert result.winning_providers == ("alpha", "beta")
    assert result.consensus_text == response.content
    assert result.providers_queried == 3
    assert result.providers_succeeded == 3
    assert result.reported_input_tokens == 10
    assert result.reported_output_tokens == 7
    assert result.token_reporting_complete is False
    assert result.reported_cost_usd == Decimal("0.012")
    assert result.cost_reporting_complete is False
    assert json.loads(json.dumps(result.to_dict()))["agreement_level"] == "moderate"


@pytest.mark.asyncio
async def test_unanimous_responses_return_medoid_in_provider_order() -> None:
    engine = ConsensusEngine(
        [provider("first", "same answer"), provider("second", "same answer")],
    )

    result = await engine.run("same", "Compare these answers")

    assert result.agreement_level is AgreementLevel.UNANIMOUS
    assert result.consensus_text == "same answer"
    assert result.winning_providers == ("first", "second")
    assert result.average_similarity == 1.0


@pytest.mark.asyncio
async def test_no_quorum_returns_no_consensus_text() -> None:
    engine = ConsensusEngine(
        [
            provider("one", "alpha only"),
            provider("two", "bravo solely"),
            provider("three", "charlie uniquely"),
        ],
        ConsensusConfig(max_providers=3, minimum_agreement_ratio=0.5),
    )

    result = await engine.run("different", "Give unrelated answers")

    assert result.agreement_level is AgreementLevel.WEAK
    assert result.agreement_ratio == pytest.approx(1 / 3)
    assert result.average_similarity == 0.0
    assert result.winning_providers == ("one",)
    assert result.consensus_text is None


@pytest.mark.asyncio
async def test_non_word_responses_do_not_create_false_agreement() -> None:
    result = await ConsensusEngine(
        [provider("one", "..."), provider("two", "!!!")],
    ).run("symbols", "Compare symbols")

    assert result.agreement_level is AgreementLevel.WEAK
    assert result.average_similarity == 0
    assert result.consensus_text is None


@pytest.mark.asyncio
async def test_strong_and_below_majority_clusters_are_labeled_honestly() -> None:
    strong = await ConsensusEngine(
        [
            provider("one", "matching answer"),
            provider("two", "matching answer"),
            provider("three", "matching answer"),
            provider("four", "unrelated minority"),
        ],
        ConsensusConfig(max_providers=4),
    ).run("strong", "prompt")
    assert strong.agreement_level is AgreementLevel.STRONG
    assert strong.consensus_text == "matching answer"

    weak = await ConsensusEngine(
        [
            provider("one", "matching answer"),
            provider("two", "matching answer"),
            provider("three", "unrelated third"),
            provider("four", "different fourth"),
            provider("five", "unique fifth"),
        ],
        ConsensusConfig(max_providers=5),
    ).run("weak", "prompt")
    assert weak.agreement_level is AgreementLevel.WEAK
    assert weak.agreement_ratio == 0.4
    assert weak.consensus_text is None


@pytest.mark.asyncio
async def test_insufficient_successes_and_all_errors_are_distinct() -> None:
    async def fail(_prompt: str, _max_tokens: int) -> str:
        raise RuntimeError("provider-secret-must-not-escape")

    one_success = ConsensusEngine([provider("ok", "answer")])
    insufficient = await one_success.run("task", "prompt")
    assert insufficient.agreement_level is AgreementLevel.INSUFFICIENT
    assert insufficient.consensus_text is None

    all_failed = await ConsensusEngine([CallableProvider("bad", fail)]).run(
        "task",
        "prompt",
    )
    assert all_failed.agreement_level is AgreementLevel.ERROR
    assert all_failed.agreement_ratio == 0
    assert all_failed.average_similarity == 0
    assert all_failed.winning_providers == ()
    assert all_failed.outcomes[0].error_code == "provider_error"
    assert all_failed.token_reporting_complete is False
    assert all_failed.cost_reporting_complete is False
    assert "provider-secret" not in json.dumps(all_failed.to_dict())


@pytest.mark.asyncio
async def test_timeout_error_and_invalid_response_do_not_hide_success() -> None:
    async def slow(_prompt: str, _max_tokens: int) -> str:
        await asyncio.sleep(1)
        return "late"

    async def fail(_prompt: str, _max_tokens: int) -> str:
        raise LookupError("private-detail")

    async def invalid(_prompt: str, _max_tokens: int) -> Any:
        return {"content": "wrong contract"}

    engine = ConsensusEngine(
        [
            provider("ok", "valid"),
            CallableProvider("slow", slow),
            CallableProvider("fail", fail),
            CallableProvider("invalid", cast(Callable[[str, int], Awaitable[str]], invalid)),
            provider("empty", "   "),
            provider("large", "x" * 11),
        ],
        ConsensusConfig(
            max_providers=6,
            max_concurrency=6,
            timeout_seconds=0.01,
            max_response_chars=10,
        ),
    )

    result = await engine.run("mixed", "prompt")

    assert [outcome.status for outcome in result.outcomes] == [
        ProviderStatus.SUCCESS,
        ProviderStatus.TIMEOUT,
        ProviderStatus.ERROR,
        ProviderStatus.INVALID_RESPONSE,
        ProviderStatus.INVALID_RESPONSE,
        ProviderStatus.INVALID_RESPONSE,
    ]
    assert result.agreement_level is AgreementLevel.INSUFFICIENT
    assert result.providers_succeeded == 1


@pytest.mark.asyncio
async def test_concurrency_is_bounded() -> None:
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def measured(_prompt: str, _max_tokens: int) -> str:
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1
        return "same"

    engine = ConsensusEngine(
        [CallableProvider(f"p{index}", measured) for index in range(5)],
        ConsensusConfig(max_providers=5, max_concurrency=2),
    )

    result = await engine.run("bounded", "prompt")

    assert peak == 2
    assert result.providers_succeeded == 5


@pytest.mark.asyncio
async def test_concurrency_limit_is_shared_across_concurrent_runs() -> None:
    active = 0
    peak = 0
    lock = asyncio.Lock()

    async def measured(_prompt: str, _max_tokens: int) -> str:
        nonlocal active, peak
        async with lock:
            active += 1
            peak = max(peak, active)
        await asyncio.sleep(0.01)
        async with lock:
            active -= 1
        return "same"

    engine = ConsensusEngine(
        [CallableProvider(f"p{index}", measured) for index in range(3)],
        ConsensusConfig(max_providers=3, max_concurrency=2),
    )

    first, second = await asyncio.gather(
        engine.run("first", "prompt"),
        engine.run("second", "prompt"),
    )

    assert peak == 2
    assert first.providers_succeeded == second.providers_succeeded == 3


@pytest.mark.asyncio
async def test_cancellation_cleans_up_provider_tasks() -> None:
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def waits_forever(_prompt: str, _max_tokens: int) -> str:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()
        return "unreachable"

    engine = ConsensusEngine([CallableProvider("wait", waits_forever)])
    run_task = asyncio.create_task(engine.run("cancel", "prompt"))
    await started.wait()
    run_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert cleaned.is_set()


@pytest.mark.asyncio
async def test_request_selection_and_validation_happen_before_calls() -> None:
    calls = 0

    async def counted(_prompt: str, _max_tokens: int) -> str:
        nonlocal calls
        calls += 1
        return "answer"

    engine = ConsensusEngine(
        [CallableProvider("one", counted), CallableProvider("two", counted)],
        ConsensusConfig(max_providers=2, max_tokens_per_provider=20, max_prompt_chars=10),
    )

    selected = await engine.run(
        "task",
        "prompt",
        provider_names=["two"],
        max_tokens=10,
    )
    assert selected.outcomes[0].provider == "two"
    assert selected.requested_max_tokens == 10

    invalid_calls: list[Callable[[], Awaitable[ConsensusResult]]] = [
        lambda: engine.run("", "prompt"),
        lambda: engine.run("task", ""),
        lambda: engine.run("task", "x" * 11),
        lambda: engine.run("x" * 501, "prompt"),
        lambda: engine.run("task", "prompt", max_tokens=21),
        lambda: engine.run("task", "prompt", provider_names=[]),
        lambda: engine.run("task", "prompt", provider_names=["one", "one"]),
        lambda: engine.run("task", "prompt", provider_names=["missing"]),
    ]
    for invalid_call in invalid_calls:
        with pytest.raises(ValueError):
            await invalid_call()
    with pytest.raises(TypeError):
        await engine.run(cast(Any, None), "prompt")
    with pytest.raises(TypeError):
        await engine.run("task", cast(Any, None))
    with pytest.raises(TypeError):
        await engine.run("task", "prompt", provider_names=cast(Any, "one"))
    assert calls == 1


@pytest.mark.parametrize(
    ("kwargs", "error_type"),
    [
        ({"max_providers": 0}, ValueError),
        ({"max_providers": 1}, ValueError),
        ({"max_providers": 33}, ValueError),
        ({"max_concurrency": 0}, ValueError),
        ({"timeout_seconds": float("inf")}, ValueError),
        ({"timeout_seconds": "slow"}, TypeError),
        ({"max_tokens_per_provider": True}, TypeError),
        ({"max_prompt_chars": 0}, ValueError),
        ({"max_response_chars": 2_000_001}, ValueError),
        ({"minimum_successful_responses": 9}, ValueError),
        ({"minimum_cluster_size": 9}, ValueError),
        ({"minimum_agreement_ratio": -0.1}, ValueError),
        ({"similarity_threshold": 1.1}, ValueError),
    ],
)
def test_config_rejects_unsafe_limits(
    kwargs: dict[str, object],
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        ConsensusConfig(**cast(Any, kwargs))


def test_provider_and_response_contract_validation() -> None:
    async def complete(_prompt: str, _max_tokens: int) -> str:
        return "ok"

    with pytest.raises(TypeError):
        CallableProvider("name", cast(Any, None))
    with pytest.raises(TypeError):
        ProviderResponse(cast(Any, 3))
    with pytest.raises(ValueError):
        ProviderResponse("ok", input_tokens=-1)
    with pytest.raises(ValueError):
        ProviderResponse("ok", output_tokens=cast(Any, 1.5))
    with pytest.raises(TypeError):
        ProviderResponse("ok", cost_usd=cast(Any, 0.1))
    with pytest.raises(ValueError):
        ProviderResponse("ok", cost_usd=Decimal("-1"))
    with pytest.raises(ValueError):
        ProviderResponse("ok", cost_usd=Decimal("NaN"))

    with pytest.raises(ValueError):
        ConsensusEngine([])
    with pytest.raises(ValueError):
        ConsensusEngine([CallableProvider("", complete)])
    with pytest.raises(ValueError):
        ConsensusEngine([CallableProvider("same", complete), CallableProvider("same", complete)])
    with pytest.raises(ValueError):
        ConsensusEngine([CallableProvider("x" * 101, complete)])
    with pytest.raises(ValueError):
        ConsensusEngine([CallableProvider("unsafe\nname", complete)])


class FailingUsageStore:
    async def append(self, _record: UsageRecord) -> None:
        raise RuntimeError("private persistence detail")


@pytest.mark.asyncio
async def test_usage_store_failure_is_redacted_and_does_not_erase_result() -> None:
    result = await ConsensusEngine(
        [provider("one", "answer")],
        usage_store=FailingUsageStore(),
    ).run("task", "prompt")

    assert result.providers_succeeded == 1
    assert result.usage_recorded is False
    assert result.usage_error_code == "write_failed"
