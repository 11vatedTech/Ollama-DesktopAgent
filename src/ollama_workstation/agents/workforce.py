"""Evidence-based model-to-role matching for a local specialist workforce."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ollama_workstation.models.identity import ROLE_DEFINITIONS
from ollama_workstation.models.schemas import ModelProfile, ModelRef

ROLE_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    definition.key: definition.capabilities for definition in ROLE_DEFINITIONS
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
        """Rank profiles for one canonical role and expose missing evidence."""
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


@dataclass(frozen=True, slots=True)
class WorkforceSelection:
    """A role assignment plus the evidence used to choose it."""

    assignments: dict[str, str]
    reasons: dict[str, str] = field(default_factory=dict)
    rejected: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "assignments": dict(self.assignments),
            "reasons": dict(self.reasons),
            "rejected": {key: list(value) for key, value in self.rejected.items()},
        }


class WorkforceSelector:
    """Selects complementary local specialists from observed profiles.

    Selection is deliberately conservative: a profile must have at least one
    observed role capability. Declared model metadata can route a single model,
    but it cannot create a specialist assignment without evidence.
    """

    @staticmethod
    def select(
        tasks: list[dict[str, Any]],
        profiles: list[ModelProfile],
        installed: list[ModelRef],
        *,
        preferred_models: tuple[str, ...] = (),
    ) -> WorkforceSelection:
        installed_names = {item.name for item in installed}
        available_profiles = [
            profile for profile in profiles
            if profile.model_name in installed_names
        ]
        assignments: dict[str, str] = {}
        reasons: dict[str, str] = {}
        rejected: dict[str, tuple[str, ...]] = {}
        used: set[str] = set()
        for task in tasks:
            task_id = str(task.get("task_id") or "")
            role = str(task.get("role") or "")
            requirements = ROLE_REQUIREMENTS.get(role)
            if requirements is None:
                rejected[task_id] = (f"unknown role: {role}",)
                continue
            matches = WorkforceMatcher.rank(role, available_profiles)
            if not matches:
                rejected[task_id] = (
                    "no installed profile has observed evidence for: " + ", ".join(requirements),
                )
                continue
            # Prefer an unused model, then preferred metadata, then fit. The
            # verifier gets the best distinct profile when one exists.
            ranked = sorted(
                matches,
                key=lambda item: (
                    item.model_name in used,
                    item.model_name not in preferred_models,
                    -item.fit_score,
                    -item.evidence_count,
                    item.model_name,
                ),
            )
            chosen = ranked[0]
            assignments[task_id] = chosen.model_name
            used.add(chosen.model_name)
            missing = (", ".join(chosen.missing_capabilities)
                       or "none")
            reasons[task_id] = (
                f"selected {chosen.model_name} for {role}: fit={chosen.fit_score:.1f}, "
                f"evidence={chosen.evidence_count}, missing={missing}, status={chosen.status}"
            )
            rejected[task_id] = tuple(
                f"{item.model_name}: fit={item.fit_score:.1f}, missing={', '.join(item.missing_capabilities) or 'none'}"
                for item in ranked[1:]
            )
        return WorkforceSelection(assignments, reasons, rejected)
