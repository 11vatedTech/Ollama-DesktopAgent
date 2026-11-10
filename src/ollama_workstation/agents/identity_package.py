"""Exportable, digest-bound agent identity packages derived from a model profile."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ollama_workstation.models.schemas import ModelProfile, utc_now


def build_identity_package(profile: ModelProfile) -> dict[str, Any]:
    return {
        "schema_version": "1.0",
        "package_type": "11vated.agent_identity",
        "created_at": utc_now(),
        "identity": {
            "title": profile.professional_title,
            "primary_specialization": profile.primary_specialization,
            "secondary_specializations": profile.secondary_specializations,
            "recommended_roles": profile.recommended_roles,
            "rationale": profile.identity_rationale,
            "confidence": profile.identity_confidence,
        },
        "model_binding": {
            "provider": "ollama",
            "model_name": profile.model_name,
            "digest": profile.digest,
            "family": profile.family,
            "parameter_size": profile.parameter_size,
            "quantization": profile.quantization,
        },
        "execution": {
            "agentability_mode": profile.agentability_mode,
            "agentability_status": profile.agentability_status,
            "default_permissions": "workspace_only_no_shell",
            "capabilities": [
                {"name": item.capability, "status": item.status, "score": item.score,
                 "confidence": item.confidence, "method": item.method,
                 "evidence": item.evidence, "limitations": item.limitations}
                for item in profile.capabilities
            ],
        },
        "origin_evidence": [
            {"claim": item.claim, "value": item.value, "level": item.level,
             "source_name": item.source_name, "source_url": item.source_url,
             "observed_at": item.observed_at, "notes": item.notes}
            for item in profile.origin_evidence
        ],
        "limitations": profile.known_limitations,
        "profile_version": profile.profile_version,
    }


def export_identity_package(profile: ModelProfile, destination: Path) -> Path:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.write_text(json.dumps(build_identity_package(profile), indent=2, ensure_ascii=False),
                         encoding="utf-8")
    temporary.replace(destination)
    return destination
