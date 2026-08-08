"""Privacy-minimized events for application-owned observability backends."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, TypeAlias, runtime_checkable

if TYPE_CHECKING:
    from .consensus import ConsensusResult

AttributeValue: TypeAlias = str | bool | int | float


@dataclass(frozen=True, slots=True)
class ConsensusEvent:
    """One completed council operation without prompt or response content."""

    SCHEMA_VERSION = "neural-mesh-consensus-event/v1"
    EVENT_NAME = "neural_mesh.consensus.completed"

    occurred_at: datetime
    attributes: dict[str, AttributeValue]

    @classmethod
    def from_result(cls, result: ConsensusResult) -> ConsensusEvent:
        """Create the default OpenTelemetry-compatible event representation."""

        statuses = [outcome.status.value for outcome in result.outcomes]
        attributes: dict[str, AttributeValue] = {
            "gen_ai.operation.name": "invoke_workflow",
            "gen_ai.workflow.name": "neural-mesh.consensus",
            "gen_ai.request.max_tokens": result.requested_max_tokens,
            "neural_mesh.schema_version": cls.SCHEMA_VERSION,
            "neural_mesh.task.sha256": hashlib.sha256(result.task.encode("utf-8")).hexdigest(),
            "neural_mesh.duration_ms": result.duration_ms,
            "neural_mesh.providers.queried": result.providers_queried,
            "neural_mesh.providers.succeeded": result.providers_succeeded,
            "neural_mesh.providers.errors": statuses.count("error"),
            "neural_mesh.providers.timeouts": statuses.count("timeout"),
            "neural_mesh.providers.invalid_responses": statuses.count("invalid_response"),
            "neural_mesh.agreement.level": result.agreement_level.value,
            "neural_mesh.agreement.ratio": round(result.agreement_ratio, 4),
            "neural_mesh.consensus.reached": result.consensus_text is not None,
            "neural_mesh.usage.token_reporting_complete": result.token_reporting_complete,
            "neural_mesh.usage.cost_reporting_complete": result.cost_reporting_complete,
            "neural_mesh.usage.reported_cost_usd": _decimal_text(result.reported_cost_usd),
            "neural_mesh.usage.recorded": result.usage_recorded,
        }
        if result.token_reporting_complete:
            attributes["gen_ai.usage.input_tokens"] = result.reported_input_tokens
            attributes["gen_ai.usage.output_tokens"] = result.reported_output_tokens
        if result.usage_error_code is not None:
            attributes["error.type"] = f"neural_mesh.usage.{result.usage_error_code}"
        return cls(occurred_at=result.created_at, attributes=attributes)

    def to_dict(self) -> dict[str, object]:
        """Return a backend-neutral event record."""

        return {
            "schema_version": self.SCHEMA_VERSION,
            "event_name": self.EVENT_NAME,
            "occurred_at": self.occurred_at.isoformat(),
            "attributes": dict(self.attributes),
        }


@runtime_checkable
class ConsensusObserver(Protocol):
    """Application-owned asynchronous sink for completed consensus events."""

    async def record(self, event: ConsensusEvent) -> None:
        """Record one event or raise to report a sink failure."""


def _decimal_text(value: Decimal) -> str:
    return format(value, "f")


__all__ = ["AttributeValue", "ConsensusEvent", "ConsensusObserver"]
