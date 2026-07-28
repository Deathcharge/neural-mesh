"""Compatibility import for the original extraction module path.

The 0.1 extraction was not independently installable and depended on private
legacy application code. New integrations should import from :mod:`neural_mesh`.
"""

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

MultiAIConsensus = ConsensusEngine
ConsensusResponse = ConsensusResult

__all__ = [
    "AgreementLevel",
    "CallableProvider",
    "CompletionCallable",
    "ConsensusConfig",
    "ConsensusEngine",
    "ConsensusResponse",
    "ConsensusResult",
    "MultiAIConsensus",
    "Provider",
    "ProviderOutcome",
    "ProviderResponse",
    "ProviderStatus",
]
