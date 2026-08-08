"""Optional adapters for official asynchronous provider SDKs."""

from __future__ import annotations

import importlib
from collections.abc import Awaitable
from typing import Protocol, cast

from .consensus import ProviderResponse


class _TokenUsage(Protocol):
    input_tokens: int
    output_tokens: int


class _OpenAIResponse(Protocol):
    output_text: str
    model: str
    usage: _TokenUsage | None


class _OpenAIResponses(Protocol):
    def create(
        self,
        *,
        model: str,
        input: str,
        max_output_tokens: int,
    ) -> Awaitable[_OpenAIResponse]: ...


class OpenAIAsyncClient(Protocol):
    """Subset of ``openai.AsyncOpenAI`` consumed by the adapter."""

    responses: _OpenAIResponses


class _AnthropicTextBlock(Protocol):
    type: str
    text: str


class _AnthropicResponse(Protocol):
    content: list[_AnthropicTextBlock]
    model: str
    usage: _TokenUsage


class _AnthropicMessages(Protocol):
    def create(
        self,
        *,
        model: str,
        max_tokens: int,
        messages: list[dict[str, str]],
    ) -> Awaitable[_AnthropicResponse]: ...


class AnthropicAsyncClient(Protocol):
    """Subset of ``anthropic.AsyncAnthropic`` consumed by the adapter."""

    messages: _AnthropicMessages


class OpenAIResponsesProvider:
    """Provider backed by the official OpenAI asynchronous Responses API."""

    def __init__(
        self,
        model: str,
        *,
        client: OpenAIAsyncClient | None = None,
        name: str = "openai",
    ) -> None:
        self._model = _require_text("model", model)
        self._name = _require_text("name", name)
        self._client = client if client is not None else _load_openai_client()

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, prompt: str, *, max_tokens: int) -> ProviderResponse:
        response = await self._client.responses.create(
            model=self._model,
            input=prompt,
            max_output_tokens=max_tokens,
        )
        usage = response.usage
        return ProviderResponse(
            content=response.output_text,
            model=response.model,
            input_tokens=None if usage is None else usage.input_tokens,
            output_tokens=None if usage is None else usage.output_tokens,
        )


class AnthropicMessagesProvider:
    """Provider backed by the official Anthropic asynchronous Messages API."""

    def __init__(
        self,
        model: str,
        *,
        client: AnthropicAsyncClient | None = None,
        name: str = "anthropic",
    ) -> None:
        self._model = _require_text("model", model)
        self._name = _require_text("name", name)
        self._client = client if client is not None else _load_anthropic_client()

    @property
    def name(self) -> str:
        return self._name

    async def complete(self, prompt: str, *, max_tokens: int) -> ProviderResponse:
        response = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        content = "\n".join(
            block.text for block in response.content if block.type == "text" and block.text
        )
        return ProviderResponse(
            content=content,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )


def _load_openai_client() -> OpenAIAsyncClient:
    return cast(OpenAIAsyncClient, _load_client("openai", "AsyncOpenAI", "providers-openai"))


def _load_anthropic_client() -> AnthropicAsyncClient:
    return cast(
        AnthropicAsyncClient,
        _load_client("anthropic", "AsyncAnthropic", "providers-anthropic"),
    )


def _load_client(module_name: str, class_name: str, extra: str) -> object:
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError as error:
        if error.name != module_name:
            raise
        raise RuntimeError(
            f"{module_name} SDK is not installed; install neural-mesh[{extra}]"
        ) from error
    constructor = getattr(module, class_name, None)
    if not callable(constructor):
        raise RuntimeError(f"{module_name} SDK does not expose {class_name}")
    return constructor()


def _require_text(name: str, value: object) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    if len(value) > 200:
        raise ValueError(f"{name} exceeds the 200-character limit")
    return value


__all__ = [
    "AnthropicAsyncClient",
    "AnthropicMessagesProvider",
    "OpenAIAsyncClient",
    "OpenAIResponsesProvider",
]
