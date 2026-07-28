"""Bounded, provider-agnostic multi-model response consensus."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
import unicodedata
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Protocol, runtime_checkable

from .usage import UsageRecord, UsageStore

logger = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)
_PROVIDER_NAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,99}")
_MAX_TASK_CHARS = 500


class ProviderStatus(str, Enum):
    """Outcome of one provider call."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    INVALID_RESPONSE = "invalid_response"


class AgreementLevel(str, Enum):
    """Strength of textual agreement between successful providers."""

    UNANIMOUS = "unanimous"
    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"
    INSUFFICIENT = "insufficient"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ProviderResponse:
    """Normalized response returned by a provider adapter.

    Token and cost values are optional because not every provider reports them.
    Currency values use :class:`~decimal.Decimal` to avoid binary rounding.
    """

    content: str
    model: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.content, str):
            raise TypeError("content must be a string")
        for name, value in (
            ("input_tokens", self.input_tokens),
            ("output_tokens", self.output_tokens),
        ):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer or None")
        if self.cost_usd is not None:
            if not isinstance(self.cost_usd, Decimal):
                raise TypeError("cost_usd must be a Decimal or None")
            if not self.cost_usd.is_finite() or self.cost_usd < 0:
                raise ValueError("cost_usd must be non-negative or None")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "content": self.content,
            "model": self.model,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": str(self.cost_usd) if self.cost_usd is not None else None,
        }


@runtime_checkable
class Provider(Protocol):
    """Minimal async provider interface consumed by :class:`ConsensusEngine`."""

    @property
    def name(self) -> str:
        """Stable unique name used in outcomes and provider selection."""

        ...

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
    ) -> ProviderResponse | str:
        """Return response text or a normalized provider response."""


CompletionCallable = Callable[[str, int], Awaitable[ProviderResponse | str]]


@dataclass(frozen=True, slots=True)
class CallableProvider:
    """Adapt a small async callable to the :class:`Provider` protocol.

    The callable receives ``(prompt, max_tokens)``. Provider-specific SDK,
    credential, retry, and pricing behavior remains in application code.
    """

    name: str
    completion: CompletionCallable = field(repr=False)

    def __post_init__(self) -> None:
        if not callable(self.completion):
            raise TypeError("completion must be callable")

    async def complete(
        self,
        prompt: str,
        *,
        max_tokens: int,
    ) -> ProviderResponse | str:
        return await self.completion(prompt, max_tokens)


@dataclass(frozen=True, slots=True)
class ConsensusConfig:
    """Safety and agreement limits for a consensus engine."""

    max_providers: int = 8
    max_concurrency: int = 4
    timeout_seconds: float = 30.0
    max_tokens_per_provider: int = 1_000
    max_prompt_chars: int = 100_000
    max_response_chars: int = 200_000
    minimum_successful_responses: int = 2
    minimum_cluster_size: int = 2
    minimum_agreement_ratio: float = 0.5
    similarity_threshold: float = 0.72

    def __post_init__(self) -> None:
        _require_int_range("max_providers", self.max_providers, 2, 32)
        _require_int_range(
            "max_concurrency",
            self.max_concurrency,
            1,
            32,
        )
        _require_float_range("timeout_seconds", self.timeout_seconds, 0.01, 600.0)
        _require_int_range(
            "max_tokens_per_provider",
            self.max_tokens_per_provider,
            1,
            1_000_000,
        )
        _require_int_range("max_prompt_chars", self.max_prompt_chars, 1, 1_000_000)
        _require_int_range(
            "max_response_chars",
            self.max_response_chars,
            1,
            2_000_000,
        )
        _require_int_range(
            "minimum_successful_responses",
            self.minimum_successful_responses,
            2,
            self.max_providers,
        )
        _require_int_range(
            "minimum_cluster_size",
            self.minimum_cluster_size,
            2,
            self.max_providers,
        )
        _require_float_range(
            "minimum_agreement_ratio",
            self.minimum_agreement_ratio,
            0.0,
            1.0,
        )
        _require_float_range(
            "similarity_threshold",
            self.similarity_threshold,
            0.0,
            1.0,
        )


