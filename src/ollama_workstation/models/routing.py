"""Hardware-aware, evidence-aware model routing for local Ollama artifacts.

Routing is deliberately separate from profiling and execution. It can use
declared metadata when empirical evidence is absent, but marks that distinction
in every decision. A caller that requires verified capability must opt in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ollama_workstation.models.model_intelligence import ModelIntelligence
from ollama_workstation.models.schemas import CapabilityResult, ModelProfile, ModelRef


class NoEligibleModel(RuntimeError):
    """Raised when no installed model satisfies a routing request."""

    def __init__(self, message: str, candidates: list[ModelRoute]) -> None:
        super().__init__(message)
        self.candidates = candidates


@dataclass(frozen=True, slots=True)
class RoutingRequest:
    required_capabilities: tuple[str, ...] = ()
    preferred_capabilities: tuple[str, ...] = ()
    context_tokens: int = 0
    minimum_quality: float = 0.0
    require_verified: bool = False
    preferred_models: tuple[str, ...] = ()
    excluded_models: tuple[str, ...] = ()
    max_memory_bytes: int | None = None
    speed_priority: float = 0.0
    require_completion: bool = True


@dataclass(frozen=True, slots=True)
class ModelRoute:
    model_name: str
    digest: str
    eligible: bool
    score: float
    reason: str
    missing_capabilities: tuple[str, ...] = ()
    declared_capabilities: tuple[str, ...] = ()
    verified_capabilities: tuple[str, ...] = ()
    context_length: int | None = None
    estimated_memory_bytes: int | None = None
    available_memory_bytes: int | None = None
    quality: float | None = None
    speed_tokens_per_second: float | None = None
    intelligence: ModelIntelligence | None = None
    profile: ModelProfile | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name,
            "digest": self.digest,
            "eligible": self.eligible,
            "score": round(self.score, 2),
            "reason": self.reason,
            "missing_capabilities": list(self.missing_capabilities),
            "declared_capabilities": list(self.declared_capabilities),
            "verified_capabilities": list(self.verified_capabilities),
            "context_length": self.context_length,
            "estimated_memory_bytes": self.estimated_memory_bytes,
            "available_memory_bytes": self.available_memory_bytes,
            "quality": self.quality,
            "speed_tokens_per_second": self.speed_tokens_per_second,
            "details": self.details,
        }


@dataclass(frozen=True, slots=True)
class RoutingDecision:
    request: RoutingRequest
    selected: ModelRoute | None
    candidates: tuple[ModelRoute, ...]

    @property
    def fallback_chain(self) -> tuple[ModelRoute, ...]:
        return tuple(item for item in self.candidates if item.eligible)

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected": self.selected.to_dict() if self.selected else None,
            "fallback_chain": [item.model_name for item in self.fallback_chain],
            "candidates": [item.to_dict() for item in self.candidates],
        }


_CAPABILITY_ALIASES = {
    "tools": "tool_calling",
    "tool": "tool_calling",
    "function_calling": "tool_calling",
    "json": "structured_output",
    "embedding": "embedding",
    "embeddings": "embedding",
    "vision": "vision",
    "image": "vision",
    "thinking": "reasoning",
}
_DECLARED_TO_CAPABILITY = {
    "tools": "tool_calling",
    "completion": "instruction_following",
    "vision": "vision",
    "embedding": "embedding",
}

_TASK_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (("code", "python", "typescript", "javascript", "refactor", "bug", "test",
      "function", "repository", "compile", "debug"), ("code_generation", "code_comprehension")),
    (("json", "schema", "structured", "extract", "fields"), ("structured_output",)),
    (("tool", "api", "function call", "mcp", "search the web"), ("tool_calling",)),
    (("image", "vision", "screenshot", "photo", "diagram"), ("vision",)),
    (("plan", "workflow", "dependency", "steps", "orchestrate"), ("planning",)),
    (("reason", "prove", "logic", "compare", "analyze", "analyse"), ("reasoning",)),
    (("summarize", "summary", "condense", "abstract"), ("summarization",)),
    (("write", "draft", "rewrite", "copy", "story", "blog"), ("creative_writing",)),
    (("context", "document", "long", "transcript", "whole file"), ("long_context",)),
)


def infer_task_capabilities(text: str) -> tuple[str, ...]:
    """Infer soft capability preferences from a task without hard-filtering models.

    These are routing hints, not benchmark claims. They raise the ranking of
    models with observed or declared evidence but never make an otherwise
    eligible local model impossible to select.
    """
    normalized = " ".join(str(text or "").casefold().split())
    matches: list[str] = []
    for terms, capabilities in _TASK_HINTS:
        if any(term in normalized for term in terms):
            matches.extend(capabilities)
    return tuple(dict.fromkeys(matches))


def _available_memory(host: dict[str, Any] | None) -> int | None:
    """Return the largest single usable memory pool, not a false GPU+RAM sum."""
    if not isinstance(host, dict):
        return None
    candidates: list[int] = []
    gpus = host.get("nvidia_gpus")
    if isinstance(gpus, list):
        for gpu in gpus:
            if not isinstance(gpu, dict):
                continue
            free_mib = gpu.get("memory_free_mib")
            if isinstance(free_mib, (int, float)) and not isinstance(free_mib, bool):
                candidates.append(int(free_mib) * 1024 * 1024)
    memory = host.get("system_memory")
    if isinstance(memory, dict):
        available = memory.get("available_bytes")
        if isinstance(available, (int, float)) and not isinstance(available, bool):
            candidates.append(int(available))
    return max(candidates) if candidates else None


def _capability_result(profile: ModelProfile | None, capability: str) -> CapabilityResult | None:
    if profile is None:
        return None
    return next((item for item in profile.capabilities if item.capability == capability), None)


def _effective_score(result: CapabilityResult | None) -> float | None:
    if result is None or result.score is None or result.status not in {"passed", "partial"}:
        return None
    return max(0.0, min(100.0, result.score)) * (0.65 + 0.35 * min(1.0, max(0.0, result.confidence)))


def _verified_capabilities(profile: ModelProfile | None) -> set[str]:
    if profile is None:
        return set()
    return {
        item.capability for item in profile.capabilities
        if item.status in {"passed", "partial"} and item.score is not None
    }


def _declared_capability_names(intelligence: ModelIntelligence | None) -> set[str]:
    if intelligence is None:
        return set()
    names = {
        _DECLARED_TO_CAPABILITY[item]
        for item in intelligence.declared_capabilities
        if item in _DECLARED_TO_CAPABILITY
    }
    if intelligence.supports_tools_declared:
        names.add("tool_calling")
    if intelligence.supports_vision_declared:
        names.add("vision")
    if intelligence.supports_embedding_declared:
        names.add("embedding")
    return names


def _profile_speed(profile: ModelProfile | None) -> float | None:
    if profile is None:
        return None
    value = profile.operational.get("performance_summary", {}) if isinstance(profile.operational, dict) else {}
    if isinstance(value, dict):
        speed = value.get("mean_generation_tokens_per_second")
        if isinstance(speed, (int, float)) and not isinstance(speed, bool) and speed > 0:
            return float(speed)
    benchmark = profile.operational.get("latest_benchmark", {}) if isinstance(profile.operational, dict) else {}
    if isinstance(benchmark, dict):
        speed = benchmark.get("mean_generation_tokens_per_second")
        if isinstance(speed, (int, float)) and not isinstance(speed, bool) and speed > 0:
            return float(speed)
    return None


def _profile_benchmark_quality(profile: ModelProfile | None) -> float | None:
    """Use a deterministic benchmark score only as a fallback quality signal."""
    if profile is None or not isinstance(profile.operational, dict):
        return None
    benchmark = profile.operational.get("latest_benchmark")
    if not isinstance(benchmark, dict):
        return None
    score = benchmark.get("score")
    if isinstance(score, (int, float)) and not isinstance(score, bool):
        return max(0.0, min(100.0, float(score)))
    return None


class ModelRouter:
    """Ranks installed models and explains every eligibility decision."""

    def rank(
        self,
        models: list[ModelRef],
        *,
        request: RoutingRequest,
        profiles: list[ModelProfile] | None = None,
        intelligence: dict[str, ModelIntelligence] | None = None,
        host: dict[str, Any] | None = None,
    ) -> RoutingDecision:
        profile_map = {item.model_name: item for item in (profiles or [])}
        available = _available_memory(host)
        normalized_required = tuple(
            dict.fromkeys(_CAPABILITY_ALIASES.get(item, item) for item in request.required_capabilities)
        )
        normalized_preferred = tuple(
            dict.fromkeys(
                _CAPABILITY_ALIASES.get(item, item)
                for item in request.preferred_capabilities
            )
        )
        candidates: list[ModelRoute] = []
        for model in models:
            profile = profile_map.get(model.name)
            intel = (intelligence or {}).get(model.name)
            candidates.append(
                self._evaluate(
                    model, profile, intel, request, normalized_required,
                    normalized_preferred, available,
                )
            )
        candidates.sort(key=lambda item: (item.eligible, item.score), reverse=True)
        selected = candidates[0] if candidates and candidates[0].eligible else None
        return RoutingDecision(request, selected, tuple(candidates))

    def select(self, *args: Any, **kwargs: Any) -> ModelRoute:
        decision = self.rank(*args, **kwargs)
        if decision.selected is None:
            reasons = "; ".join(
                f"{item.model_name}: {item.reason}" for item in decision.candidates[:6]
            ) or "no installed models"
            raise NoEligibleModel(
                f"No installed model satisfies this request ({reasons})", list(decision.candidates)
            )
        return decision.selected

    @staticmethod
    def _evaluate(
        model: ModelRef,
        profile: ModelProfile | None,
        intelligence: ModelIntelligence | None,
        request: RoutingRequest,
        required: tuple[str, ...],
        preferred: tuple[str, ...],
        available: int | None,
    ) -> ModelRoute:
        if model.name in request.excluded_models:
            return ModelRoute(
                model.name, model.digest, False, 0.0, "explicitly excluded",
                intelligence=intelligence, profile=profile,
            )
        declared = _declared_capability_names(intelligence)
        verified = _verified_capabilities(profile)
        if request.require_completion and intelligence is not None and not intelligence.is_completion_declared:
            return ModelRoute(
                model.name, model.digest, False, 0.0,
                "artifact does not declare completion capability",
                declared_capabilities=tuple(sorted(declared)),
                verified_capabilities=tuple(sorted(verified)),
                context_length=intelligence.context_length,
                intelligence=intelligence, profile=profile,
            )
        missing: list[str] = []
        evidence_sources: list[str] = []
        for capability in required:
            observed = _capability_result(profile, capability)
            observed_ok = observed is not None and observed.status in {"passed", "partial"} and observed.score is not None
            if request.require_verified:
                if not observed_ok:
                    missing.append(capability)
                    evidence_sources.append(f"{capability}: no verified observation")
                else:
                    evidence_sources.append(f"{capability}: observed {observed.status}")
                continue
            if observed_ok:
                evidence_sources.append(f"{capability}: observed {observed.status}")
            elif capability in declared:
                evidence_sources.append(f"{capability}: declared by artifact, unverified")
            else:
                missing.append(capability)
                evidence_sources.append(f"{capability}: unknown")
        if missing:
            return ModelRoute(
                model.name, model.digest, False, 0.0,
                "missing or unverified capability: " + ", ".join(missing),
                missing_capabilities=tuple(missing),
                declared_capabilities=tuple(sorted(declared)),
                verified_capabilities=tuple(sorted(verified)),
                context_length=intelligence.context_length if intelligence else None,
                intelligence=intelligence, profile=profile,
            )

        context = intelligence.context_length if intelligence else None
        if request.context_tokens > 0:
            if context is None:
                return ModelRoute(
                    model.name, model.digest, False, 0.0,
                    f"context capacity unknown; {request.context_tokens} tokens requested",
                    declared_capabilities=tuple(sorted(declared)),
                    verified_capabilities=tuple(sorted(verified)),
                    context_length=None, intelligence=intelligence, profile=profile,
                )
            if context < request.context_tokens:
                return ModelRoute(
                    model.name, model.digest, False, 0.0,
                    f"context capacity {context} is below requested {request.context_tokens}",
                    context_length=context, intelligence=intelligence, profile=profile,
                )
        else:
            context = context or (profile.context_length if profile else None)

        estimate = intelligence.resource_estimate if intelligence else None
        estimated_memory = estimate.total_bytes if estimate else None
        if request.max_memory_bytes is not None and estimated_memory is not None:
            if estimated_memory > request.max_memory_bytes:
                return ModelRoute(
                    model.name, model.digest, False, 0.0,
                    f"estimated memory {estimated_memory} exceeds allowed {request.max_memory_bytes}",
                    context_length=context, estimated_memory_bytes=estimated_memory,
                    available_memory_bytes=available, intelligence=intelligence, profile=profile,
                )
        if available is not None and estimated_memory is not None and estimated_memory > available * 0.95:
            return ModelRoute(
                model.name, model.digest, False, 0.0,
                f"estimated memory {estimated_memory / 1e9:.2f} GB exceeds usable free memory "
                f"{available / 1e9:.2f} GB",
                context_length=context, estimated_memory_bytes=estimated_memory,
                available_memory_bytes=available, intelligence=intelligence, profile=profile,
            )

        observed_scores = [
            score for capability in required
            if (score := _effective_score(_capability_result(profile, capability))) is not None
        ]
        if observed_scores:
            quality = sum(observed_scores) / len(observed_scores)
        elif required and all(capability in declared for capability in required):
            quality = 55.0
        else:
            quality = _profile_benchmark_quality(profile) or 60.0
        if profile is not None and profile.overall_reliability is not None:
            quality = 0.7 * quality + 0.3 * (profile.overall_reliability * 100.0)
        if quality < request.minimum_quality:
            return ModelRoute(
                model.name, model.digest, False, 0.0,
                f"quality evidence {quality:.1f} is below minimum {request.minimum_quality:.1f}",
                context_length=context, estimated_memory_bytes=estimated_memory,
                available_memory_bytes=available, quality=quality,
                intelligence=intelligence, profile=profile,
            )

        completion = [
            1.0 for capability in required
            if capability in verified or capability in declared
        ]
        coverage = sum(completion) / len(required) if required else 1.0
        context_fit = 1.0 if not request.context_tokens or context is None else min(
            1.0, request.context_tokens / max(1, context)
        )
        resource_fit = 0.5
        if available and estimated_memory:
            resource_fit = max(0.0, min(1.0, 1.0 - estimated_memory / available))
        speed = _profile_speed(profile)
        speed_fit = min(1.0, speed / 40.0) if speed else 0.5
        speed_weight = max(0.0, min(1.0, request.speed_priority))
        score = 100.0 * (
            0.65 * quality / 100.0
            + 0.15 * coverage
            + 0.10 * context_fit
            + 0.10 * resource_fit
        )
        preferred_observed = [
            capability for capability in preferred
            if _effective_score(_capability_result(profile, capability)) is not None
        ]
        preferred_declared = [
            capability for capability in preferred
            if capability in declared and capability not in preferred_observed
        ]
        if preferred_observed or preferred_declared:
            score += min(8.0, 2.0 * len(preferred_observed) + 1.0 * len(preferred_declared))
        if speed_weight:
            score = score * (1.0 - 0.25 * speed_weight) + 25.0 * speed_weight * speed_fit
        if model.name in request.preferred_models:
            score += 4.0
        score = max(0.0, min(100.0, score))
        reason = "eligible"
        if evidence_sources:
            reason += "; " + "; ".join(evidence_sources)
        if estimated_memory is None:
            reason += "; memory estimate unavailable"
        if speed is None:
            reason += "; latency profile unavailable"
        if preferred_observed:
            reason += "; preferred evidence: " + ", ".join(preferred_observed)
        elif preferred_declared:
            reason += "; preferred declared-only: " + ", ".join(preferred_declared)
        return ModelRoute(
            model.name, model.digest, True, score, reason,
            declared_capabilities=tuple(sorted(declared)),
            verified_capabilities=tuple(sorted(verified)),
            context_length=context,
            estimated_memory_bytes=estimated_memory,
            available_memory_bytes=available,
            quality=quality,
            speed_tokens_per_second=speed,
            intelligence=intelligence,
            profile=profile,
            details={
                "context_fit": context_fit,
                "resource_fit": resource_fit,
                "speed_fit": speed_fit,
                "coverage": coverage,
                "declared_only": not observed_scores,
                "preferred_capabilities": list(preferred),
                "preferred_observed": preferred_observed,
                "preferred_declared": preferred_declared,
            },
        )
