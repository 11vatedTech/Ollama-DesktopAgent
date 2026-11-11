"""Typed model intelligence derived from Ollama's installed artifact metadata.

Ollama exposes a mixture of architecture metadata, publisher labels and declared
capability flags. This module keeps those facts separate from empirical
benchmark evidence: a declared ``tools`` flag means the model advertises tool
support, not that it reliably calls tools. Memory estimates are explicitly
inferred and may differ from the runtime's actual allocation.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

from ollama_workstation.models.schemas import ModelRef, utc_now

# Approximate bits per weight for common GGUF quantizations. These are used only
# for a conservative memory estimate, never as a capability claim.
_QUANTIZATION_BITS: dict[str, float] = {
    "F32": 32.0,
    "F16": 16.0,
    "BF16": 16.0,
    "Q8_0": 8.5,
    "Q6_K": 6.6,
    "Q5_K_M": 5.7,
    "Q5_K_S": 5.7,
    "Q5_0": 5.5,
    "Q4_K_M": 4.8,
    "Q4_K_S": 4.7,
    "Q4_0": 4.5,
    "IQ4_XS": 4.25,
    "IQ4_NL": 4.5,
    "Q3_K_M": 3.9,
    "Q3_K_S": 3.8,
    "Q2_K": 3.0,
}


@dataclass(frozen=True, slots=True)
class ModelResourceEstimate:
    """Best-effort memory estimate with an explicit evidence level."""

    weights_bytes: int | None
    kv_cache_bytes: int | None
    total_bytes: int | None
    context_tokens: int | None
    evidence_level: str = "inferred"
    assumptions: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["assumptions"] = list(self.assumptions)
        return payload


@dataclass(frozen=True, slots=True)
class ModelIntelligence:
    """Normalized, JSON-serializable intelligence for one installed artifact."""

    model_name: str
    digest: str
    observed_at: str
    family: str = "unknown"
    architecture: str = "unknown"
    format: str = "unknown"
    parameter_size: str = "unknown"
    parameter_count: int | None = None
    quantization: str = "unknown"
    quantization_bits: float | None = None
    context_length: int | None = None
    embedding_length: int | None = None
    block_count: int | None = None
    head_count: int | None = None
    head_count_kv: int | None = None
    key_length: int | None = None
    value_length: int | None = None
    declared_capabilities: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    languages: tuple[str, ...] = ()
    license: str = ""
    license_link: str = ""
    finetune: str = ""
    base_models: tuple[str, ...] = ()
    system_prompt: str = ""
    inventory_size_bytes: int = 0
    modified_at: str = ""
    resource_estimate: ModelResourceEstimate | None = None
    evidence: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    limitations: tuple[str, ...] = ()

    @property
    def supports_tools_declared(self) -> bool:
        return "tools" in self.declared_capabilities

    @property
    def supports_vision_declared(self) -> bool:
        return "vision" in self.declared_capabilities

    @property
    def supports_embedding_declared(self) -> bool:
        return "embedding" in self.declared_capabilities

    @property
    def supports_thinking_declared(self) -> bool:
        return "thinking" in self.declared_capabilities

    @property
    def is_completion_declared(self) -> bool:
        return "completion" in self.declared_capabilities or not self.declared_capabilities

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["declared_capabilities"] = list(self.declared_capabilities)
        payload["tags"] = list(self.tags)
        payload["languages"] = list(self.languages)
        payload["base_models"] = list(self.base_models)
        payload["evidence"] = [dict(item) for item in self.evidence]
        payload["limitations"] = list(self.limitations)
        payload["resource_estimate"] = (
            self.resource_estimate.as_dict() if self.resource_estimate else None
        )
        payload["supports_tools_declared"] = self.supports_tools_declared
        payload["supports_vision_declared"] = self.supports_vision_declared
        payload["supports_embedding_declared"] = self.supports_embedding_declared
        payload["supports_thinking_declared"] = self.supports_thinking_declared
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModelIntelligence:
        values = dict(payload)
        for key in ("declared_capabilities", "tags", "languages", "base_models"):
            raw = values.get(key) or ()
            values[key] = tuple(str(item) for item in raw) if isinstance(raw, (list, tuple)) else ()
        values["evidence"] = tuple(
            dict(item) for item in (values.get("evidence") or []) if isinstance(item, dict)
        )
        values["limitations"] = tuple(str(item) for item in (values.get("limitations") or ()))
        resource = values.get("resource_estimate")
        if isinstance(resource, dict):
            assumptions = resource.get("assumptions") or ()
            values["resource_estimate"] = ModelResourceEstimate(
                weights_bytes=_optional_int(resource.get("weights_bytes")),
                kv_cache_bytes=_optional_int(resource.get("kv_cache_bytes")),
                total_bytes=_optional_int(resource.get("total_bytes")),
                context_tokens=_optional_int(resource.get("context_tokens")),
                evidence_level=str(resource.get("evidence_level") or "inferred"),
                assumptions=tuple(str(item) for item in assumptions),
            )
        else:
            values["resource_estimate"] = None
        # Drop derived properties that may have been serialized by older callers.
        for key in (
            "supports_tools_declared", "supports_vision_declared",
            "supports_embedding_declared", "supports_thinking_declared",
        ):
            values.pop(key, None)
        return cls(**values)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first_value(info: dict[str, Any], suffix: str) -> Any:
    for key, value in info.items():
        if key.endswith(suffix):
            return value
    return None


def _copy_strings(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item) for item in value if isinstance(item, (str, int, float)) and str(item))


def _parameter_count(details: dict[str, Any], info: dict[str, Any]) -> int | None:
    count = _optional_int(info.get("general.parameter_count"))
    if count is not None:
        return count
    label = str(details.get("parameter_size") or "")
    match = re.fullmatch(r"\s*([0-9]+(?:\.[0-9]+)?)\s*([BMK])\s*", label, flags=re.I)
    if not match:
        return None
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}[match.group(2).upper()]
    return int(float(match.group(1)) * multiplier)


def _quantization_bits(quantization: str) -> float | None:
    normalized = quantization.strip().upper()
    if normalized in _QUANTIZATION_BITS:
        return _QUANTIZATION_BITS[normalized]
    match = re.match(r"Q([2-8])", normalized)
    return float(match.group(1)) + 0.5 if match else None


def estimate_resource_envelope(
    *,
    inventory_size_bytes: int,
    parameter_count: int | None,
    quantization_bits: float | None,
    block_count: int | None,
    context_length: int | None,
    embedding_length: int | None,
    head_count: int | None,
    head_count_kv: int | None,
    key_length: int | None,
    value_length: int | None,
    context_tokens: int | None = None,
) -> ModelResourceEstimate:
    """Estimate weights plus a conservative FP16 KV cache.

    The estimate deliberately does not claim to account for CUDA graphs, vision
    projectors, fragmentation, temporary buffers or the Ollama server process.
    """
    weights = inventory_size_bytes if inventory_size_bytes > 0 else None
    if weights is None and parameter_count and quantization_bits:
        weights = int(parameter_count * quantization_bits / 8.0)
    effective_context = context_tokens or context_length
    kv_bytes: int | None = None
    assumptions: list[str] = [
        "Weights use the installed artifact size when Ollama reports it.",
        "KV cache assumes FP16 and does not include temporary inference buffers.",
    ]
    if (
        block_count and effective_context and head_count_kv
        and (key_length or (embedding_length and head_count))
    ):
        head_dim = key_length or max(1, int(embedding_length / max(1, head_count)))
        value_dim = value_length or head_dim
        kv_bytes = int(
            2 * block_count * effective_context * head_count_kv * (head_dim + value_dim) * 2
        )
        assumptions.append(
            "KV cache uses 2 bytes per element, two K/V tensors and the reported layer/head geometry."
        )
    total = weights + kv_bytes if weights is not None and kv_bytes is not None else weights
    return ModelResourceEstimate(
        weights_bytes=weights,
        kv_cache_bytes=kv_bytes,
        total_bytes=total,
        context_tokens=effective_context,
        assumptions=tuple(assumptions),
    )


def inspect_ollama_model(
    model: ModelRef,
    show: dict[str, Any],
    *,
    context_tokens: int | None = None,
) -> ModelIntelligence:
    """Normalize one `/api/show` response and preserve evidence boundaries."""
    details = show.get("details") if isinstance(show.get("details"), dict) else {}
    info = show.get("model_info") if isinstance(show.get("model_info"), dict) else {}
    capabilities = _copy_strings(show.get("capabilities"))
    families = details.get("families")
    family = (
        str(families[0]) if isinstance(families, list) and families
        else str(details.get("family") or "unknown")
    )
    architecture = str(info.get("general.architecture") or family)
    parameter_count = _parameter_count(details, info)
    context_length = _optional_int(_first_value(info, ".context_length"))
    embedding_length = _optional_int(_first_value(info, ".embedding_length"))
    block_count = _optional_int(_first_value(info, ".block_count"))
    head_count = _optional_int(_first_value(info, ".attention.head_count"))
    head_count_kv = _optional_int(_first_value(info, ".attention.head_count_kv"))
    key_length = _optional_int(_first_value(info, ".attention.key_length"))
    value_length = _optional_int(_first_value(info, ".attention.value_length"))
    quantization = str(details.get("quantization_level") or "unknown")
    bits = _quantization_bits(quantization)
    tags = _copy_strings(info.get("general.tags"))
    languages = _copy_strings(info.get("general.languages"))
    base_models: list[str] = []
    for index in range(8):
        name = info.get(f"general.base_model.{index}.name")
        organization = info.get(f"general.base_model.{index}.organization")
        if name:
            base_models.append(
                f"{organization}/{name}" if organization and "/" not in str(name) else str(name)
            )
    estimate = estimate_resource_envelope(
        inventory_size_bytes=model.size_bytes,
        parameter_count=parameter_count,
        quantization_bits=bits,
        block_count=block_count,
        context_length=context_length,
        embedding_length=embedding_length,
        head_count=head_count,
        head_count_kv=head_count_kv,
        key_length=key_length,
        value_length=value_length,
        context_tokens=context_tokens,
    )
    limitations: list[str] = []
    if not capabilities:
        limitations.append("Ollama did not report declared capabilities; execution support is unverified.")
    if "tools" in capabilities:
        limitations.append("Tool support is declared by the artifact; call reliability requires empirical tests.")
    if "vision" in capabilities:
        limitations.append("Vision support is declared; visual task quality is not established by metadata.")
    if parameter_count is None:
        limitations.append("Exact parameter count is unavailable; parameter label may be approximate.")
    if context_length is None:
        limitations.append("Context capacity is not reported by this artifact.")
    evidence = (
        {"claim": "Installed artifact", "value": model.name, "level": "observed",
         "source": "Ollama /api/tags"},
        {"claim": "Artifact digest", "value": model.digest or "not reported", "level": "observed",
         "source": "Ollama /api/tags"},
        {"claim": "Architecture", "value": architecture, "level": "observed",
         "source": "Ollama /api/show model_info"},
        {"claim": "Parameter count", "value": parameter_count, "level": "observed" if parameter_count else "unknown",
         "source": "Ollama /api/show"},
        {"claim": "Context capacity", "value": context_length, "level": "observed" if context_length else "unknown",
         "source": "Ollama /api/show"},
        {"claim": "Declared capabilities", "value": list(capabilities),
         "level": "observed", "source": "Ollama /api/show capabilities"},
        {"claim": "Memory footprint", "value": estimate.total_bytes,
         "level": "inferred", "source": "Host estimate from artifact size and KV geometry"},
        {"claim": "Capability quality", "value": "not established by metadata", "level": "unknown",
         "source": "Requires empirical profiling"},
    )
    return ModelIntelligence(
        model_name=model.name,
        digest=model.digest,
        observed_at=utc_now(),
        family=family,
        architecture=architecture,
        format=str(details.get("format") or "unknown"),
        parameter_size=str(details.get("parameter_size") or (
            f"{parameter_count:,}" if parameter_count else "unknown"
        )),
        parameter_count=parameter_count,
        quantization=quantization,
        quantization_bits=bits,
        context_length=context_length,
        embedding_length=embedding_length,
        block_count=block_count,
        head_count=head_count,
        head_count_kv=head_count_kv,
        key_length=key_length,
        value_length=value_length,
        declared_capabilities=capabilities,
        tags=tags,
        languages=languages,
        license=str(info.get("general.license") or ""),
        license_link=str(info.get("general.license.link") or ""),
        finetune=str(info.get("general.finetune") or ""),
        base_models=tuple(base_models),
        system_prompt=str(show.get("system") or "")[:4_000],
        inventory_size_bytes=model.size_bytes,
        modified_at=model.modified_at,
        resource_estimate=estimate,
        evidence=evidence,
        limitations=tuple(limitations),
    )
