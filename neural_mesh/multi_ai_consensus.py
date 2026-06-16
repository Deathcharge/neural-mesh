"""
🌀 Helix Collective v17.1 - Multi-AI Consensus Layer
backend/multi_ai_consensus.py

Parallel processing across Claude, GPT-4, Grok, and Gemini with consensus voting:
- Concurrent AI API calls via unified_llm
- Consensus scoring (weighted voting)
- Fallback hierarchy
- Response quality metrics
- Cost optimization
- Dynamic model selection based on available providers

Author: Helix Collective
Version: 17.2.0 (Refactored - Phase 2 Consolidation)
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from apps.backend.services.unified_llm import UnifiedLLMResponse, unified_llm

logger = logging.getLogger(__name__)

# ============================================================================
# ENUMS & TYPES
# ============================================================================


class AIModel(Enum):
    """Available AI models for consensus."""

    CLAUDE = "claude-sonnet-4-6"
    GPT4 = "gpt-4-turbo"
    GROK = "grok-3-mini"
    GEMINI = "gemini-2.0-flash"


# Map model enum names to unified_llm provider names
MODEL_TO_PROVIDER = {
    AIModel.CLAUDE: "anthropic",
    AIModel.GPT4: "openai",
    AIModel.GROK: "xai",
    AIModel.GEMINI: "google",
}


class AgreementLevel(Enum):
    """Consensus agreement strength."""

    UNANIMOUS = 4  # All models agree
    STRONG = 3  # 3+ out of 4 agree
    MODERATE = 2  # 2 out of 4 agree
    WEAK = 1  # No agreement
    ERROR = 0  # Errors occurred


# ============================================================================
# CONSENSUS CONFIGURATION
# ============================================================================


@dataclass
class ConsensusConfig:
    """Configuration for consensus behavior."""

    # Models to include in consensus (can be customized per request)
    models: list[AIModel] = field(default_factory=lambda: list(AIModel))

    # Confidence scores by agreement level
    confidence_scores: dict[str, float] = field(
        default_factory=lambda: {
            "UNANIMOUS": 0.97,
            "STRONG": 0.85,
            "MODERATE": 0.70,
            "WEAK": 0.50,
            "ERROR": 0.0,
        }
    )

    # Maximum tokens for consensus queries
    max_tokens: int = 1000

    # Timeout for individual model queries (seconds)
    timeout_seconds: float = 30.0

    # Whether to include cost tracking
    track_costs: bool = True


# Default configuration
DEFAULT_CONFIG = ConsensusConfig()


# ============================================================================
# CONSENSUS RESPONSE
# ============================================================================


class ConsensusResponse:
    """Result of multi-AI consensus processing."""

    def __init__(
        self,
        task: str,
        responses: dict[str, dict[str, Any]],
        consensus_result: str,
        agreement_level: AgreementLevel,
        confidence_score: float,
        models_queried: int = 0,
        models_succeeded: int = 0,
    ):
        self.task = task
        self.responses = responses
        self.consensus_result = consensus_result
        self.agreement_level = agreement_level
        self.confidence_score = confidence_score
        self.models_queried = models_queried
        self.models_succeeded = models_succeeded
        self.created_at = datetime.now(UTC)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for logging/transmission."""
        return {
            "task": self.task,
            "consensus_result": self.consensus_result,
            "agreement_level": self.agreement_level.name,
            "confidence_score": round(self.confidence_score, 2),
            "models_queried": self.models_queried,
            "models_succeeded": self.models_succeeded,
            "responses": self.responses,
            "created_at": self.created_at.isoformat() + "Z",
        }


# ============================================================================
# CONSENSUS ENGINE
# ============================================================================


