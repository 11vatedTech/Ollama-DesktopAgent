"""Evidence-driven role and title generation for profiled models."""

from __future__ import annotations

from dataclasses import dataclass

from ollama_workstation.models.schemas import CapabilityResult, ModelProfile


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    key: str
    title: str
    purpose: str
    capabilities: tuple[str, ...]


ROLE_DEFINITIONS = (
    RoleDefinition("software_engineering", "Precision Software Engineer",
                   "Implements, modifies and debugs software with testable changes.",
                   ("code_generation", "code_comprehension", "instruction_following")),
    RoleDefinition("tool_operations", "Workflow Operations Specialist",
                   "Selects and executes tools reliably in bounded multi-step workflows.",
                   ("tool_calling", "structured_actions", "instruction_following")),
    RoleDefinition("visual_analysis", "Visual Intelligence Inspector",
                   "Interprets images and visual context when the model supports vision.",
                   ("vision",)),
    RoleDefinition("quantitative", "Quantitative Problem Solver",
                   "Handles mathematical, logical and quantitative tasks.",
                   ("reasoning", "mathematics")),
    RoleDefinition("general_assistant", "General Intelligence Associate",
                   "Provides broad instruction-following and knowledge assistance.",
                   ("instruction_following", "structured_output")),
)


def _cap(profile: ModelProfile, name: str) -> CapabilityResult | None:
    return next((item for item in profile.capabilities if item.capability == name), None)


def _effective_score(result: CapabilityResult | None) -> float | None:
    if result is None or result.score is None or result.status in {"not_assessed", "unsupported"}:
        return None
    return result.score * (0.65 + 0.35 * min(1.0, result.confidence))


def _title_for(role_key: str, profile: ModelProfile, score: float) -> str:
    code_understanding = _effective_score(_cap(profile, "code_comprehension"))
    code_generation = _effective_score(_cap(profile, "code_generation"))
    if role_key == "software_engineering":
        if (code_understanding is not None and code_generation is not None
                and code_understanding >= code_generation + 10):
            return "Repository Cartographer"
        return ("Precision Software Engineer" if score >= 85 else
                "Applied Software Engineer" if score >= 65 else "Emerging Software Specialist")
    if role_key == "tool_operations":
        tool = _cap(profile, "tool_calling")
        return ("Native Tool Operations Specialist" if tool and tool.status == "passed"
                else "Structured Workflow Operator")
    if role_key == "visual_analysis":
        return "Visual Intelligence Inspector"
    if role_key == "quantitative":
        return "Quantitative Reasoning Specialist"
    if role_key == "general_assistant":
        return "General Intelligence Associate"
    return "Specialist Agent"


def assign_identity(profile: ModelProfile) -> ModelProfile:
    roles = list(ROLE_DEFINITIONS)
    ranked: list[tuple[float, RoleDefinition, list[CapabilityResult]]] = []
    for role in roles:
        observed = [_cap(profile, name) for name in role.capabilities]
        available = [item for item in observed if item and item.score is not None
                     and item.status in {"passed", "partial", "failed"}]
        if not available:
            continue
        scores = [_effective_score(item) for item in available]
        valid_scores = [float(score) for score in scores if score is not None]
        if not valid_scores:
            continue
        coverage = len(available) / len(role.capabilities)
        score = sum(valid_scores) / len(valid_scores) * (0.7 + 0.3 * coverage)
        ranked.append((score, role, available))
    if not ranked:
        profile.professional_title = "Unclassified Model"
        profile.primary_specialization = profile.declared_purpose
        profile.identity_rationale = ["Insufficient empirical evidence to recommend a specialist role."]
        profile.identity_confidence = 0.0
        profile.recommended_roles = []
        return profile
    ranked.sort(key=lambda row: row[0], reverse=True)
    top_score, top_role, evidence = ranked[0]
    profile.professional_title = _title_for(top_role.key, profile, top_score)
    profile.primary_specialization = top_role.purpose
    profile.secondary_specializations = [row[1].purpose for row in ranked[1:3]]
    profile.recommended_roles = [row[1].key for row in ranked]
    profile.identity_rationale = [
        f"{item.capability}: {item.score:.1f}/100 ({item.status}, confidence {item.confidence:.2f})"
        for item in evidence
    ]
    profile.identity_confidence = min(0.45, sum(x.confidence for x in evidence) / len(evidence))
    profile.agentability_mode = (
        "native_tools" if any(x.capability == "tool_calling" and x.status == "passed"
                              for x in profile.capabilities)
        else "structured_actions" if any(x.capability == "structured_actions" and x.status == "passed"
                                         for x in profile.capabilities)
        else "guided"
    )
    profile.agentability_status = "provisional"
    return profile