@dataclass(frozen=True, slots=True)
class ProviderOutcome:
    """Structured, redacted result of one provider call."""

    provider: str
    status: ProviderStatus
    latency_ms: int
    response: ProviderResponse | None = None
    error_code: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "provider": self.provider,
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "response": self.response.to_dict() if self.response is not None else None,
            "error_code": self.error_code,
        }


@dataclass(frozen=True, slots=True)
class ConsensusResult:
    """Complete outcome of one bounded consensus run."""

    task: str
    created_at: datetime
    duration_ms: int
    requested_max_tokens: int
    outcomes: tuple[ProviderOutcome, ...]
    agreement_level: AgreementLevel
    agreement_ratio: float
    average_similarity: float
    consensus_text: str | None
    winning_providers: tuple[str, ...]
    reported_input_tokens: int
    reported_output_tokens: int
    token_reporting_complete: bool
    reported_cost_usd: Decimal
    cost_reporting_complete: bool
    usage_recorded: bool = False
    usage_error_code: str | None = None

    @property
    def providers_queried(self) -> int:
        """Number of providers selected for this run."""

        return len(self.outcomes)

    @property
    def providers_succeeded(self) -> int:
        """Number of providers that returned valid non-empty text."""

        return sum(outcome.status is ProviderStatus.SUCCESS for outcome in self.outcomes)

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "task": self.task,
            "created_at": self.created_at.isoformat(),
            "duration_ms": self.duration_ms,
            "requested_max_tokens": self.requested_max_tokens,
            "providers_queried": self.providers_queried,
            "providers_succeeded": self.providers_succeeded,
            "outcomes": [outcome.to_dict() for outcome in self.outcomes],
            "agreement_level": self.agreement_level.value,
            "agreement_ratio": round(self.agreement_ratio, 4),
            "average_similarity": round(self.average_similarity, 4),
            "consensus_text": self.consensus_text,
            "winning_providers": list(self.winning_providers),
            "reported_input_tokens": self.reported_input_tokens,
            "reported_output_tokens": self.reported_output_tokens,
            "token_reporting_complete": self.token_reporting_complete,
            "reported_cost_usd": str(self.reported_cost_usd),
            "cost_reporting_complete": self.cost_reporting_complete,
            "usage_recorded": self.usage_recorded,
            "usage_error_code": self.usage_error_code,
        }


