from __future__ import annotations

import asyncio
import json
from decimal import Decimal

import pytest

from neural_mesh import (
    CallableProvider,
    ConsensusEngine,
    ConsensusEvent,
    ProviderResponse,
)


class RecordingObserver:
    def __init__(self) -> None:
        self.events: list[ConsensusEvent] = []

    async def record(self, event: ConsensusEvent) -> None:
        self.events.append(event)


def complete_response(content: str = "same") -> CallableProvider:
    async def complete(prompt: str, max_tokens: int) -> ProviderResponse:
        del prompt, max_tokens
        return ProviderResponse(
            content,
            model="private-model",
            input_tokens=4,
            output_tokens=2,
            cost_usd=Decimal("0.01"),
        )

    return CallableProvider(f"provider-{content}", complete)


@pytest.mark.asyncio
async def test_observer_receives_privacy_minimized_otel_attributes() -> None:
    observer = RecordingObserver()
    engine = ConsensusEngine(
        [complete_response("one"), complete_response("two")],
        observers=[observer],
    )

    result = await engine.run("private/task", "private prompt")

    assert len(observer.events) == 1
    event = observer.events[0]
    attributes = event.attributes
    assert attributes["gen_ai.operation.name"] == "invoke_workflow"
    assert attributes["gen_ai.workflow.name"] == "neural-mesh.consensus"
    assert attributes["gen_ai.usage.input_tokens"] == 8
    assert attributes["gen_ai.usage.output_tokens"] == 4
    assert attributes["neural_mesh.providers.queried"] == 2
    assert attributes["neural_mesh.agreement.level"] == result.agreement_level.value
    assert len(str(attributes["neural_mesh.task.sha256"])) == 64
    serialized = json.dumps(event.to_dict())
    assert "private/task" not in serialized
    assert "private prompt" not in serialized
    assert "private-model" not in serialized
    assert "provider-one" not in serialized


@pytest.mark.asyncio
async def test_incomplete_usage_omits_standard_token_attributes() -> None:
    async def complete(prompt: str, max_tokens: int) -> str:
        del prompt, max_tokens
        return "same"

    observer = RecordingObserver()
    engine = ConsensusEngine(
        [CallableProvider("one", complete), CallableProvider("two", complete)],
        observers=[observer],
    )
    await engine.run("task", "prompt")
    attributes = observer.events[0].attributes
    assert "gen_ai.usage.input_tokens" not in attributes
    assert "gen_ai.usage.output_tokens" not in attributes
    assert attributes["neural_mesh.usage.token_reporting_complete"] is False


@pytest.mark.asyncio
async def test_observer_failure_and_timeout_do_not_change_result() -> None:
    class FailedObserver:
        async def record(self, event: ConsensusEvent) -> None:
            del event
            raise RuntimeError("private observer failure")

    class SlowObserver:
        async def record(self, event: ConsensusEvent) -> None:
            del event
            await asyncio.sleep(60)

    engine = ConsensusEngine(
        [complete_response("one"), complete_response("two")],
        observers=[FailedObserver(), SlowObserver()],
        observer_timeout_seconds=0.01,
    )
    result = await asyncio.wait_for(engine.run("task", "prompt"), timeout=1)
    assert result.providers_succeeded == 2


@pytest.mark.asyncio
async def test_usage_failure_is_exposed_as_low_cardinality_error_type() -> None:
    class FailedStore:
        async def append(self, record: object) -> None:
            del record
            raise OSError("private path")

    observer = RecordingObserver()
    engine = ConsensusEngine(
        [complete_response("one"), complete_response("two")],
        usage_store=FailedStore(),
        observers=[observer],
    )
    result = await engine.run("task", "prompt")
    assert result.usage_error_code == "write_failed"
    assert observer.events[0].attributes["error.type"] == "neural_mesh.usage.write_failed"


def test_observer_configuration_is_bounded_and_validated() -> None:
    provider = complete_response()
    with pytest.raises(ValueError, match="more than 16"):
        ConsensusEngine([provider, provider], observers=[RecordingObserver()] * 17)
    with pytest.raises(TypeError, match="ConsensusObserver"):
        ConsensusEngine([provider, provider], observers=[object()])  # type: ignore[list-item]
    with pytest.raises(ValueError, match="observer_timeout_seconds"):
        ConsensusEngine([provider, provider], observer_timeout_seconds=0)
