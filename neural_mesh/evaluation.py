"""Replayable evaluation suites and CI-grade quality gates."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from enum import Enum
from pathlib import Path
from typing import ClassVar, Protocol

from .consensus import (
    CallableProvider,
    CompletionCallable,
    ConsensusConfig,
    ConsensusEngine,
    ConsensusResult,
    ProviderResponse,
    ProviderStatus,
)

logger = logging.getLogger(__name__)

_MAX_CASES_HARD_LIMIT = 100_000
_MAX_REPLAY_BYTES = 50_000_000
_MAX_REPORT_BYTES = 100_000_000


class ScoreStatus(str, Enum):
    """Outcome of applying one scorer to one evaluation case."""

    PASSED = "passed"
    FAILED = "failed"
    SKIPPED = "skipped"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class EvaluationScore:
    """Normalized scorer result with no prompt or response content."""

    scorer: str
    status: ScoreStatus
    value: float | None
    reason: str

    def __post_init__(self) -> None:
        _require_name("scorer", self.scorer)
        if self.value is not None:
            if not isinstance(self.value, int | float) or isinstance(self.value, bool):
                raise TypeError("score value must be a number or None")
            if not math.isfinite(self.value) or not 0.0 <= self.value <= 1.0:
                raise ValueError("score value must be between 0 and 1")
        _require_short_text("reason", self.reason, maximum=200)

    @property
    def passed(self) -> bool:
        """Whether this scorer accepted the case."""

        return self.status is ScoreStatus.PASSED

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "scorer": self.scorer,
            "status": self.status.value,
            "value": round(self.value, 4) if self.value is not None else None,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvaluationScore:
        """Load a score from a report artifact."""

        raw_value = value.get("value")
        return cls(
            scorer=_required_string(value, "scorer"),
            status=ScoreStatus(_required_string(value, "status")),
            value=None if raw_value is None else _required_float(value, "value"),
            reason=_required_string(value, "reason"),
        )


@dataclass(frozen=True, slots=True)
class EvaluationCase:
    """One bounded input and its optional deterministic expectations."""

    case_id: str
    task: str
    prompt: str = field(repr=False)
    expected_text: str | None = field(default=None, repr=False)
    required_substrings: tuple[str, ...] = field(default=(), repr=False)
    forbidden_substrings: tuple[str, ...] = field(default=(), repr=False)
    required_json_keys: tuple[str, ...] = field(default=(), repr=False)
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_name("case_id", self.case_id)
        _require_short_text("task", self.task, maximum=500)
        _require_short_text("prompt", self.prompt, maximum=1_000_000)
        if self.expected_text is not None:
            _require_short_text("expected_text", self.expected_text, maximum=2_000_000)
        if len(self.required_substrings) > 100:
            raise ValueError("required_substrings cannot contain more than 100 values")
        for required in self.required_substrings:
            _require_short_text("required substring", required, maximum=10_000)
        if len(set(self.required_substrings)) != len(self.required_substrings):
            raise ValueError("required_substrings must not contain duplicates")
        if len(self.forbidden_substrings) > 100:
            raise ValueError("forbidden_substrings cannot contain more than 100 values")
        for forbidden in self.forbidden_substrings:
            _require_short_text("forbidden substring", forbidden, maximum=10_000)
        if len(set(self.forbidden_substrings)) != len(self.forbidden_substrings):
            raise ValueError("forbidden_substrings must not contain duplicates")
        if len(self.required_json_keys) > 100:
            raise ValueError("required_json_keys cannot contain more than 100 values")
        for key in self.required_json_keys:
            _require_short_text("required JSON key", key, maximum=500)
        if len(set(self.required_json_keys)) != len(self.required_json_keys):
            raise ValueError("required_json_keys must not contain duplicates")
        if len(self.tags) > 50:
            raise ValueError("tags cannot contain more than 50 values")
        for tag in self.tags:
            _require_name("tag", tag)
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tags must not contain duplicates")


class Scorer(Protocol):
    """Application-supplied deterministic evaluation scorer."""

    @property
    def name(self) -> str:
        """Stable scorer name used in report artifacts."""

        ...

    def score(self, case: EvaluationCase, result: ConsensusResult) -> EvaluationScore:
        """Score one consensus result without side effects."""

        ...


@dataclass(frozen=True, slots=True)
class ConsensusReachedScorer:
    """Require the configured council quorum to select an answer."""

    name: str = "consensus_reached"

    def score(self, case: EvaluationCase, result: ConsensusResult) -> EvaluationScore:
        del case
        passed = result.consensus_text is not None
        return EvaluationScore(
            scorer=self.name,
            status=ScoreStatus.PASSED if passed else ScoreStatus.FAILED,
            value=result.agreement_ratio,
            reason="consensus_selected" if passed else "consensus_absent",
        )


@dataclass(frozen=True, slots=True)
class ExactMatchScorer:
    """Compare selected consensus text with an optional case reference."""

    case_sensitive: bool = False
    normalize_whitespace: bool = True
    name: str = "exact_match"

    def score(self, case: EvaluationCase, result: ConsensusResult) -> EvaluationScore:
        if case.expected_text is None:
            return EvaluationScore(self.name, ScoreStatus.SKIPPED, None, "reference_absent")
        if result.consensus_text is None:
            return EvaluationScore(self.name, ScoreStatus.FAILED, 0.0, "consensus_absent")
        actual = _normalize_text(
            result.consensus_text,
            case_sensitive=self.case_sensitive,
            normalize_whitespace=self.normalize_whitespace,
        )
        expected = _normalize_text(
            case.expected_text,
            case_sensitive=self.case_sensitive,
            normalize_whitespace=self.normalize_whitespace,
        )
        passed = actual == expected
        return EvaluationScore(
            self.name,
            ScoreStatus.PASSED if passed else ScoreStatus.FAILED,
            1.0 if passed else 0.0,
            "matched" if passed else "mismatch",
        )


@dataclass(frozen=True, slots=True)
class ContainsScorer:
    """Require every case substring to appear in the selected answer."""

    case_sensitive: bool = False
    name: str = "contains_required"

    def score(self, case: EvaluationCase, result: ConsensusResult) -> EvaluationScore:
        if not case.required_substrings:
            return EvaluationScore(self.name, ScoreStatus.SKIPPED, None, "requirements_absent")
        if result.consensus_text is None:
            return EvaluationScore(self.name, ScoreStatus.FAILED, 0.0, "consensus_absent")
        actual = result.consensus_text if self.case_sensitive else result.consensus_text.casefold()
        required = (
            case.required_substrings
            if self.case_sensitive
            else tuple(value.casefold() for value in case.required_substrings)
        )
        matches = sum(value in actual for value in required)
        value = matches / len(required)
        passed = matches == len(required)
        return EvaluationScore(
            self.name,
            ScoreStatus.PASSED if passed else ScoreStatus.FAILED,
            value,
            "all_present" if passed else "required_content_missing",
        )


@dataclass(frozen=True, slots=True)
class ExcludesScorer:
    """Reject selected answers containing any case-forbidden substring."""

    case_sensitive: bool = False
    name: str = "excludes_forbidden"

    def score(self, case: EvaluationCase, result: ConsensusResult) -> EvaluationScore:
        if not case.forbidden_substrings:
            return EvaluationScore(self.name, ScoreStatus.SKIPPED, None, "requirements_absent")
        if result.consensus_text is None:
            return EvaluationScore(self.name, ScoreStatus.FAILED, 0.0, "consensus_absent")
        actual = result.consensus_text if self.case_sensitive else result.consensus_text.casefold()
        forbidden = (
            case.forbidden_substrings
            if self.case_sensitive
            else tuple(value.casefold() for value in case.forbidden_substrings)
        )
        matches = sum(value in actual for value in forbidden)
        passed = matches == 0
        return EvaluationScore(
            self.name,
            ScoreStatus.PASSED if passed else ScoreStatus.FAILED,
            1.0 - (matches / len(forbidden)),
            "all_absent" if passed else "forbidden_content_present",
        )


@dataclass(frozen=True, slots=True)
class JsonObjectScorer:
    """Require valid JSON object output containing configured top-level keys."""

    name: str = "json_object"

    def score(self, case: EvaluationCase, result: ConsensusResult) -> EvaluationScore:
        if not case.required_json_keys:
            return EvaluationScore(self.name, ScoreStatus.SKIPPED, None, "requirements_absent")
        if result.consensus_text is None:
            return EvaluationScore(self.name, ScoreStatus.FAILED, 0.0, "consensus_absent")
        try:
            parsed = json.loads(result.consensus_text)
        except json.JSONDecodeError:
            return EvaluationScore(self.name, ScoreStatus.FAILED, 0.0, "invalid_json")
        if not isinstance(parsed, dict):
            return EvaluationScore(self.name, ScoreStatus.FAILED, 0.0, "not_json_object")
        matches = sum(key in parsed for key in case.required_json_keys)
        value = matches / len(case.required_json_keys)
        passed = matches == len(case.required_json_keys)
        return EvaluationScore(
            self.name,
            ScoreStatus.PASSED if passed else ScoreStatus.FAILED,
            value,
            "all_keys_present" if passed else "required_key_missing",
        )


@dataclass(frozen=True, slots=True)
class EvaluationRunConfig:
    """Resource ceilings for executing a complete evaluation suite."""

    max_cases: int = 1_000
    max_case_concurrency: int = 1
    max_total_provider_calls: int = 10_000

    def __post_init__(self) -> None:
        _require_int("max_cases", self.max_cases, minimum=1, maximum=_MAX_CASES_HARD_LIMIT)
        _require_int("max_case_concurrency", self.max_case_concurrency, minimum=1, maximum=32)
        _require_int(
            "max_total_provider_calls",
            self.max_total_provider_calls,
            minimum=2,
            maximum=10_000_000,
        )


@dataclass(frozen=True, slots=True)
class GateViolation:
    """One machine-readable reason an evaluation did not pass policy."""

    metric: str
    actual: str
    operator: str
    threshold: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        """Return a JSON-serializable representation."""

        return {
            "metric": self.metric,
            "actual": self.actual,
            "operator": self.operator,
            "threshold": self.threshold,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> GateViolation:
        """Load a violation from a report artifact."""

        return cls(
            metric=_required_string(value, "metric"),
            actual=_required_string(value, "actual"),
            operator=_required_string(value, "operator"),
            threshold=_required_string(value, "threshold"),
            reason=_required_string(value, "reason"),
        )


@dataclass(frozen=True, slots=True)
class EvaluationPolicy:
    """Acceptance thresholds evaluated after every case completes."""

    minimum_case_pass_rate: float = 1.0
    minimum_mean_agreement_ratio: float = 0.0
    maximum_provider_failure_rate: float = 1.0
    maximum_total_cost_usd: Decimal | None = None
    maximum_p95_duration_ms: int | None = None
    require_complete_cost_reporting: bool = False

    def __post_init__(self) -> None:
        _require_ratio("minimum_case_pass_rate", self.minimum_case_pass_rate)
        _require_ratio("minimum_mean_agreement_ratio", self.minimum_mean_agreement_ratio)
        _require_ratio("maximum_provider_failure_rate", self.maximum_provider_failure_rate)
        if self.maximum_total_cost_usd is not None:
            if not isinstance(self.maximum_total_cost_usd, Decimal):
                raise TypeError("maximum_total_cost_usd must be a Decimal or None")
            if not self.maximum_total_cost_usd.is_finite() or self.maximum_total_cost_usd < 0:
                raise ValueError("maximum_total_cost_usd must be non-negative")
        if self.maximum_p95_duration_ms is not None:
            _require_int(
                "maximum_p95_duration_ms",
                self.maximum_p95_duration_ms,
                minimum=0,
                maximum=86_400_000,
            )

    def violations(self, summary: EvaluationSummary) -> tuple[GateViolation, ...]:
        """Return every threshold violated by an aggregate summary."""

        violations: list[GateViolation] = []
        _append_minimum_violation(
            violations,
            "case_pass_rate",
            summary.case_pass_rate,
            self.minimum_case_pass_rate,
        )
        _append_minimum_violation(
            violations,
            "mean_agreement_ratio",
            summary.mean_agreement_ratio,
            self.minimum_mean_agreement_ratio,
        )
        _append_maximum_violation(
            violations,
            "provider_failure_rate",
            summary.provider_failure_rate,
            self.maximum_provider_failure_rate,
        )
        if self.maximum_p95_duration_ms is not None and (
            summary.p95_duration_ms > self.maximum_p95_duration_ms
        ):
            violations.append(
                GateViolation(
                    metric="p95_duration_ms",
                    actual=str(summary.p95_duration_ms),
                    operator="<=",
                    threshold=str(self.maximum_p95_duration_ms),
                    reason="latency_budget_exceeded",
                )
            )
        if self.require_complete_cost_reporting and not summary.cost_reporting_complete:
            violations.append(
                GateViolation(
                    metric="cost_reporting_complete",
                    actual="false",
                    operator="==",
                    threshold="true",
                    reason="cost_reporting_incomplete",
                )
            )
        if self.maximum_total_cost_usd is not None:
            if not summary.cost_reporting_complete:
                violations.append(
                    GateViolation(
                        metric="total_reported_cost_usd",
                        actual="incomplete",
                        operator="<=",
                        threshold=str(self.maximum_total_cost_usd),
                        reason="cost_budget_unverifiable",
                    )
                )
            elif summary.total_reported_cost_usd > self.maximum_total_cost_usd:
                violations.append(
                    GateViolation(
                        metric="total_reported_cost_usd",
                        actual=str(summary.total_reported_cost_usd),
                        operator="<=",
                        threshold=str(self.maximum_total_cost_usd),
                        reason="cost_budget_exceeded",
                    )
                )
        return tuple(violations)


@dataclass(frozen=True, slots=True)
class EvaluationCaseResult:
    """Privacy-minimized result for one evaluation case."""

    case_id: str
    task: str
    tags: tuple[str, ...]
    passed: bool
    scores: tuple[EvaluationScore, ...]
    duration_ms: int
    agreement_level: str
    agreement_ratio: float
    providers_queried: int
    providers_succeeded: int
    provider_errors: int
    provider_timeouts: int
    invalid_responses: int
    reported_cost_usd: Decimal
    cost_reporting_complete: bool
    consensus_sha256: str | None

    def __post_init__(self) -> None:
        _require_name("case_id", self.case_id)
        _require_short_text("task", self.task, maximum=500)
        _require_int("duration_ms", self.duration_ms, minimum=0, maximum=86_400_000)
        _require_ratio("agreement_ratio", self.agreement_ratio)
        for name, value in (
            ("providers_queried", self.providers_queried),
            ("providers_succeeded", self.providers_succeeded),
            ("provider_errors", self.provider_errors),
            ("provider_timeouts", self.provider_timeouts),
            ("invalid_responses", self.invalid_responses),
        ):
            _require_int(name, value, minimum=0, maximum=32)
        if self.providers_succeeded > self.providers_queried:
            raise ValueError("providers_succeeded cannot exceed providers_queried")
        categorized = (
            self.providers_succeeded
            + self.provider_errors
            + self.provider_timeouts
            + self.invalid_responses
        )
        if categorized != self.providers_queried:
            raise ValueError("provider outcome counts must equal providers_queried")
        if not self.reported_cost_usd.is_finite() or self.reported_cost_usd < 0:
            raise ValueError("reported_cost_usd must be non-negative")
        if self.consensus_sha256 is not None and (
            len(self.consensus_sha256) != 64
            or any(character not in "0123456789abcdef" for character in self.consensus_sha256)
        ):
            raise ValueError("consensus_sha256 must be a lowercase SHA-256 digest or None")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation without prompt content."""

        return {
            "case_id": self.case_id,
            "task": self.task,
            "tags": list(self.tags),
            "passed": self.passed,
            "scores": [score.to_dict() for score in self.scores],
            "duration_ms": self.duration_ms,
            "agreement_level": self.agreement_level,
            "agreement_ratio": round(self.agreement_ratio, 4),
            "providers_queried": self.providers_queried,
            "providers_succeeded": self.providers_succeeded,
            "provider_errors": self.provider_errors,
            "provider_timeouts": self.provider_timeouts,
            "invalid_responses": self.invalid_responses,
            "reported_cost_usd": str(self.reported_cost_usd),
            "cost_reporting_complete": self.cost_reporting_complete,
            "consensus_sha256": self.consensus_sha256,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvaluationCaseResult:
        """Load a case result from a report artifact."""

        raw_scores = _required_list(value, "scores")
        raw_tags = _required_list(value, "tags")
        return cls(
            case_id=_required_string(value, "case_id"),
            task=_required_string(value, "task"),
            tags=tuple(_list_strings(raw_tags, "tags")),
            passed=_required_bool(value, "passed"),
            scores=tuple(EvaluationScore.from_dict(_mapping(item, "score")) for item in raw_scores),
            duration_ms=_required_int(value, "duration_ms"),
            agreement_level=_required_string(value, "agreement_level"),
            agreement_ratio=_required_float(value, "agreement_ratio"),
            providers_queried=_required_int(value, "providers_queried"),
            providers_succeeded=_required_int(value, "providers_succeeded"),
            provider_errors=_required_int(value, "provider_errors"),
            provider_timeouts=_required_int(value, "provider_timeouts"),
            invalid_responses=_required_int(value, "invalid_responses"),
            reported_cost_usd=_required_decimal(value, "reported_cost_usd"),
            cost_reporting_complete=_required_bool(value, "cost_reporting_complete"),
            consensus_sha256=_optional_string(value, "consensus_sha256"),
        )


@dataclass(frozen=True, slots=True)
class EvaluationSummary:
    """Aggregate quality, reliability, cost, and latency metrics."""

    cases_total: int
    cases_passed: int
    case_pass_rate: float
    mean_agreement_ratio: float
    provider_failure_rate: float
    p95_duration_ms: int
    total_reported_cost_usd: Decimal
    cost_reporting_complete: bool

    def __post_init__(self) -> None:
        _require_int("cases_total", self.cases_total, minimum=1, maximum=_MAX_CASES_HARD_LIMIT)
        _require_int(
            "cases_passed",
            self.cases_passed,
            minimum=0,
            maximum=self.cases_total,
        )
        _require_ratio("case_pass_rate", self.case_pass_rate)
        _require_ratio("mean_agreement_ratio", self.mean_agreement_ratio)
        _require_ratio("provider_failure_rate", self.provider_failure_rate)
        _require_int("p95_duration_ms", self.p95_duration_ms, minimum=0, maximum=86_400_000)
        if not self.total_reported_cost_usd.is_finite() or self.total_reported_cost_usd < 0:
            raise ValueError("total_reported_cost_usd must be non-negative")

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable representation."""

        return {
            "cases_total": self.cases_total,
            "cases_passed": self.cases_passed,
            "case_pass_rate": round(self.case_pass_rate, 4),
            "mean_agreement_ratio": round(self.mean_agreement_ratio, 4),
            "provider_failure_rate": round(self.provider_failure_rate, 4),
            "p95_duration_ms": self.p95_duration_ms,
            "total_reported_cost_usd": str(self.total_reported_cost_usd),
            "cost_reporting_complete": self.cost_reporting_complete,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvaluationSummary:
        """Load a summary from a report artifact."""

        return cls(
            cases_total=_required_int(value, "cases_total"),
            cases_passed=_required_int(value, "cases_passed"),
            case_pass_rate=_required_float(value, "case_pass_rate"),
            mean_agreement_ratio=_required_float(value, "mean_agreement_ratio"),
            provider_failure_rate=_required_float(value, "provider_failure_rate"),
            p95_duration_ms=_required_int(value, "p95_duration_ms"),
            total_reported_cost_usd=_required_decimal(value, "total_reported_cost_usd"),
            cost_reporting_complete=_required_bool(value, "cost_reporting_complete"),
        )


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    """Versioned, privacy-minimized artifact for one evaluation experiment."""

    SCHEMA_VERSION: ClassVar[str] = "neural-mesh-evaluation/v1"

    suite_name: str
    created_at: datetime
    duration_ms: int
    provider_names: tuple[str, ...]
    case_results: tuple[EvaluationCaseResult, ...]
    summary: EvaluationSummary
    gate_violations: tuple[GateViolation, ...]

    def __post_init__(self) -> None:
        _require_name("suite_name", self.suite_name)
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must include a timezone")
        _require_int("duration_ms", self.duration_ms, minimum=0, maximum=86_400_000)
        if not self.provider_names or len(self.provider_names) > 32:
            raise ValueError("provider_names must contain between 1 and 32 values")
        for provider_name in self.provider_names:
            _require_name("provider name", provider_name)
        if len(set(self.provider_names)) != len(self.provider_names):
            raise ValueError("provider_names must be unique")
        case_ids = tuple(case.case_id for case in self.case_results)
        if not case_ids or len(set(case_ids)) != len(case_ids):
            raise ValueError("case results must be non-empty with unique ids")
        expected = _summarize(self.case_results)
        if not _summaries_match(self.summary, expected):
            raise ValueError("report summary does not match case results")

    @property
    def passed(self) -> bool:
        """Whether the aggregate policy accepted this experiment."""

        return not self.gate_violations

    def to_dict(self) -> dict[str, object]:
        """Return the stable JSON artifact contract."""

        return {
            "schema_version": self.SCHEMA_VERSION,
            "suite_name": self.suite_name,
            "created_at": self.created_at.isoformat(),
            "duration_ms": self.duration_ms,
            "provider_names": list(self.provider_names),
            "passed": self.passed,
            "summary": self.summary.to_dict(),
            "gate_violations": [violation.to_dict() for violation in self.gate_violations],
            "cases": [case.to_dict() for case in self.case_results],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EvaluationReport:
        """Load and validate a report produced by :meth:`to_dict`."""

        schema = _required_string(value, "schema_version")
        if schema != cls.SCHEMA_VERSION:
            raise ValueError(f"unsupported report schema: {schema}")
        raw_cases = _required_list(value, "cases")
        raw_violations = _required_list(value, "gate_violations")
        raw_providers = _required_list(value, "provider_names")
        created_at = datetime.fromisoformat(_required_string(value, "created_at"))
        if created_at.tzinfo is None:
            raise ValueError("report created_at must include a timezone")
        report = cls(
            suite_name=_required_string(value, "suite_name"),
            created_at=created_at,
            duration_ms=_required_int(value, "duration_ms"),
            provider_names=tuple(_list_strings(raw_providers, "provider_names")),
            case_results=tuple(
                EvaluationCaseResult.from_dict(_mapping(item, "case")) for item in raw_cases
            ),
            summary=EvaluationSummary.from_dict(_mapping(value.get("summary"), "summary")),
            gate_violations=tuple(
                GateViolation.from_dict(_mapping(item, "gate violation")) for item in raw_violations
            ),
        )
        if _required_bool(value, "passed") != report.passed:
            raise ValueError("report passed flag does not match gate violations")
        return report


@dataclass(frozen=True, slots=True)
class ComparisonPolicy:
    """Allowed regression budget between a baseline and candidate report."""

    maximum_pass_rate_drop: float = 0.0
    maximum_agreement_drop: float = 0.0
    maximum_failure_rate_increase: float = 0.0
    maximum_cost_increase_usd: Decimal | None = None
    maximum_p95_duration_increase_ms: int | None = None
    maximum_regressed_cases: int = 0

    def __post_init__(self) -> None:
        _require_ratio("maximum_pass_rate_drop", self.maximum_pass_rate_drop)
        _require_ratio("maximum_agreement_drop", self.maximum_agreement_drop)
        _require_ratio("maximum_failure_rate_increase", self.maximum_failure_rate_increase)
        if self.maximum_cost_increase_usd is not None:
            if not isinstance(self.maximum_cost_increase_usd, Decimal):
                raise TypeError("maximum_cost_increase_usd must be a Decimal or None")
            if not self.maximum_cost_increase_usd.is_finite() or self.maximum_cost_increase_usd < 0:
                raise ValueError("maximum_cost_increase_usd must be non-negative")
        if self.maximum_p95_duration_increase_ms is not None:
            _require_int(
                "maximum_p95_duration_increase_ms",
                self.maximum_p95_duration_increase_ms,
                minimum=0,
                maximum=86_400_000,
            )
        _require_int(
            "maximum_regressed_cases",
            self.maximum_regressed_cases,
            minimum=0,
            maximum=_MAX_CASES_HARD_LIMIT,
        )


@dataclass(frozen=True, slots=True)
class ReportComparison:
    """Machine-readable regression result for two compatible experiments."""

    SCHEMA_VERSION: ClassVar[str] = "neural-mesh-comparison/v1"

    baseline_suite: str
    candidate_suite: str
    pass_rate_delta: float
    agreement_delta: float
    failure_rate_delta: float
    cost_delta_usd: Decimal | None
    p95_duration_delta_ms: int
    regressed_cases: tuple[str, ...]
    improved_cases: tuple[str, ...]
    violations: tuple[GateViolation, ...]

    @property
    def passed(self) -> bool:
        """Whether the candidate stays within every regression budget."""

        return not self.violations

    def to_dict(self) -> dict[str, object]:
        """Return a JSON-serializable comparison artifact."""

        return {
            "schema_version": self.SCHEMA_VERSION,
            "baseline_suite": self.baseline_suite,
            "candidate_suite": self.candidate_suite,
            "passed": self.passed,
            "pass_rate_delta": round(self.pass_rate_delta, 4),
            "agreement_delta": round(self.agreement_delta, 4),
            "failure_rate_delta": round(self.failure_rate_delta, 4),
            "cost_delta_usd": str(self.cost_delta_usd) if self.cost_delta_usd is not None else None,
            "p95_duration_delta_ms": self.p95_duration_delta_ms,
            "regressed_cases": list(self.regressed_cases),
            "improved_cases": list(self.improved_cases),
            "violations": [violation.to_dict() for violation in self.violations],
        }


class EvaluationRunner:
    """Execute bounded evaluation cases against one consensus engine."""

    def __init__(
        self,
        engine: ConsensusEngine,
        *,
        scorers: Sequence[Scorer] | None = None,
        policy: EvaluationPolicy | None = None,
        config: EvaluationRunConfig | None = None,
    ) -> None:
        self._engine = engine
        self._scorers = tuple(
            scorers
            if scorers is not None
            else (
                ConsensusReachedScorer(),
                ExactMatchScorer(),
                ContainsScorer(),
                ExcludesScorer(),
                JsonObjectScorer(),
            )
        )
        if not self._scorers:
            raise ValueError("scorers must contain at least one scorer")
        scorer_names = tuple(scorer.name for scorer in self._scorers)
        for name in scorer_names:
            _require_name("scorer name", name)
        if len(set(scorer_names)) != len(scorer_names):
            raise ValueError("scorer names must be unique")
        self._policy = policy or EvaluationPolicy()
        self._config = config or EvaluationRunConfig()

    async def run(
        self,
        suite_name: str,
        cases: Sequence[EvaluationCase],
    ) -> EvaluationReport:
        """Run all cases and return a privacy-minimized evaluation artifact."""

        _require_name("suite_name", suite_name)
        selected = tuple(cases)
        if not selected:
            raise ValueError("cases must not be empty")
        if len(selected) > self._config.max_cases:
            raise ValueError("case count exceeds max_cases")
        if any(not isinstance(case, EvaluationCase) for case in selected):
            raise TypeError("cases must contain only EvaluationCase values")
        case_ids = tuple(case.case_id for case in selected)
        if len(set(case_ids)) != len(case_ids):
            raise ValueError("case ids must be unique")
        provider_calls = len(selected) * len(self._engine.provider_names)
        if provider_calls > self._config.max_total_provider_calls:
            raise ValueError("suite exceeds max_total_provider_calls")

        started = time.perf_counter()
        created_at = datetime.now(timezone.utc)
        semaphore = asyncio.Semaphore(self._config.max_case_concurrency)
        tasks = [
            asyncio.create_task(
                self._run_case(case, semaphore),
                name=f"neural-mesh-eval:{case.case_id}",
            )
            for case in selected
        ]
        try:
            case_results = tuple(await asyncio.gather(*tasks))
        except BaseException:
            for case_task in tasks:
                if not case_task.done():
                    case_task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

        summary = _summarize(case_results)
        return EvaluationReport(
            suite_name=suite_name,
            created_at=created_at,
            duration_ms=_elapsed_ms(started),
            provider_names=self._engine.provider_names,
            case_results=case_results,
            summary=summary,
            gate_violations=self._policy.violations(summary),
        )

    async def _run_case(
        self,
        case: EvaluationCase,
        semaphore: asyncio.Semaphore,
    ) -> EvaluationCaseResult:
        async with semaphore:
            result = await self._engine.run(case.task, case.prompt)
        scores = tuple(self._apply_scorer(scorer, case, result) for scorer in self._scorers)
        applied = tuple(score for score in scores if score.status is not ScoreStatus.SKIPPED)
        passed = bool(applied) and all(score.passed for score in applied)
        return _case_result(case, result, scores, passed)

    @staticmethod
    def _apply_scorer(
        scorer: Scorer,
        case: EvaluationCase,
        result: ConsensusResult,
    ) -> EvaluationScore:
        try:
            score = scorer.score(case, result)
            if score.scorer != scorer.name:
                raise ValueError("scorer returned a mismatched name")
        except Exception as error:
            logger.warning("Scorer %s failed with %s", scorer.name, type(error).__name__)
            return EvaluationScore(scorer.name, ScoreStatus.ERROR, None, "scorer_error")
        return score


@dataclass(frozen=True, slots=True)
class ReplaySuite:
    """Credential-free provider responses and evaluation configuration."""

    SCHEMA_VERSION: ClassVar[str] = "neural-mesh-replay-suite/v1"

    name: str
    cases: tuple[EvaluationCase, ...]
    provider_responses: Mapping[str, Mapping[str, ProviderResponse | str]] = field(repr=False)
    consensus_config: ConsensusConfig = field(default_factory=ConsensusConfig)
    policy: EvaluationPolicy = field(default_factory=EvaluationPolicy)
    run_config: EvaluationRunConfig = field(default_factory=EvaluationRunConfig)

    def build_runner(self) -> EvaluationRunner:
        """Create a runner backed by deterministic replay providers."""

        providers = [
            CallableProvider(name, _replay_completion(responses))
            for name, responses in self.provider_responses.items()
        ]
        engine = ConsensusEngine(providers, self.consensus_config)
        return EvaluationRunner(engine, policy=self.policy, config=self.run_config)


def load_replay_suite(path: str | Path) -> ReplaySuite:
    """Load a bounded, versioned replay suite from JSON."""

    source = Path(path)
    raw = source.read_bytes()
    if len(raw) > _MAX_REPLAY_BYTES:
        raise ValueError("replay suite exceeds the 50 MB limit")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("replay suite must be valid UTF-8 JSON") from error
    root = _mapping(parsed, "replay suite")
    schema = _required_string(root, "schema_version")
    if schema != ReplaySuite.SCHEMA_VERSION:
        raise ValueError(f"unsupported replay suite schema: {schema}")
    return _parse_replay_suite(root)


def load_evaluation_report(path: str | Path) -> EvaluationReport:
    """Load a bounded evaluation report for baseline comparison."""

    raw = Path(path).read_bytes()
    if len(raw) > _MAX_REPORT_BYTES:
        raise ValueError("evaluation report exceeds the 100 MB limit")
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError("evaluation report must be valid UTF-8 JSON") from error
    return EvaluationReport.from_dict(_mapping(parsed, "evaluation report"))


def compare_reports(
    baseline: EvaluationReport,
    candidate: EvaluationReport,
    policy: ComparisonPolicy | None = None,
) -> ReportComparison:
    """Compare compatible case sets and enforce an allowed regression budget."""

    selected_policy = policy or ComparisonPolicy()
    baseline_cases = {case.case_id: case for case in baseline.case_results}
    candidate_cases = {case.case_id: case for case in candidate.case_results}
    if baseline_cases.keys() != candidate_cases.keys():
        raise ValueError("baseline and candidate reports must contain the same case ids")
    regressed = tuple(
        case_id
        for case_id in baseline_cases
        if baseline_cases[case_id].passed and not candidate_cases[case_id].passed
    )
    improved = tuple(
        case_id
        for case_id in baseline_cases
        if not baseline_cases[case_id].passed and candidate_cases[case_id].passed
    )
    pass_delta = candidate.summary.case_pass_rate - baseline.summary.case_pass_rate
    agreement_delta = candidate.summary.mean_agreement_ratio - baseline.summary.mean_agreement_ratio
    failure_delta = candidate.summary.provider_failure_rate - baseline.summary.provider_failure_rate
    duration_delta = candidate.summary.p95_duration_ms - baseline.summary.p95_duration_ms
    cost_delta: Decimal | None = None
    if baseline.summary.cost_reporting_complete and candidate.summary.cost_reporting_complete:
        cost_delta = (
            candidate.summary.total_reported_cost_usd - baseline.summary.total_reported_cost_usd
        )

    violations: list[GateViolation] = []
    if -pass_delta > selected_policy.maximum_pass_rate_drop:
        violations.append(
            _comparison_violation(
                "pass_rate_delta",
                pass_delta,
                f">= -{selected_policy.maximum_pass_rate_drop}",
                "pass_rate_regressed",
            )
        )
    if -agreement_delta > selected_policy.maximum_agreement_drop:
        violations.append(
            _comparison_violation(
                "agreement_delta",
                agreement_delta,
                f">= -{selected_policy.maximum_agreement_drop}",
                "agreement_regressed",
            )
        )
    if failure_delta > selected_policy.maximum_failure_rate_increase:
        violations.append(
            _comparison_violation(
                "failure_rate_delta",
                failure_delta,
                f"<= {selected_policy.maximum_failure_rate_increase}",
                "provider_failures_increased",
            )
        )
    if len(regressed) > selected_policy.maximum_regressed_cases:
        violations.append(
            GateViolation(
                metric="regressed_cases",
                actual=str(len(regressed)),
                operator="<=",
                threshold=str(selected_policy.maximum_regressed_cases),
                reason="case_regression_budget_exceeded",
            )
        )
    if selected_policy.maximum_p95_duration_increase_ms is not None and (
        duration_delta > selected_policy.maximum_p95_duration_increase_ms
    ):
        violations.append(
            GateViolation(
                metric="p95_duration_delta_ms",
                actual=str(duration_delta),
                operator="<=",
                threshold=str(selected_policy.maximum_p95_duration_increase_ms),
                reason="latency_regressed",
            )
        )
    if selected_policy.maximum_cost_increase_usd is not None:
        if cost_delta is None:
            violations.append(
                GateViolation(
                    metric="cost_delta_usd",
                    actual="incomplete",
                    operator="<=",
                    threshold=str(selected_policy.maximum_cost_increase_usd),
                    reason="cost_regression_unverifiable",
                )
            )
        elif cost_delta > selected_policy.maximum_cost_increase_usd:
            violations.append(
                GateViolation(
                    metric="cost_delta_usd",
                    actual=str(cost_delta),
                    operator="<=",
                    threshold=str(selected_policy.maximum_cost_increase_usd),
                    reason="cost_regressed",
                )
            )

    return ReportComparison(
        baseline_suite=baseline.suite_name,
        candidate_suite=candidate.suite_name,
        pass_rate_delta=pass_delta,
        agreement_delta=agreement_delta,
        failure_rate_delta=failure_delta,
        cost_delta_usd=cost_delta,
        p95_duration_delta_ms=duration_delta,
        regressed_cases=regressed,
        improved_cases=improved,
        violations=tuple(violations),
    )


def _parse_replay_suite(root: Mapping[str, object]) -> ReplaySuite:
    raw_cases = _required_list(root, "cases")
    if not raw_cases:
        raise ValueError("replay suite cases must not be empty")
    if len(raw_cases) > _MAX_CASES_HARD_LIMIT:
        raise ValueError("replay suite exceeds the hard case limit")
    cases: list[EvaluationCase] = []
    provider_responses: dict[str, dict[str, ProviderResponse | str]] = {}
    prompts: set[str] = set()
    for raw_case in raw_cases:
        case_value = _mapping(raw_case, "case")
        prompt = _required_string(case_value, "prompt")
        if prompt in prompts:
            raise ValueError("replay suite prompts must be unique")
        prompts.add(prompt)
        required = _list_strings(
            _optional_list(case_value, "required_substrings"),
            "required_substrings",
        )
        forbidden = _list_strings(
            _optional_list(case_value, "forbidden_substrings"),
            "forbidden_substrings",
        )
        required_json_keys = _list_strings(
            _optional_list(case_value, "required_json_keys"),
            "required_json_keys",
        )
        tags = _list_strings(_optional_list(case_value, "tags"), "tags")
        case = EvaluationCase(
            case_id=_required_string(case_value, "id"),
            task=_required_string(case_value, "task"),
            prompt=prompt,
            expected_text=_optional_string(case_value, "expected_text"),
            required_substrings=tuple(required),
            forbidden_substrings=tuple(forbidden),
            required_json_keys=tuple(required_json_keys),
            tags=tuple(tags),
        )
        cases.append(case)
        raw_responses = _mapping(case_value.get("responses"), "responses")
        if len(raw_responses) < 2:
            raise ValueError("each replay case must contain at least two provider responses")
        for provider_name, raw_response in raw_responses.items():
            _require_name("provider name", provider_name)
            provider_responses.setdefault(provider_name, {})[prompt] = _parse_provider_response(
                raw_response
            )

    if len(provider_responses) > 32:
        raise ValueError("replay suite cannot contain more than 32 providers")
    if any(len(responses) != len(cases) for responses in provider_responses.values()):
        raise ValueError("every replay provider must have a response for every case")

    consensus_config = _parse_consensus_config(_optional_mapping(root, "consensus_config"))
    policy = _parse_evaluation_policy(_optional_mapping(root, "policy"))
    run_config = _parse_run_config(_optional_mapping(root, "run_config"))
    return ReplaySuite(
        name=_required_string(root, "name"),
        cases=tuple(cases),
        provider_responses=provider_responses,
        consensus_config=consensus_config,
        policy=policy,
        run_config=run_config,
    )


def _parse_provider_response(value: object) -> ProviderResponse | str:
    if isinstance(value, str):
        return value
    response = _mapping(value, "provider response")
    raw_input_tokens = response.get("input_tokens")
    raw_output_tokens = response.get("output_tokens")
    raw_cost = response.get("cost_usd")
    return ProviderResponse(
        content=_required_string(response, "content"),
        model=_optional_string(response, "model"),
        input_tokens=(
            None if raw_input_tokens is None else _required_int(response, "input_tokens")
        ),
        output_tokens=(
            None if raw_output_tokens is None else _required_int(response, "output_tokens")
        ),
        cost_usd=None if raw_cost is None else _required_decimal(response, "cost_usd"),
    )


def _parse_consensus_config(value: Mapping[str, object]) -> ConsensusConfig:
    defaults = ConsensusConfig()
    return ConsensusConfig(
        max_providers=_optional_int(value, "max_providers", defaults.max_providers),
        max_concurrency=_optional_int(value, "max_concurrency", defaults.max_concurrency),
        timeout_seconds=_optional_float(value, "timeout_seconds", defaults.timeout_seconds),
        max_tokens_per_provider=_optional_int(
            value,
            "max_tokens_per_provider",
            defaults.max_tokens_per_provider,
        ),
        max_prompt_chars=_optional_int(value, "max_prompt_chars", defaults.max_prompt_chars),
        max_response_chars=_optional_int(
            value,
            "max_response_chars",
            defaults.max_response_chars,
        ),
        minimum_successful_responses=_optional_int(
            value,
            "minimum_successful_responses",
            defaults.minimum_successful_responses,
        ),
        minimum_cluster_size=_optional_int(
            value,
            "minimum_cluster_size",
            defaults.minimum_cluster_size,
        ),
        minimum_agreement_ratio=_optional_float(
            value,
            "minimum_agreement_ratio",
            defaults.minimum_agreement_ratio,
        ),
        similarity_threshold=_optional_float(
            value,
            "similarity_threshold",
            defaults.similarity_threshold,
        ),
    )


def _parse_evaluation_policy(value: Mapping[str, object]) -> EvaluationPolicy:
    defaults = EvaluationPolicy()
    raw_cost = value.get("maximum_total_cost_usd")
    raw_latency = value.get("maximum_p95_duration_ms")
    return EvaluationPolicy(
        minimum_case_pass_rate=_optional_float(
            value,
            "minimum_case_pass_rate",
            defaults.minimum_case_pass_rate,
        ),
        minimum_mean_agreement_ratio=_optional_float(
            value,
            "minimum_mean_agreement_ratio",
            defaults.minimum_mean_agreement_ratio,
        ),
        maximum_provider_failure_rate=_optional_float(
            value,
            "maximum_provider_failure_rate",
            defaults.maximum_provider_failure_rate,
        ),
        maximum_total_cost_usd=(
            None if raw_cost is None else _required_decimal(value, "maximum_total_cost_usd")
        ),
        maximum_p95_duration_ms=(
            None if raw_latency is None else _required_int(value, "maximum_p95_duration_ms")
        ),
        require_complete_cost_reporting=_optional_bool(
            value,
            "require_complete_cost_reporting",
            defaults.require_complete_cost_reporting,
        ),
    )


def _parse_run_config(value: Mapping[str, object]) -> EvaluationRunConfig:
    defaults = EvaluationRunConfig()
    return EvaluationRunConfig(
        max_cases=_optional_int(value, "max_cases", defaults.max_cases),
        max_case_concurrency=_optional_int(
            value,
            "max_case_concurrency",
            defaults.max_case_concurrency,
        ),
        max_total_provider_calls=_optional_int(
            value,
            "max_total_provider_calls",
            defaults.max_total_provider_calls,
        ),
    )


def _replay_completion(
    responses: Mapping[str, ProviderResponse | str],
) -> CompletionCallable:
    async def complete(prompt: str, max_tokens: int) -> ProviderResponse | str:
        del max_tokens
        try:
            return responses[prompt]
        except KeyError as error:
            raise LookupError("replay response unavailable") from error

    return complete


def _case_result(
    case: EvaluationCase,
    result: ConsensusResult,
    scores: tuple[EvaluationScore, ...],
    passed: bool,
) -> EvaluationCaseResult:
    statuses = tuple(outcome.status for outcome in result.outcomes)
    return EvaluationCaseResult(
        case_id=case.case_id,
        task=case.task,
        tags=case.tags,
        passed=passed,
        scores=scores,
        duration_ms=result.duration_ms,
        agreement_level=result.agreement_level.value,
        agreement_ratio=result.agreement_ratio,
        providers_queried=result.providers_queried,
        providers_succeeded=result.providers_succeeded,
        provider_errors=statuses.count(ProviderStatus.ERROR),
        provider_timeouts=statuses.count(ProviderStatus.TIMEOUT),
        invalid_responses=statuses.count(ProviderStatus.INVALID_RESPONSE),
        reported_cost_usd=result.reported_cost_usd,
        cost_reporting_complete=result.cost_reporting_complete,
        consensus_sha256=(
            hashlib.sha256(result.consensus_text.encode("utf-8")).hexdigest()
            if result.consensus_text is not None
            else None
        ),
    )


def _summarize(case_results: tuple[EvaluationCaseResult, ...]) -> EvaluationSummary:
    cases_passed = sum(result.passed for result in case_results)
    providers_queried = sum(result.providers_queried for result in case_results)
    providers_succeeded = sum(result.providers_succeeded for result in case_results)
    durations = sorted(result.duration_ms for result in case_results)
    p95_index = max(0, math.ceil(len(durations) * 0.95) - 1)
    return EvaluationSummary(
        cases_total=len(case_results),
        cases_passed=cases_passed,
        case_pass_rate=cases_passed / len(case_results),
        mean_agreement_ratio=(
            sum(result.agreement_ratio for result in case_results) / len(case_results)
        ),
        provider_failure_rate=(
            (providers_queried - providers_succeeded) / providers_queried
            if providers_queried
            else 0.0
        ),
        p95_duration_ms=durations[p95_index],
        total_reported_cost_usd=sum(
            (result.reported_cost_usd for result in case_results),
            start=Decimal("0"),
        ),
        cost_reporting_complete=all(result.cost_reporting_complete for result in case_results),
    )


def _summaries_match(actual: EvaluationSummary, expected: EvaluationSummary) -> bool:
    return (
        actual.cases_total == expected.cases_total
        and actual.cases_passed == expected.cases_passed
        and math.isclose(actual.case_pass_rate, expected.case_pass_rate, abs_tol=0.0001)
        and math.isclose(
            actual.mean_agreement_ratio,
            expected.mean_agreement_ratio,
            abs_tol=0.0001,
        )
        and math.isclose(
            actual.provider_failure_rate,
            expected.provider_failure_rate,
            abs_tol=0.0001,
        )
        and actual.p95_duration_ms == expected.p95_duration_ms
        and actual.total_reported_cost_usd == expected.total_reported_cost_usd
        and actual.cost_reporting_complete == expected.cost_reporting_complete
    )


def _append_minimum_violation(
    violations: list[GateViolation],
    metric: str,
    actual: float,
    threshold: float,
) -> None:
    if actual < threshold:
        violations.append(
            GateViolation(
                metric=metric,
                actual=_format_ratio(actual),
                operator=">=",
                threshold=_format_ratio(threshold),
                reason=f"{metric}_below_minimum",
            )
        )


def _append_maximum_violation(
    violations: list[GateViolation],
    metric: str,
    actual: float,
    threshold: float,
) -> None:
    if actual > threshold:
        violations.append(
            GateViolation(
                metric=metric,
                actual=_format_ratio(actual),
                operator="<=",
                threshold=_format_ratio(threshold),
                reason=f"{metric}_above_maximum",
            )
        )


def _comparison_violation(
    metric: str,
    actual: float,
    threshold: str,
    reason: str,
) -> GateViolation:
    return GateViolation(
        metric=metric,
        actual=_format_ratio(actual),
        operator="budget",
        threshold=threshold,
        reason=reason,
    )


def _normalize_text(value: str, *, case_sensitive: bool, normalize_whitespace: bool) -> str:
    normalized = " ".join(value.split()) if normalize_whitespace else value
    return normalized if case_sensitive else normalized.casefold()


def _format_ratio(value: float) -> str:
    return f"{value:.4f}"


def _elapsed_ms(started: float) -> int:
    return max(0, round((time.perf_counter() - started) * 1_000))


def _require_name(name: str, value: object) -> None:
    _require_short_text(name, value, maximum=200)
    if isinstance(value, str) and any(character.isspace() for character in value):
        raise ValueError(f"{name} must not contain whitespace")


def _require_short_text(name: str, value: object, *, maximum: int) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be a string")
    if not value.strip():
        raise ValueError(f"{name} must not be empty")
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds the {maximum}-character limit")


def _require_int(name: str, value: object, *, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")


def _require_ratio(name: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number")
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError(f"{name} must be between 0 and 1")


def _mapping(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise TypeError(f"{name} must be an object with string keys")
    return value


def _required_string(value: Mapping[str, object], key: str) -> str:
    selected = value.get(key)
    if not isinstance(selected, str):
        raise TypeError(f"{key} must be a string")
    return selected


def _optional_string(value: Mapping[str, object], key: str) -> str | None:
    selected = value.get(key)
    if selected is None:
        return None
    if not isinstance(selected, str):
        raise TypeError(f"{key} must be a string or null")
    return selected


def _required_int(value: Mapping[str, object], key: str) -> int:
    selected = value.get(key)
    if isinstance(selected, bool) or not isinstance(selected, int):
        raise TypeError(f"{key} must be an integer")
    return selected


def _optional_int(value: Mapping[str, object], key: str, default: int) -> int:
    return default if key not in value else _required_int(value, key)


def _required_float(value: Mapping[str, object], key: str) -> float:
    selected = value.get(key)
    if isinstance(selected, bool) or not isinstance(selected, int | float):
        raise TypeError(f"{key} must be a number")
    if not math.isfinite(selected):
        raise ValueError(f"{key} must be finite")
    return float(selected)


def _optional_float(value: Mapping[str, object], key: str, default: float) -> float:
    return default if key not in value else _required_float(value, key)


def _required_bool(value: Mapping[str, object], key: str) -> bool:
    selected = value.get(key)
    if not isinstance(selected, bool):
        raise TypeError(f"{key} must be a boolean")
    return selected


def _optional_bool(value: Mapping[str, object], key: str, default: bool) -> bool:
    return default if key not in value else _required_bool(value, key)


def _required_decimal(value: Mapping[str, object], key: str) -> Decimal:
    selected = value.get(key)
    if isinstance(selected, bool) or not isinstance(selected, str | int | float):
        raise TypeError(f"{key} must be a decimal string or number")
    try:
        decimal = Decimal(str(selected))
    except InvalidOperation as error:
        raise ValueError(f"{key} must be a valid decimal") from error
    if not decimal.is_finite():
        raise ValueError(f"{key} must be finite")
    return decimal


def _required_list(value: Mapping[str, object], key: str) -> list[object]:
    selected = value.get(key)
    if not isinstance(selected, list):
        raise TypeError(f"{key} must be an array")
    return selected


def _optional_list(value: Mapping[str, object], key: str) -> list[object]:
    return [] if key not in value else _required_list(value, key)


def _list_strings(value: list[object], name: str) -> list[str]:
    if not all(isinstance(item, str) for item in value):
        raise TypeError(f"{name} must contain only strings")
    return [item for item in value if isinstance(item, str)]


def _optional_mapping(value: Mapping[str, object], key: str) -> Mapping[str, object]:
    return {} if key not in value else _mapping(value.get(key), key)


__all__ = [
    "ComparisonPolicy",
    "ConsensusReachedScorer",
    "ContainsScorer",
    "EvaluationCase",
    "EvaluationCaseResult",
    "EvaluationPolicy",
    "EvaluationReport",
    "EvaluationRunConfig",
    "EvaluationRunner",
    "EvaluationScore",
    "EvaluationSummary",
    "ExactMatchScorer",
    "ExcludesScorer",
    "GateViolation",
    "JsonObjectScorer",
    "ReplaySuite",
    "ReportComparison",
    "ScoreStatus",
    "Scorer",
    "compare_reports",
    "load_evaluation_report",
    "load_replay_suite",
]