class ConsensusEngine:
    """Run bounded provider calls and derive deterministic textual agreement."""

    def __init__(
        self,
        providers: Sequence[Provider],
        config: ConsensusConfig | None = None,
        *,
        usage_store: UsageStore | None = None,
    ) -> None:
        self.config = config or ConsensusConfig()
        self._providers = tuple(providers)
        self._usage_store = usage_store
        self._provider_by_name = _validate_providers(self._providers, self.config)
        self._semaphore = asyncio.Semaphore(self.config.max_concurrency)

    @property
    def provider_names(self) -> tuple[str, ...]:
        """Configured provider names, in deterministic call order."""

        return tuple(self._provider_by_name)

    async def run(
        self,
        task: str,
        prompt: str,
        *,
        provider_names: Sequence[str] | None = None,
        max_tokens: int | None = None,
    ) -> ConsensusResult:
        """Execute a council request and return structured agreement evidence.

        Invalid input is rejected before provider tasks are created. Cancellation
        propagates to the caller and pending provider tasks are cancelled.
        """

        selected = self._select_providers(provider_names)
        requested_max_tokens = self._validate_request(task, prompt, max_tokens)
        started = time.perf_counter()
        created_at = datetime.now(timezone.utc)
        tasks = [
            asyncio.create_task(
                self._invoke_provider(
                    provider,
                    prompt,
                    requested_max_tokens,
                    self._semaphore,
                ),
                name=f"neural-mesh:{provider.name}",
            )
            for provider in selected
        ]

        try:
            outcomes = tuple(await asyncio.gather(*tasks))
        except BaseException:
            for provider_task in tasks:
                if not provider_task.done():
                    provider_task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        result = self._build_result(
            task=task,
            created_at=created_at,
            duration_ms=_elapsed_ms(started),
            requested_max_tokens=requested_max_tokens,
            outcomes=outcomes,
        )

        if self._usage_store is not None:
            try:
                await self._usage_store.append(_usage_record(result))
            except Exception as error:
                logger.warning(
                    "Usage persistence failed with %s",
                    type(error).__name__,
                )
                result = replace(
                    result,
                    usage_recorded=False,
                    usage_error_code="write_failed",
                )
            else:
                result = replace(result, usage_recorded=True)

        return result

    def _select_providers(
        self,
        provider_names: Sequence[str] | None,
    ) -> tuple[Provider, ...]:
        if provider_names is None:
            return self._providers
        if isinstance(provider_names, str | bytes):
            raise TypeError("provider_names must be a sequence of provider names")
        names = tuple(provider_names)
        if not names:
            raise ValueError("provider_names must select at least one provider")
        if len(names) > self.config.max_providers:
            raise ValueError("provider_names exceeds max_providers")
        if len(set(names)) != len(names):
            raise ValueError("provider_names must not contain duplicates")
        unknown = [name for name in names if name not in self._provider_by_name]
        if unknown:
            raise ValueError("provider_names contains an unknown provider")
        return tuple(self._provider_by_name[name] for name in names)

    def _validate_request(
        self,
        task: str,
        prompt: str,
        max_tokens: int | None,
    ) -> int:
        if not isinstance(task, str):
            raise TypeError("task must be a string")
        if not task.strip():
            raise ValueError("task must not be empty")
        if len(task) > _MAX_TASK_CHARS:
            raise ValueError(f"task exceeds {_MAX_TASK_CHARS} characters")
        if not isinstance(prompt, str):
            raise TypeError("prompt must be a string")
        if not prompt.strip():
            raise ValueError("prompt must not be empty")
        if len(prompt) > self.config.max_prompt_chars:
            raise ValueError("prompt exceeds max_prompt_chars")
        requested = self.config.max_tokens_per_provider if max_tokens is None else max_tokens
        _require_int_range(
            "max_tokens",
            requested,
            1,
            self.config.max_tokens_per_provider,
        )
        return requested

    async def _invoke_provider(
        self,
        provider: Provider,
        prompt: str,
        max_tokens: int,
        semaphore: asyncio.Semaphore,
    ) -> ProviderOutcome:
        async with semaphore:
            started = time.perf_counter()
            try:
                raw_response = await asyncio.wait_for(
                    provider.complete(prompt, max_tokens=max_tokens),
                    timeout=self.config.timeout_seconds,
                )
            except TimeoutError:
                return ProviderOutcome(
                    provider=provider.name,
                    status=ProviderStatus.TIMEOUT,
                    latency_ms=_elapsed_ms(started),
                    error_code="timeout",
                )
            except Exception as error:
                logger.warning(
                    "Provider %s failed with %s",
                    provider.name,
                    type(error).__name__,
                )
                return ProviderOutcome(
                    provider=provider.name,
                    status=ProviderStatus.ERROR,
                    latency_ms=_elapsed_ms(started),
                    error_code="provider_error",
                )

            try:
                response = _normalize_response(raw_response)
            except (TypeError, ValueError):
                return ProviderOutcome(
                    provider=provider.name,
                    status=ProviderStatus.INVALID_RESPONSE,
                    latency_ms=_elapsed_ms(started),
                    error_code="invalid_response",
                )
            if (
                not response.content.strip()
                or len(response.content) > self.config.max_response_chars
            ):
                return ProviderOutcome(
                    provider=provider.name,
                    status=ProviderStatus.INVALID_RESPONSE,
                    latency_ms=_elapsed_ms(started),
                    error_code="invalid_response",
                )
            return ProviderOutcome(
                provider=provider.name,
                status=ProviderStatus.SUCCESS,
                latency_ms=_elapsed_ms(started),
                response=response,
            )

    def _build_result(
        self,
        *,
        task: str,
        created_at: datetime,
        duration_ms: int,
        requested_max_tokens: int,
        outcomes: tuple[ProviderOutcome, ...],
    ) -> ConsensusResult:
        successful = tuple(
            outcome
            for outcome in outcomes
            if outcome.status is ProviderStatus.SUCCESS and outcome.response is not None
        )
        input_tokens, output_tokens, token_complete = _token_totals(outcomes)
        cost_usd, cost_complete = _cost_total(outcomes)

        if not successful:
            return ConsensusResult(
                task=task,
                created_at=created_at,
                duration_ms=duration_ms,
                requested_max_tokens=requested_max_tokens,
                outcomes=outcomes,
                agreement_level=AgreementLevel.ERROR,
                agreement_ratio=0.0,
                average_similarity=0.0,
                consensus_text=None,
                winning_providers=(),
                reported_input_tokens=input_tokens,
                reported_output_tokens=output_tokens,
                token_reporting_complete=token_complete,
                reported_cost_usd=cost_usd,
                cost_reporting_complete=cost_complete,
            )

        responses = tuple(outcome.response.content for outcome in successful if outcome.response)
        clusters, similarities = _cluster_responses(
            responses,
            threshold=self.config.similarity_threshold,
        )
        winning_cluster = _winning_cluster(clusters, similarities)
        agreement_ratio = len(winning_cluster) / len(successful)
        average_similarity = _cluster_similarity(winning_cluster, similarities)
        winning_providers = tuple(successful[index].provider for index in winning_cluster)

        has_enough_successes = len(successful) >= self.config.minimum_successful_responses
        has_quorum = (
            has_enough_successes
            and len(winning_cluster) >= self.config.minimum_cluster_size
            and agreement_ratio >= self.config.minimum_agreement_ratio
        )
        agreement_level = _agreement_level(
            successful_count=len(successful),
            winning_count=len(winning_cluster),
            minimum_successful=self.config.minimum_successful_responses,
        )
        consensus_text = None
        if has_quorum:
            consensus_index = _cluster_medoid(winning_cluster, similarities)
            consensus_text = successful[consensus_index].response.content  # type: ignore[union-attr]

        return ConsensusResult(
            task=task,
            created_at=created_at,
            duration_ms=duration_ms,
            requested_max_tokens=requested_max_tokens,
            outcomes=outcomes,
            agreement_level=agreement_level,
            agreement_ratio=agreement_ratio,
            average_similarity=average_similarity,
            consensus_text=consensus_text,
            winning_providers=winning_providers,
            reported_input_tokens=input_tokens,
            reported_output_tokens=output_tokens,
            token_reporting_complete=token_complete,
            reported_cost_usd=cost_usd,
            cost_reporting_complete=cost_complete,
        )


