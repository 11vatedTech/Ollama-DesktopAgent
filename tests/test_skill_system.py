"""Skill contracts, versioning, dependency resolution and the controlled improvement loop."""

from __future__ import annotations

import pytest
from conftest import make_profile

from ollama_workstation.agentification.harness import HarnessLab, shell_tool_factory
from ollama_workstation.agentification.skills import (
    Skill,
    SkillError,
    SkillEvaluator,
    SkillRegistry,
    builtin_skills,
    render_skills,
)


def _payload(**changes) -> dict:
    base = builtin_skills()[0].to_dict()
    base.update({"skill_id": "custom-skill", "name": "Custom"})
    base.update(changes)
    return base


def test_builtin_skills_are_valid_complete_contracts() -> None:
    skills = builtin_skills()
    assert {s.skill_id for s in skills} == {
        "inspect-before-edit", "recover-from-tool-error", "defect-fix-loop",
        "evidence-backed-report"}
    for skill in skills:
        assert skill.purpose and skill.input_contract and skill.output_contract
        assert skill.procedure and skill.validation_criteria and skill.test_cases
        assert skill.provenance["source"] == "built-in"
        assert Skill.from_dict(skill.to_dict()).to_dict() == skill.to_dict()
    rendered = render_skills(list(skills))
    assert "SKILL defect-fix-loop v1" in rendered and "Never edit a test" in rendered


@pytest.mark.parametrize(("changes", "message"), [
    ({"skill_id": "Bad Id"}, "skill_id"),
    ({"procedure": []}, "procedure"),
    ({"validation_criteria": []}, "validation_criteria"),
    ({"test_cases": []}, "test case"),
    ({"permissions": ["root"]}, "unknown permissions"),
    ({"required_tools": ["run_command"], "permissions": ["read"]}, "shell permission"),
    ({"required_tools": ["write_file"], "permissions": ["read"]}, "write permission"),
    ({"depends_on": ["custom-skill"]}, "depend on itself"),
    ({"procedure": ["x" * 7000]}, "context budget"),
    ({"test_cases": [{"task_id": "t", "objective": "o", "checks": [{"kind": "nope"}]}]},
     "unsupported kind"),
])
def test_invalid_skills_are_rejected(changes: dict, message: str) -> None:
    with pytest.raises((SkillError, ValueError), match=message):
        Skill.from_dict(_payload(**changes))


def test_publishing_versions_preserves_history_and_retirement_is_per_version(store) -> None:
    registry = SkillRegistry(store)
    assert len(registry.install_builtins()) == 4 and registry.install_builtins() == []
    first = registry.publish(_payload())
    second = registry.publish(_payload(purpose="a sharper purpose"))
    assert (first.version, second.version) == (1, 2)
    assert registry.get("custom-skill", 1).purpose != registry.get("custom-skill").purpose
    registry.retire("custom-skill")
    assert registry.status("custom-skill", 2) == "retired"
    assert registry.status("custom-skill", 1) == "active"
    with pytest.raises(SkillError, match="Unknown skill"):
        registry.get("missing")
    with pytest.raises(SkillError, match="Unknown skill dependency"):
        registry.publish(_payload(skill_id="orphan", depends_on=["missing"]))


def test_dependencies_resolve_first_once_and_cycles_fail(store) -> None:
    registry = SkillRegistry(store)
    registry.install_builtins()
    registry.publish(_payload(skill_id="layer-one", depends_on=["inspect-before-edit"]))
    registry.publish(_payload(skill_id="layer-two", depends_on=["layer-one", "inspect-before-edit"]))
    resolved = registry.resolve([{"skill_id": "layer-two", "version": 1},
                                 {"skill_id": "inspect-before-edit", "version": 1}])
    assert [s.skill_id for s in resolved] == ["inspect-before-edit", "layer-one", "layer-two"]
    store.save_skill_version(_payload(skill_id="loop-a", version=1, depends_on=["loop-b"]))
    store.save_skill_version(_payload(skill_id="loop-b", version=1, depends_on=["loop-a"]))
    with pytest.raises(SkillError, match="cycle"):
        registry.resolve([{"skill_id": "loop-a", "version": 1}])


def test_improvement_loop_accepts_a_skill_only_on_measured_evidence(client, repository, store) -> None:
    lab = HarnessLab(client)
    evaluator = SkillEvaluator(lab, store)
    skills = {s.skill_id: s for s in builtin_skills()}
    careless = make_profile(client, repository, "careless:latest")
    improved = evaluator.evaluate("careless:latest", careless, "native_tools",
                                  skills["inspect-before-edit"], trials=2)
    assert (improved.baseline_passed, improved.skill_passed, improved.attempts) == (0, 2, 2)
    assert improved.outcome == "improved" and improved.accepted
    capable = make_profile(client, repository, "native-pro:latest")
    neutral = evaluator.evaluate("native-pro:latest", capable, "native_tools",
                                 skills["inspect-before-edit"], trials=1)
    assert neutral.outcome == "neutral" and neutral.accepted
    hopeless = make_profile(client, repository, "hopeless:latest")
    useless = evaluator.evaluate("hopeless:latest", hopeless, "guided",
                                 skills["recover-from-tool-error"], trials=1)
    assert useless.outcome == "no_evidence" and not useless.accepted
    saved = store.list_skill_evaluations("inspect-before-edit")
    assert [item["outcome"] for item in saved] == ["improved", "neutral"]
    arms = {item["arm"] for item in saved[0]["attempts_detail"]}
    assert arms == {"baseline", "skill"}
    assert len({item["seed"] for item in saved[0]["attempts_detail"]}) == 2  # same seeds per arm


def test_skill_tests_that_execute_code_stay_blocked_until_opted_in(client, repository) -> None:
    skill = next(s for s in builtin_skills() if s.skill_id == "defect-fix-loop")
    profile = make_profile(client, repository, "native-pro:latest")
    blocked = SkillEvaluator(HarnessLab(client)).evaluate(
        "native-pro:latest", profile, "native_tools", skill, trials=1,
        tool_factory=shell_tool_factory)
    assert blocked.skill_passed == 0 and not blocked.accepted
    assert "command_not_permitted" in blocked.attempts_detail[0]["failure_reasons"]
    allowed = SkillEvaluator(HarnessLab(client, allow_command_checks=True)).evaluate(
        "native-pro:latest", profile, "native_tools", skill, trials=1,
        tool_factory=shell_tool_factory)
    assert allowed.skill_passed == 1 and allowed.outcome == "neutral"
