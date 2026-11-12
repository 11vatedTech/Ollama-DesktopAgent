"""Harness qualification measured end to end through the real client and runtime.

The scripted models differ only in protocol ability, so these tests prove the
laboratory detects and reacts to differences. They say nothing about real models.
"""

from __future__ import annotations

from dataclasses import replace

import pytest
from conftest import make_profile

from ollama_workstation.agentification.harness import (
    SUITE_VERSION,
    AttemptRecord,
    HarnessLab,
    HarnessQualification,
    core_suite,
    qualification_issues,
    shell_tool_factory,
)
from ollama_workstation.agentification.tasks import TaskSpec, TaskSpecError, instantiate
from ollama_workstation.agents.runtime import PROMPT_PROTOCOL_VERSION


def _qualify(client, repository, model: str, **options) -> HarnessQualification:
    return HarnessLab(client).qualify(model, make_profile(client, repository, model),
                                      trials=2, seed=11, **options)


def test_models_receive_different_harnesses_from_measured_evidence(client, repository) -> None:
    selected = {model: _qualify(client, repository, model)
                for model in ("native-pro:latest", "json-only:latest", "guided-only:latest",
                              "sloppy-native:latest", "hopeless:latest")}
    assert selected["native-pro:latest"].selected_harness == "native_tools"
    assert selected["json-only:latest"].selected_harness == "structured_actions"
    assert selected["guided-only:latest"].selected_harness == "guided"
    assert selected["sloppy-native:latest"].selected_harness == "structured_actions"
    assert selected["hopeless:latest"].selected_harness is None
    assert selected["hopeless:latest"].status == "unqualified"


def test_declared_tool_support_does_not_qualify_the_native_harness(client, repository) -> None:
    # sloppy-native declares "tools" and emits native calls, but with wrong arguments.
    record = _qualify(client, repository, "sloppy-native:latest")
    native = record.harnesses["native_tools"]
    assert native.status == "unqualified" and native.passed == 0 and native.attempts == 12
    assert record.harnesses["structured_actions"].status == "qualified"


def test_unsupported_native_tools_is_detected_once_and_not_retried(client, repository, scripted) -> None:
    record = _qualify(client, repository, "json-only:latest", harnesses=("native_tools",))
    assert record.harnesses["native_tools"].status == "unsupported"
    assert scripted.requests["json-only:latest"] == 1
    assert record.status == "unqualified"


def test_following_embedded_instructions_caps_a_model_at_provisional(client, repository) -> None:
    record = _qualify(client, repository, "gullible:latest")
    native = record.harnesses["native_tools"]
    assert native.pass_rate >= 0.8  # the score alone would qualify
    assert native.status == "provisional" and "safety" in native.note
    assert record.status == "provisional"
    assert record.workload_suitability["injection_resistance"] == "unsuitable"
    assert native.failure_reasons["artifact_unexpected"] == 2


def test_record_is_bound_and_invalidated_by_any_dependency_change(client, repository) -> None:
    record = _qualify(client, repository, "native-pro:latest")
    assert record.prompt_version == PROMPT_PROTOCOL_VERSION and record.runtime_version == "0.99.0"
    assert record.suite_version == SUITE_VERSION and len(record.suite_sha256) == 64
    assert len(record.attempts) == 36 and all(a.report_sha256 for a in record.attempts)
    current = {"model_digest": record.model_digest, "tool_manifest": record.tool_manifest_sha256,
               "runtime_version": "0.99.0"}
    assert qualification_issues(record, **current, harness="native_tools") == []
    for change, expected in (
        ({"model_digest": "other"}, "digest"),
        ({"tool_manifest": "other"}, "tool manifest"),
        ({"runtime_version": "1.0.0"}, "Ollama changed"),
        ({"runtime_version": None}, "could not be determined"),
        ({"prompt_version": "older"}, "protocol version"),
    ):
        issues = qualification_issues(record, **{**current, **change})
        assert len(issues) == 1 and expected in issues[0]
    restored = HarnessQualification.from_dict(record.to_dict())
    assert restored.to_dict() == record.to_dict()


def test_shell_tool_set_is_a_different_manifest(client, repository) -> None:
    plain = _qualify(client, repository, "native-pro:latest", harnesses=("native_tools",))
    shell = _qualify(client, repository, "native-pro:latest", harnesses=("native_tools",),
                     tool_factory=shell_tool_factory)
    assert "run_command" in shell.tool_names and "run_command" not in plain.tool_names
    assert shell.tool_manifest_sha256 != plain.tool_manifest_sha256


def test_trials_are_seeded_reproducible_and_not_memorisable() -> None:
    spec = core_suite()[0]
    first, again, other = instantiate(spec, 5), instantiate(spec, 5), instantiate(spec, 6)
    assert first.files == again.files and first.checks == again.checks
    assert first.values["code"] != other.values["code"]
    assert first.values["code"] in first.files["inventory.txt"]
    edit = instantiate(core_suite()[2], 3)
    assert edit.values["next"] == edit.values["current"] + 1
    assert edit.checks[1]["equals"] == edit.values["next"]  # typed, not a string


def test_selection_rules_and_thresholds() -> None:
    def attempts(mode: str, passes: int, total: int, category: str = "multi_step"):
        return [AttemptRecord(f"t{i}", category, mode, 0, i, i < passes, "completed", 2, 2, 0,
                              1.0, 0, 0, (), "", "x") for i in range(total)]

    summarize = HarnessLab.summarize
    assert summarize("guided", attempts("guided", 12, 12)).status == "qualified"
    assert summarize("guided", attempts("guided", 5, 6)).status == "provisional"  # CI too wide
    assert summarize("guided", attempts("guided", 2, 6)).status == "unqualified"
    assert summarize("guided", []).status == "not_tested"
    tie = {mode: summarize(mode, attempts(mode, 12, 12))
           for mode in ("guided", "structured_actions", "native_tools")}
    assert HarnessLab.select(tie)[0] == "native_tools"
    tie["native_tools"] = summarize("native_tools", attempts("native_tools", 10, 12))
    assert HarnessLab.select(tie)[0] == "structured_actions"
    weak = replace(tie["guided"], status="provisional")
    assert HarnessLab.select({"guided": weak})[1] == "provisional"


def test_qualification_requires_a_digest_and_valid_tasks(client, repository) -> None:
    profile = replace(make_profile(client, repository, "native-pro:latest"), digest="")
    with pytest.raises(ValueError, match="digest"):
        HarnessLab(client).qualify("native-pro:latest", profile)
    with pytest.raises(TaskSpecError):
        TaskSpec("bad", "x", "objective", files={"../escape.txt": "x"})
    with pytest.raises(TaskSpecError):
        TaskSpec("bad", "x", "objective", variables={"v": {"kind": "magic"}})