def _validate_providers(
    providers: tuple[Provider, ...],
    config: ConsensusConfig,
) -> Mapping[str, Provider]:
    if not providers:
        raise ValueError("at least one provider is required")
    if len(providers) > config.max_providers:
        raise ValueError("provider count exceeds max_providers")
    result: dict[str, Provider] = {}
    for provider in providers:
        name = getattr(provider, "name", None)
        complete = getattr(provider, "complete", None)
        if not isinstance(name, str):
            raise TypeError("provider name must be a string")
        if not name.strip():
            raise ValueError("provider name must not be empty")
        if not _PROVIDER_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                "provider name must start with an ASCII letter or digit and contain only "
                "letters, digits, dot, underscore, colon, slash, or hyphen"
            )
        if not callable(complete):
            raise TypeError(f"provider {name!r} has no callable complete method")
        if name in result:
            raise ValueError(f"duplicate provider name: {name}")
        result[name] = provider
    return result


def _normalize_response(response: ProviderResponse | str) -> ProviderResponse:
    if isinstance(response, str):
        return ProviderResponse(content=response)
    if isinstance(response, ProviderResponse):
        return response
    raise TypeError("provider must return str or ProviderResponse")


def _normalize_tokens(content: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", content).casefold()
    return frozenset(_TOKEN_PATTERN.findall(normalized))


def _jaccard(first: frozenset[str], second: frozenset[str]) -> float:
    if not first and not second:
        return 0.0
    union = first | second
    return len(first & second) / len(union) if union else 0.0


def _cluster_responses(
    responses: tuple[str, ...],
    *,
    threshold: float,
) -> tuple[list[list[int]], list[list[float]]]:
    token_sets = [_normalize_tokens(response) for response in responses]
    similarities = [[_jaccard(first, second) for second in token_sets] for first in token_sets]
    clusters: list[list[int]] = []
    for index in range(len(responses)):
        eligible: list[tuple[float, int]] = []
        for cluster_index, cluster in enumerate(clusters):
            pair_scores = [similarities[index][member] for member in cluster]
            if all(score >= threshold for score in pair_scores):
                eligible.append((sum(pair_scores) / len(pair_scores), cluster_index))
        if eligible:
            _, selected_cluster = max(eligible, key=lambda item: (item[0], -item[1]))
            clusters[selected_cluster].append(index)
        else:
            clusters.append([index])
    return clusters, similarities


def _winning_cluster(
    clusters: list[list[int]],
    similarities: list[list[float]],
) -> list[int]:
    return max(
        clusters,
        key=lambda cluster: (
            len(cluster),
            _cluster_similarity(cluster, similarities),
            -cluster[0],
        ),
    )


def _cluster_similarity(
    cluster: list[int],
    similarities: list[list[float]],
) -> float:
    if len(cluster) <= 1:
        return 0.0
    scores = [
        similarities[first][second]
        for offset, first in enumerate(cluster)
        for second in cluster[offset + 1 :]
    ]
    return sum(scores) / len(scores)


def _cluster_medoid(
    cluster: list[int],
    similarities: list[list[float]],
) -> int:
    def mean_similarity(index: int) -> float:
        peers = [member for member in cluster if member != index]
        return sum(similarities[index][peer] for peer in peers) / len(peers)

    return max(cluster, key=lambda index: (mean_similarity(index), -index))


def _agreement_level(
    *,
    successful_count: int,
    winning_count: int,
    minimum_successful: int,
) -> AgreementLevel:
    if successful_count < minimum_successful:
        return AgreementLevel.INSUFFICIENT
    if winning_count < 2:
        return AgreementLevel.WEAK
    if winning_count == successful_count:
        return AgreementLevel.UNANIMOUS
    ratio = winning_count / successful_count
    if ratio >= 0.75:
        return AgreementLevel.STRONG
    if ratio >= 0.5:
        return AgreementLevel.MODERATE
    return AgreementLevel.WEAK


def _token_totals(
    outcomes: tuple[ProviderOutcome, ...],
) -> tuple[int, int, bool]:
    input_total = 0
    output_total = 0
    complete = True
    for outcome in outcomes:
        response = outcome.response
        if response is None:
            complete = False
            continue
        if response.input_tokens is None or response.output_tokens is None:
            complete = False
        input_total += response.input_tokens or 0
        output_total += response.output_tokens or 0
    return input_total, output_total, complete


def _cost_total(
    outcomes: tuple[ProviderOutcome, ...],
) -> tuple[Decimal, bool]:
    total = Decimal("0")
    complete = True
    for outcome in outcomes:
        response = outcome.response
        if response is None:
            complete = False
            continue
        if response.cost_usd is None:
            complete = False
        else:
            total += response.cost_usd
    return total, complete


def _usage_record(result: ConsensusResult) -> UsageRecord:
    task_digest = hashlib.sha256(result.task.encode("utf-8")).hexdigest()
    return UsageRecord(
        timestamp=result.created_at,
        task_sha256=task_digest,
        agreement_level=result.agreement_level.value,
        providers_queried=result.providers_queried,
        providers_succeeded=result.providers_succeeded,
        requested_max_tokens=result.requested_max_tokens,
        reported_input_tokens=result.reported_input_tokens,
        reported_output_tokens=result.reported_output_tokens,
        token_reporting_complete=result.token_reporting_complete,
        reported_cost_usd=result.reported_cost_usd,
        cost_reporting_complete=result.cost_reporting_complete,
        duration_ms=result.duration_ms,
    )


def _require_int_range(name: str, value: int, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _require_float_range(
    name: str,
    value: float,
    minimum: float,
    maximum: float,
) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(float(value)) or value < minimum or value > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1_000))


__all__ = [
    "AgreementLevel",
    "CallableProvider",
    "CompletionCallable",
    "ConsensusConfig",
    "ConsensusEngine",
    "ConsensusResult",
    "Provider",
    "ProviderOutcome",
    "ProviderResponse",
    "ProviderStatus",
]
