"""Bounded, auditable consensus across application-supplied AI providers."""

from .consensus import (
    AgreementLevel,
    CallableProvider,
    CompletionCallable,
    ConsensusConfig,
    ConsensusEngine,
    ConsensusResult,
    Provider,
    ProviderOutcome,
    ProviderResponse,
    ProviderStatus,
)
from .usage import JsonlUsageStore, UsageRecord, UsageStatistics, UsageStore

__version__ = "0.2.0"

__all__ = [
    "AgreementLevel",
    "CallableProvider",
    "CompletionCallable",
    "ConsensusConfig",
    "ConsensusEngine",
    "ConsensusResult",
    "JsonlUsageStore",
    "Provider",
    "ProviderOutcome",
    "ProviderResponse",
    "ProviderStatus",
    "UsageRecord",
    "UsageStatistics",
    "UsageStore",
    "__version__",
]
