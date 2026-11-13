"""Deterministic verification: outcomes, failure reasons and artifact binding."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from ollama_workstation.agentification.verifier import (
    CheckSpecError,
    VerificationEngine,
    VerificationStatus,
    sha256_file,
    snapshot_workspace,
)


def _workspace(tmp_path: Path) -> Path:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("alpha\n", encoding="utf-8")
    (workspace / "data.json").write_text(json.dumps({"n": 3, "items": [{"id": 7}]}), encoding="utf-8")
    return workspace


def test_passes_only_when_every_check_passes_and_binds_artifact_digests(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    report = VerificationEngine(workspace).verify([
        {"kind": "run_completed"},
        {"kind": "file_exists", "path": "a.txt", "min_bytes": 1},
        {"kind": "file_equals", "path": "a.txt", "content": "alpha"},
        {"kind": "file_contains", "path": "a.txt", "regex": r"^al"},
        {"kind": "file_sha256", "path": "a.txt", "sha256": sha256_file(workspace / "a.txt")},
        {"kind": "json_value", "path": "data.json", "pointer": "/items/0/id", "equals": 7},
        {"kind": "json_schema", "path": "data.json",
         "schema": {"type": "object", "required": ["n"], "properties": {"n": {"type": "integer"}}}},
        {"kind": "file_absent", "path": "nope.txt"},
        {"kind": "final_message_contains", "text": "DONE", "case_sensitive": False},
    ], run_id="run-1", run_status="completed", final_message="all done")
    assert report.status is VerificationStatus.PASSED
    body = report.to_dict()
    assert body["run_id"] == "run-1" and len(body["report_sha256"]) == 64
    assert body["checks"][1]["artifacts"]["a.txt"] == sha256_file(workspace / "a.txt")


@pytest.mark.parametrize(("check", "reason"), [
    ({"kind": "file_exists", "path": "missing.txt"}, "artifact_missing"),
    ({"kind": "file_absent", "path": "a.txt"}, "artifact_unexpected"),
    ({"kind": "file_equals", "path": "a.txt", "content": "beta"}, "content_mismatch"),
    ({"kind": "file_sha256", "path": "a.txt", "sha256": "0" * 64}, "integrity_mismatch"),
    ({"kind": "json_value", "path": "data.json", "pointer": "/n", "equals": 4}, "content_mismatch"),
    ({"kind": "json_value", "path": "data.json", "pointer": "/zz/1", "equals": 4}, "content_mismatch"),
    ({"kind": "json_schema", "path": "data.json", "schema": {"required": ["absent"]}},
     "schema_violation"),
    ({"kind": "json_schema", "path": "a.txt", "schema": {}}, "schema_violation"),
    ({"kind": "final_message_contains", "text": "tests passed"}, "claim_unsupported"),
    ({"kind": "run_completed"}, "run_not_completed"),
])
def test_failures_are_classified(tmp_path: Path, check: dict, reason: str) -> None:
    report = VerificationEngine(_workspace(tmp_path)).verify(
        [check], run_status="step_limit", final_message="I finished everything.")
    assert report.status is VerificationStatus.FAILED
    assert report.failure_reasons == (reason,)


def test_agent_claim_alone_is_never_certified(tmp_path: Path) -> None:
    report = VerificationEngine(_workspace(tmp_path)).verify(
        [], run_status="completed", final_message="Everything is verified and passing.")
    assert report.status is VerificationStatus.NEEDS_HUMAN_REVIEW
    assert not report.passed


def test_path_escape_is_a_failed_check_not_a_read(tmp_path: Path) -> None:
    (tmp_path / "outside.txt").write_text("secret", encoding="utf-8")
    report = VerificationEngine(_workspace(tmp_path)).verify(
        [{"kind": "file_exists", "path": "../outside.txt"}])
    assert report.status is VerificationStatus.FAILED
    assert report.failure_reasons == ("invalid_check",)


def test_diff_rules_detect_unauthorized_missing_and_forbidden_changes(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    baseline = snapshot_workspace(workspace)
    (workspace / "a.txt").write_text("changed\n", encoding="utf-8")
    (workspace / "sub").mkdir()
    (workspace / "sub" / "new.txt").write_text("new", encoding="utf-8")
    engine = VerificationEngine(workspace)
    assert engine.verify([{"kind": "changes_within", "allowed": ["a.txt", "sub"]}],
                         baseline=baseline).passed
    outside = engine.verify([{"kind": "changes_within", "allowed": ["a.txt"]}], baseline=baseline)
    assert outside.failure_reasons == ("unauthorized_change",)
    assert engine.verify([{"kind": "changed", "paths": ["a.txt"]}], baseline=baseline).passed
    assert engine.verify([{"kind": "changed", "paths": ["data.json"]}],
                         baseline=baseline).failure_reasons == ("no_change",)
    assert engine.verify([{"kind": "unchanged", "paths": ["a.txt"]}],
                         baseline=baseline).failure_reasons == ("unauthorized_change",)
    assert engine.verify([{"kind": "unchanged", "paths": ["a.txt"]}]).status is (
        VerificationStatus.INCONCLUSIVE)


def test_command_checks_are_blocked_until_enabled_then_classified(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path)
    ok = {"kind": "command", "argv": ["{python}", "-c", "print('fine')"]}
    blocked = VerificationEngine(workspace).verify([ok])
    assert blocked.status is VerificationStatus.BLOCKED
    assert blocked.failure_reasons == ("command_not_permitted",)
    engine = VerificationEngine(workspace, allow_commands=True, command_timeout_seconds=1)
    passed = engine.verify([ok])
    assert passed.passed and "fine" in passed.checks[0].evidence["stdout_tail"]
    failed = engine.verify([{"kind": "command", "argv": [sys.executable, "-c", "raise SystemExit(3)"]}])
    assert failed.failure_reasons == ("command_failed",)
    assert failed.checks[0].evidence["exit_code"] == 3
    missing = engine.verify([{"kind": "command", "argv": ["no-such-binary-11vated"]}])
    assert missing.status is VerificationStatus.BLOCKED
    slow = engine.verify([{"kind": "command", "argv": [sys.executable, "-c",
                                                        "import time; time.sleep(5)"]}])
    assert slow.status is VerificationStatus.INCONCLUSIVE
    assert slow.failure_reasons == ("command_timeout",)


def test_failed_outranks_blocked_and_inconclusive(tmp_path: Path) -> None:
    report = VerificationEngine(_workspace(tmp_path)).verify([
        {"kind": "command", "argv": ["{python}", "-V"]},
        {"kind": "unchanged", "paths": ["a.txt"]},
        {"kind": "file_exists", "path": "missing.txt"},
    ])
    assert report.status is VerificationStatus.FAILED


@pytest.mark.parametrize("checks", [
    [{"kind": "telepathy"}],
    [{"kind": "file_exists"}],
    [{"kind": "file_contains", "path": "a.txt"}],
    [{"kind": "file_contains", "path": "a.txt", "regex": "("}],
    [{"kind": "command", "argv": "rm -rf /"}],
    [{"kind": "file_exists", "path": "a.txt", "id": "x"},
     {"kind": "file_exists", "path": "a.txt", "id": "x"}],
])
def test_malformed_check_lists_are_rejected_before_execution(tmp_path: Path, checks: list) -> None:
    with pytest.raises(CheckSpecError):
        VerificationEngine(_workspace(tmp_path)).verify(checks)
