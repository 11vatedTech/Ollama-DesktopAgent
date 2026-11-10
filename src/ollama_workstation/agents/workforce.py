"""Evidence-based model-to-role matching for a local specialist workforce."""

from __future__ import annotations

from dataclasses import dataclass

from ollama_workstation.models.schemas import ModelProfile

ROLE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "software_engineering": ("code_generation", "instruction_following"),
    "repository_analysis": ("code_comprehension", "long_context"),
    "workflow_operations": ("tool_calling", "structured_actions"),
    "research": ("structured_output", "instruction_following", "long_context"),
    "visual_analysis": ("vision",),
    "quantitative_analysis": ("mathematics", "reasoning"),
    "data_extraction": ("data_extraction", "structured_output", "instruction_following"),
    "creative": ("creative_writing", "instruction_following"),
    "multilingual": ("multilingual", "instruction_following"),
    "planning": ("planning", "reasoning", "instruction_following"),
    "general_assistance": ("instruction_following",),
}


@dataclass(frozen=True, slots=True)
class RoleMatch:
    model_name: str
    professional_title: str
    role: str
    fit_score: float
    evidence_count: int
    missing_capabilities: tuple[str, ...]
    status: str


class WorkforceMatcher:
    """Ranks local profiles for requested roles without treating unknown as zero skill."""

    @staticmethod
    def rank(role: str, profiles: list[ModelProfile]) -> list[RoleMatch]:
        requirements = ROLE_REQUIREMENTS.get(role)
        if requirements is None:
            raise ValueError(f"Unknown workforce role: {role}")
        matches: list[RoleMatch] = []
        for profile in profiles:
            indexed = {item.capability: item for item in profile.capabilities}
            observed = [indexed[name] for name in requirements
                        if name in indexed and indexed[name].score is not None
                        and indexed[name].status in {"passed", "partial", "failed"}]
            missing = tuple(name for name in requirements if name not in indexed
                            or indexed[name].score is None
                            or indexed[name].status in {"not_assessed", "unsupported"})
            if not observed:
                continue
            adjusted_scores = [float(item.score) * (0.65 + 0.35 * min(1.0, item.confidence))
                               for item in observed if item.score is not None]
            fit = sum(adjusted_scores) / len(adjusted_scores)
            completeness = len(observed) / len(requirements)
            fit *= 0.7 + 0.3 * completeness
            role_qualified = (
                profile.agentability_status == "role_qualified"
                and not missing
                and all(item.confidence >= 0.7 and item.status == "passed" for item in observed)
            )
            matches.append(RoleMatch(
                profile.model_name, profile.professional_title, role, round(fit, 1),
                len(observed), missing, "role-qualified" if role_qualified else "provisional",
            ))
        return sorted(matches, key=lambda item: (item.fit_score, item.evidence_count), reverse=True)