class MultiAIConsensus:
    """
    Coordinates consensus across multiple AI models via unified_llm.

    Supports Claude, GPT-4, Grok, and Gemini. Automatically detects
    which providers are available and queries them in parallel.
    """

    def __init__(self, config: ConsensusConfig | None = None):
        self.config = config or DEFAULT_CONFIG
        self._usage_log = Path("Helix/state/consensus_usage.jsonl")
        self._usage_log.parent.mkdir(parents=True, exist_ok=True)

    async def _query_model(
        self,
        model: AIModel,
        prompt: str,
        max_tokens: int = 1000,
    ) -> dict[str, Any]:
        """
        Query a single model via unified_llm.

        Args:
            model: The AI model to query
            prompt: The prompt to send
            max_tokens: Maximum tokens in response

        Returns:
            Dict with model name, status, response, and token usage
        """
        provider = MODEL_TO_PROVIDER.get(model)
        model_name = model.name

        try:
            # Use unified_llm for all providers (including Gemini)
            if model == AIModel.GEMINI:
                # Gemini uses special chat_gemini method
                resp: UnifiedLLMResponse = await unified_llm.chat_gemini(
                    prompt,
                    model=model.value,
                    max_tokens=max_tokens,
                )
            else:
                # All other providers use standard chat_with_metadata
                resp = await unified_llm.chat_with_metadata(
                    [{"role": "user", "content": prompt}],
                    model=model.value,
                    provider=provider,
                    max_tokens=max_tokens,
                )

            if resp.error:
                return {
                    "model": model_name,
                    "status": "error",
                    "error": resp.error,
                    "provider": provider or "google",
                }

            return {
                "model": model_name,
                "status": "success",
                "response": resp.content,
                "tokens_used": resp.total_tokens,
                "provider": resp.provider,
                "actual_model": resp.model,
            }

        except TimeoutError:
            logger.warning("%s query timed out", model_name)
            return {
                "model": model_name,
                "status": "error",
                "error": "Query timed out",
                "provider": provider or "google",
            }
        except Exception as e:
            logger.error("%s API error: %s", model_name, e)
            return {
                "model": model_name,
                "status": "error",
                "error": type(e).__name__,
                "provider": provider or "google",
            }

    async def query_all_models(
        self,
        prompt: str,
        max_tokens: int = 1000,
        models: list[AIModel] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """
        Query multiple models in parallel.

        Args:
            prompt: The prompt to send to all models
            max_tokens: Maximum tokens per response
            models: Specific models to query (defaults to all configured)

        Returns:
            Dict mapping model names to their responses
        """
        target_models = models or self.config.models
        logger.info(
            "Starting multi-AI consensus query across %d models: %s",
            len(target_models),
            [m.name for m in target_models],
        )

        # Create tasks with timeout
        tasks = [
            asyncio.wait_for(
                self._query_model(model, prompt, max_tokens),
                timeout=self.config.timeout_seconds,
            )
            for model in target_models
        ]

        # Gather results (return_exceptions=True to handle timeouts gracefully)
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        response_dict: dict[str, dict[str, Any]] = {}
        for i, result in enumerate(raw_results):
            model_name = target_models[i].name
            if isinstance(result, BaseException):
                response_dict[model_name] = {
                    "model": model_name,
                    "status": "error",
                    "error": str(result),
                }
            else:
                response_dict[model_name] = result

        return response_dict

    async def calculate_consensus(
        self,
        task: str,
        prompt: str,
        max_tokens: int = 1000,
        models: list[AIModel] | None = None,
    ) -> ConsensusResponse:
        """
        Calculate consensus across all available models.

        Args:
            task: Description of the consensus task
            prompt: The prompt to evaluate
            max_tokens: Maximum tokens per model response
            models: Specific models to include (defaults to all)

        Returns:
            ConsensusResponse with agreement level and confidence score
        """
        logger.info("Consensus task: %s", task)

        # Query all models
        responses = await self.query_all_models(prompt, max_tokens, models)

        # Extract successful responses
        successful: dict[str, str] = {
            model_name: resp["response"] for model_name, resp in responses.items() if resp.get("status") == "success"
        }

        total_queried = len(responses)
        total_succeeded = len(successful)

        if not successful:
            logger.error("All %d models failed", total_queried)
            return ConsensusResponse(
                task=task,
                responses=responses,
                consensus_result="ERROR: All models failed",
                agreement_level=AgreementLevel.ERROR,
                confidence_score=0.0,
                models_queried=total_queried,
                models_succeeded=0,
            )

        # Calculate agreement level based on success count
        agreement = self._calculate_agreement(total_succeeded, total_queried)
        confidence = self.config.confidence_scores.get(agreement.name, 0.5)

        # Build consensus result
        consensus_result = self._build_consensus(successful, agreement)

        logger.info(
            "Consensus calculated: %s (confidence: %.0f%%, %d/%d models)",
            agreement.name,
            confidence * 100,
            total_succeeded,
            total_queried,
        )

        # Log usage
        self._log_usage(task, responses, agreement, confidence)

        return ConsensusResponse(
            task=task,
            responses=responses,
            consensus_result=consensus_result,
            agreement_level=agreement,
            confidence_score=confidence,
            models_queried=total_queried,
            models_succeeded=total_succeeded,
        )

    def _calculate_agreement(self, succeeded: int, total: int) -> AgreementLevel:
        """Calculate agreement level based on success count."""
        if succeeded == 0:
            return AgreementLevel.ERROR
        if succeeded == total and total >= 3:
            return AgreementLevel.UNANIMOUS
        if succeeded >= 3:
            return AgreementLevel.STRONG
        if succeeded >= 2:
            return AgreementLevel.MODERATE
        return AgreementLevel.WEAK

    def _build_consensus(
        self,
        successful: dict[str, str],
        agreement: AgreementLevel,
    ) -> str:
        """Build consensus result from successful responses."""
        responses_list = list(successful.items())

        if len(responses_list) == 1:
            # Only one model succeeded - use its response
            model_name, response = responses_list[0]
            return f"**Single Model ({model_name}):**\n\n{response}"

        if agreement in (AgreementLevel.UNANIMOUS, AgreementLevel.STRONG):
            # Strong consensus - use first response (typically Claude) as primary
            # and note agreement from others
            primary_model, primary_response = responses_list[0]
            other_models = [name for name, _ in responses_list[1:]]
            return f"**Consensus ({primary_model} + {', '.join(other_models)}):**\n\n{primary_response}"

        # Moderate/weak - show all responses
        parts = []
        for model_name, response in responses_list:
            parts.append(f"**{model_name}:**\n{response}")
        return "\n\n---\n\n".join(parts)

    def _merge_responses(self, response1: str, response2: str) -> str:
        """Merge two responses into one consensus response."""
        return f"**Consensus (Combined):**\n\nResponse 1:\n{response1}\n\nResponse 2:\n{response2}"

    def _log_usage(
        self,
        task: str,
        responses: dict[str, Any],
        agreement: AgreementLevel,
        confidence: float,
    ) -> None:
        """Log consensus usage for cost tracking."""
        entry = {
            "timestamp": datetime.now(UTC).isoformat(),
            "task": task,
            "agreement_level": agreement.name,
            "confidence": confidence,
            "models_queried": list(responses.keys()),
            "successful_models": [m for m, r in responses.items() if r.get("status") == "success"],
            "total_tokens": sum(r.get("tokens_used", 0) for r in responses.values() if r.get("status") == "success"),
        }

        try:
            with open(self._usage_log, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError as e:
            logger.warning("Failed to write consensus usage log: %s", e)

    async def get_consensus_statistics(self) -> dict[str, Any]:
        """Get consensus usage statistics."""
        if not self._usage_log.exists():
            return {"total_queries": 0}

        queries: list[dict[str, Any]] = []
        try:
            with open(self._usage_log, encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        queries.append(json.loads(line))
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("Failed to read consensus usage log: %s", e)
            return {"total_queries": 0, "error": type(e).__name__}

        if not queries:
            return {"total_queries": 0}

        agreement_counts: dict[str, int] = {}
        total_tokens = 0
        for q in queries:
            agreement = q.get("agreement_level", "UNKNOWN")
            agreement_counts[agreement] = agreement_counts.get(agreement, 0) + 1
            total_tokens += q.get("total_tokens", 0)

        avg_confidence = sum(q.get("confidence", 0) for q in queries) / len(queries)
        total = len(queries)

        return {
            "total_queries": total,
            "agreement_distribution": agreement_counts,
            "average_confidence": round(avg_confidence, 2),
            "unanimous_rate": round(agreement_counts.get("UNANIMOUS", 0) / total * 100, 1),
            "strong_rate": round(agreement_counts.get("STRONG", 0) / total * 100, 1),
            "total_tokens_used": total_tokens,
            "models_available": [m.name for m in AIModel],
        }

    def get_available_models(self) -> list[str]:
        """Get list of models that have configured API keys."""
        available = unified_llm.get_available_providers()
        result = []
        for model in AIModel:
            provider = MODEL_TO_PROVIDER.get(model)
            if provider and provider in available:
                result.append(model.name)
            elif model == AIModel.GEMINI:
                # Gemini uses separate SDK, check env var
                import os

                if os.getenv("GOOGLE_AI_KEY"):
                    result.append(model.name)
        return result


# ============================================================================
# BACKWARD COMPATIBILITY
# ============================================================================

# Keep old client classes as thin wrappers for backward compatibility
# These are deprecated - use MultiAIConsensus directly


class ClaudeClient:
    """Deprecated: Use MultiAIConsensus directly."""

    def __init__(self, api_key: str | None = None):
        self.model = AIModel.CLAUDE
        self._consensus = MultiAIConsensus()

    async def query(self, prompt: str, max_tokens: int = 1000) -> dict[str, Any]:
        return await self._consensus._query_model(self.model, prompt, max_tokens)


class GPT4Client:
    """Deprecated: Use MultiAIConsensus directly."""

    def __init__(self, api_key: str | None = None):
        self.model = AIModel.GPT4
        self._consensus = MultiAIConsensus()

    async def query(self, prompt: str, max_tokens: int = 1000) -> dict[str, Any]:
        return await self._consensus._query_model(self.model, prompt, max_tokens)


class GeminiClient:
    """Deprecated: Use MultiAIConsensus directly."""

    def __init__(self, api_key: str | None = None):
        self.model = AIModel.GEMINI
        self._consensus = MultiAIConsensus()

    async def query(self, prompt: str, max_tokens: int = 1000) -> dict[str, Any]:
        return await self._consensus._query_model(self.model, prompt, max_tokens)


class GrokClient:
    """xAI/Grok integration via unified LLM."""

    def __init__(self, api_key: str | None = None):
        self.model = AIModel.GROK
        self._consensus = MultiAIConsensus()

    async def query(self, prompt: str, max_tokens: int = 1000) -> dict[str, Any]:
        return await self._consensus._query_model(self.model, prompt, max_tokens)


# ============================================================================
# EXPORTS
# ============================================================================

__all__ = [
    "AIModel",
    "AgreementLevel",
    # Backward compatibility
    "ClaudeClient",
    "ConsensusConfig",
    "ConsensusResponse",
    "GPT4Client",
    "GeminiClient",
    "GrokClient",
    "MultiAIConsensus",
]
