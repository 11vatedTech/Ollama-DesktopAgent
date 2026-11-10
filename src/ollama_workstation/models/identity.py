"""Evidence-driven role and title generation for profiled models.

Titles are deterministic, explainable labels derived from host-scored observations.
Publisher descriptions can inform a recommendation but cannot raise empirical scores.
"""

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
                   "Understands, implements and debugs software with testable changes.",
                   ("code_generation", "code_comprehension", "instruction_following")),
    RoleDefinition("repository_analysis", "Repository Cartographer",
                   "Maps unfamiliar codebases, traces dependencies and explains repository structure.",
                   ("code_comprehension", "long_context", "reasoning")),
    RoleDefinition("tool_operations", "Workflow Operations Specialist",
                   "Selects and executes tools reliably in bounded multi-step workflows.",
                   ("tool_calling", "structured_actions", "instruction_following")),
    RoleDefinition("visual_analysis", "Visual Intelligence Inspector",
                   "Interprets images and visual context when the model supports vision.",
                   ("vision",)),
    RoleDefinition("quantitative", "Quantitative Reasoning Specialist",
                   "Handles mathematical, logical and quantitative tasks.",
                   ("reasoning", "mathematics")),
    RoleDefinition("research", "Evidence Synthesis Analyst",
                   "Synthesizes long-form information, evaluates evidence and produces grounded research.",
                   ("research", "long_context", "instruction_following")),
    RoleDefinition("data_extraction", "Precision Data Extraction Specialist",
                   "Extracts, normalizes and transforms information into reliable structured data.",
                   ("data_extraction", "structured_output", "instruction_following")),
    RoleDefinition("creative", "Creative Narrative Architect",
                   "Develops coherent creative concepts, narratives and authored content.",
                   ("creative_writing", "instruction_following")),
    RoleDefinition("multilingual", "Multilingual Communication Specialist",
                   "Understands and communicates across multiple languages.",
                   ("multilingual", "instruction_following")),
    RoleDefinition("planning", "Strategic Workflow Planner",
                   "Decomposes objectives into sequenced, constraint-aware plans.",
                   ("planning", "reasoning", "instruction_following")),
    RoleDefinition("general_assistant", "General Intelligence Associate",
                   "Provides broad instruction-following and knowledge assistance.",
                   ("instruction_following", "structured_output")),
)


def _cap(profile: ModelProfile, name: str) -> CapabilityResult | None:
    return next((item for item in profile.capabilities if item.capability == name), None)


def _effective_score(result: CapabilityResult | None) -> float | None:
    if result is None or result.score is None or result.status in {"not_assessed", "unsupported"}:
        return None
    # Confidence discounts one-shot/small-sample results; it does not turn unknown into zero.
    return result.score * (0.65 + 0.35 * min(1.0, max(0.0, result.confidence)))


def _title_for(role_key: str, profile: ModelProfile, score: float) -> str:
    comprehension = _effective_score(_cap(profile, "code_comprehension"))
    generation = _effective_score(_cap(profile, "code_generation"))
    if role_key == "repository_analysis":
        return "Repository Cartographer"
    if role_key == "software_engineering":
        if (comprehension is not None and generation is not None
                and comprehension >= generation + 10):
            return "Repository Cartographer"
        if (generation is not None and comprehension is not None
                and generation >= comprehension + 10):
            return "Precision Implementation Engineer"
        return ("Precision Software Engineer" if score >= 85 else
                "Applied Software Engineer" if score >= 65 else "Emerging Software Specialist")
    if role_key == "tool_operations":
        native = _cap(profile, "tool_calling")
        structured = _cap(profile, "structured_actions")
        if native and native.status == "passed":
            return "Native Tool Operations Specialist"
        if structured and structured.status == "passed":
            return "Structured Workflow Operator"
        return "Guided Operations Specialist"
    if role_key == "visual_analysis":
        return "Visual Intelligence Inspector"
    if role_key == "quantitative":
        return "Quantitative Reasoning Specialist"
    if role_key == "research":
        return "Evidence Synthesis Analyst"
    if role_key == "data_extraction":
        return "Precision Data Extraction Specialist"
    if role_key == "creative":
        return "Creative Narrative Architect"
    if role_key == "multilingual":
        return "Multilingual Communication Specialist"
    if role_key == "planning":
        return "Strategic Workflow Planner"
    if role_key == "general_assistant":
        return "General Intelligence Associate"
    return "Specialist Agent"


def assign_identity(profile: ModelProfile) -> ModelProfile:
    ranked: list[tuple[float, RoleDefinition, list[CapabilityResult], float]] = []
    for role in ROLE_DEFINITIONS:
        observed = [_cap(profile, name) for name in role.capabilities]
        available = [
            item for item in observed
            if item is not None and item.score is not None
            and item.status in {"passed", "partial", "failed"}
        ]
        if not available:
            continue
        scores = [_effective_score(item) for item in available]
        valid_scores = [float(score) for score in scores if score is not None]
        if not valid_scores:
            continue
        coverage = len(available) / len(role.capabilities)
        score = sum(valid_scores) / len(valid_scores) * (0.7 + 0.3 * coverage)
        mean_confidence = sum(item.confidence for item in available) / len(available)
        ranked.append((score, role, available, mean_confidence))

    if not ranked:
        profile.professional_title = "Unclassified Model"
        profile.primary_specialization = profile.declared_purpose
        profile.secondary_specializations = []
        profile.identity_rationale = [
            "Insufficient empirical evidence to recommend a specialist role.",
            "Publisher-declared purpose is retained separately from measured capability.",
        ]
        profile.identity_confidence = 0.0
        profile.recommended_roles = []
        profile.agentability_status = "not_assessed"
        profile.agentability_mode = "guided"
        return profile

    ranked.sort(key=lambda row: (row[0], row[3]), reverse=True)
    top_score, top_role, evidence, mean_confidence = ranked[0]
    profile.professional_title = _title_for(top_role.key, profile, top_score)
    profile.primary_specialization = top_role.purpose
    profile.secondary_specializations = [row[1].purpose for row in ranked[1:4]]
    profile.recommended_roles = [row[1].key for row in ranked]
    profile.identity_rationale = [
        f"{item.capability}: {item.score:.1f}/100 ({item.status}, "
        f"confidence {item.confidence:.2f}, {item.attempts} attempt(s))"
        for item in evidence
    ]
    # The current smoke suite is deliberately capped below role qualification.
    profile.identity_confidence = min(0.45, mean_confidence * (len(evidence) / len(top_role.capabilities)))
    native = any(item.capability == "tool_calling" and item.status == "passed"
                 for item in profile.capabilities)
    structured = any(item.capability == "structured_actions" and item.status == "passed"
                     for item in profile.capabilities)
    profile.agentability_mode = "native_tools" if native else "structured_actions" if structured else "guided"
    profile.agentability_status = "provisional"
    return profile
