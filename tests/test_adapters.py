from __future__ import annotations

import importlib
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from neural_mesh import (
    AnthropicMessagesProvider,
    ConsensusEngine,
    OpenAIResponsesProvider,
)


@dataclass
class Usage:
    input_tokens: int = 10
    output_tokens: int = 5


class FakeOpenAIResponses:
    def __init__(self, *, usage: Usage | None = None) -> None:
        self.calls: list[dict[str, object]] = []
        self.usage = usage

    async def create(self, **arguments: object) -> Any:
        self.calls.append(arguments)
        return SimpleNamespace(
            output_text="shared answer", model="openai-returned", usage=self.usage
        )


class FakeAnthropicMessages:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    async def create(self, **arguments: object) -> Any:
        self.calls.append(arguments)
        return SimpleNamespace(
            content=[
                SimpleNamespace(type="thinking", text="private"),
                SimpleNamespace(type="text", text="shared answer"),
                SimpleNamespace(type="text", text=""),
            ],
            model="anthropic-returned",
            usage=Usage(),
        )


@pytest.mark.asyncio
async def test_official_adapters_translate_requests_and_usage() -> None:
    openai_responses = FakeOpenAIResponses(usage=Usage())
    anthropic_messages = FakeAnthropicMessages()
    openai = OpenAIResponsesProvider(
        "openai-requested",
        client=SimpleNamespace(responses=openai_responses),
    )
    anthropic = AnthropicMessagesProvider(
        "anthropic-requested",
        client=SimpleNamespace(messages=anthropic_messages),
    )

    result = await ConsensusEngine([openai, anthropic]).run(
        "contract",
        "private prompt",
        max_tokens=25,
    )

    assert result.consensus_text == "shared answer"
    assert result.reported_input_tokens == 20
    assert result.reported_output_tokens == 10
    assert result.token_reporting_complete is True
    assert result.cost_reporting_complete is False
    assert openai_responses.calls == [
        {"model": "openai-requested", "input": "private prompt", "max_output_tokens": 25}
    ]
    assert anthropic_messages.calls == [
        {
            "model": "anthropic-requested",
            "max_tokens": 25,
            "messages": [{"role": "user", "content": "private prompt"}],
        }
    ]


@pytest.mark.asyncio
async def test_openai_adapter_preserves_missing_usage() -> None:
    resource = FakeOpenAIResponses()
    provider = OpenAIResponsesProvider("model", client=SimpleNamespace(responses=resource))
    response = await provider.complete("prompt", max_tokens=10)
    assert response.input_tokens is None
    assert response.output_tokens is None


def test_adapters_validate_configuration() -> None:
    client = SimpleNamespace(responses=FakeOpenAIResponses())
    with pytest.raises(ValueError, match="must not be empty"):
        OpenAIResponsesProvider("", client=client)
    with pytest.raises(TypeError, match="must be a string"):
        OpenAIResponsesProvider(1, client=client)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="200-character"):
        OpenAIResponsesProvider("x" * 201, client=client)


def test_missing_optional_sdk_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    real_import = importlib.import_module

    def missing(name: str) -> Any:
        if name == "openai":
            raise ModuleNotFoundError("missing", name="openai")
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(RuntimeError, match=r"neural-mesh\[providers-openai\]"):
        OpenAIResponsesProvider("model")


def test_incompatible_optional_sdk_has_actionable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(importlib, "import_module", lambda name: SimpleNamespace())
    with pytest.raises(RuntimeError, match="does not expose AsyncAnthropic"):
        AnthropicMessagesProvider("model")
