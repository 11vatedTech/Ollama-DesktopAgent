"""Typed domain models for Ollama inventory, evidence, profiles and agents."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

EvidenceLevel = Literal["documented", "observed", "certified", "inferred", "unknown"]
AssessmentStatus = Literal["passed", "partial", "failed", "not_assessed", "unsupported"]


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class ModelRef:
    name: str
    size_bytes: int = 0
    digest: str = ""
    modified_at: str = ""
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class Evidence:
    claim: str
    value: str
    level: EvidenceLevel
    source_name: str = ""
    source_url: str = ""
    observed_at: str = field(default_factory=utc_now)
    notes: str = ""


@dataclass(slots=True)
class CapabilityResult:
    capability: str
    status: AssessmentStatus
    score: float | None = None
    confidence: float = 0.0
    attempts: int = 0
    passed: int = 0
    duration_ms: float | None = None
    method: str = ""
    evidence: list[str] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelProfile:
    model_name: str
    digest: str
    generated_at: str
    family: str = "unknown"
    parameter_size: str = "unknown"
    quantization: str = "unknown"
    architecture: str = "unknown"
    context_length: int | None = None
    declared_capabilities: list[str] = field(default_factory=list)
    declared_purpose: str = "Not established"
    professional_title: str = "Unclassified Model"
    primary_specialization: str = "General-purpose assistance"
    secondary_specializations: list[str] = field(default_factory=list)
    recommended_roles: list[str] = field(default_factory=list)
    agentability_mode: str = "guided"
    agentability_status: str = "not_assessed"
    overall_reliability: float | None = None
    origin_evidence: list[Evidence] = field(default_factory=list)
    capabilities: list[CapabilityResult] = field(default_factory=list)
    known_limitations: list[str] = field(default_factory=list)
    operational: dict[str, Any] = field(default_factory=dict)
    identity_rationale: list[str] = field(default_factory=list)
    identity_confidence: float = 0.0
    profile_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ModelProfile:
        values = dict(payload)
        values["origin_evidence"] = [Evidence(**x) for x in values.get("origin_evidence", [])]
        values["capabilities"] = [CapabilityResult(**x) for x in values.get("capabilities", [])]
        return cls(**values)
