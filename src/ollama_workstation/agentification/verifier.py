"""Deterministic, host-executed verification of agent work.

An agent's statement that it finished is never evidence. This engine runs
host-authored checks against the actual workspace and returns one of five
outcomes. Every result is bound to the run identity and to the SHA-256 of
each artifact it inspected, so a verdict cannot be reused for different
files or a different execution.

Model-based review may be attached as additional evidence by a caller, but it
cannot turn a failed or missing deterministic check into a pass.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import jsonschema

from ollama_workstation.models.schemas import utc_now

VERIFIER_VERSION = "1.0"
MAX_ARTIFACT_BYTES = 8_000_000
MAX_SNAPSHOT_FILES = 5_000
COMMAND_OUTPUT_TAIL = 4_000
IGNORED_DIRECTORIES = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "node_modules",
})


class VerificationStatus(StrEnum):
    PASSED = "passed"
    FAILED = "failed"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


class FailureReason(StrEnum):
    """Stable classification of why a check did not pass."""

    NONE = ""
    ARTIFACT_MISSING = "artifact_missing"
    ARTIFACT_UNEXPECTED = "artifact_unexpected"
    CONTENT_MISMATCH = "content_mismatch"
    INTEGRITY_MISMATCH = "integrity_mismatch"
    SCHEMA_VIOLATION = "schema_violation"
    COMMAND_FAILED = "command_failed"
    COMMAND_TIMEOUT = "command_timeout"
    COMMAND_NOT_PERMITTED = "command_not_permitted"
    COMMAND_UNAVAILABLE = "command_unavailable"
    UNAUTHORIZED_CHANGE = "unauthorized_change"
    NO_CHANGE = "no_change"
    CLAIM_UNSUPPORTED = "claim_unsupported"
    RUN_NOT_COMPLETED = "run_not_completed"
    INVALID_CHECK = "invalid_check"
    UNREADABLE = "unreadable"


class CheckSpecError(ValueError):
    """A check specification is malformed. Raised before anything is executed."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 16), b""):
            digest.update(block)
    return digest.hexdigest()


def snapshot_workspace(workspace: Path) -> dict[str, str]:
    """Map every regular workspace file to its SHA-256 for diff analysis."""
    root = workspace.resolve()
    snapshot: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_DIRECTORIES for part in relative.parts):
            continue
        if path.is_symlink() or not path.is_file():
            continue
        if len(snapshot) >= MAX_SNAPSHOT_FILES:
            raise ValueError(f"Workspace exceeds the {MAX_SNAPSHOT_FILES}-file snapshot limit")
        snapshot[relative.as_posix()] = sha256_file(path)
    return snapshot


def diff_snapshots(before: Mapping[str, str], after: Mapping[str, str]) -> dict[str, list[str]]:
    return {
        "added": sorted(set(after) - set(before)),
        "removed": sorted(set(before) - set(after)),
        "modified": sorted(name for name in set(before) & set(after) if before[name] != after[name]),
    }


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    kind: str
    status: VerificationStatus
    reason: FailureReason = FailureReason.NONE
    detail: str = ""
    artifacts: dict[str, str] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "check_id": self.check_id, "kind": self.kind, "status": self.status.value,
            "reason": self.reason.value, "detail": self.detail,
            "artifacts": dict(self.artifacts), "evidence": dict(self.evidence),
        }


@dataclass(frozen=True, slots=True)
class VerificationReport:
    status: VerificationStatus
    run_id: str
    workspace: str
    checks: tuple[CheckResult, ...]
    verified_at: str
    verifier_version: str = VERIFIER_VERSION
    reviews: tuple[dict[str, Any], ...] = ()

    @property
    def passed(self) -> bool:
        return self.status is VerificationStatus.PASSED

    @property
    def failure_reasons(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(
            item.reason.value for item in self.checks
            if item.status is not VerificationStatus.PASSED and item.reason.value
        ))

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "status": self.status.value, "run_id": self.run_id, "workspace": self.workspace,
            "verified_at": self.verified_at, "verifier_version": self.verifier_version,
            "failure_reasons": list(self.failure_reasons),
            "checks": [item.to_dict() for item in self.checks],
            "reviews": [dict(item) for item in self.reviews],
        }
        body["report_sha256"] = hashlib.sha256(
            json.dumps(body, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()
        return body


_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "run_completed": (),
    "file_exists": ("path",),
    "file_absent": ("path",),
    "file_contains": ("path",),
    "file_equals": ("path", "content"),
    "file_sha256": ("path", "sha256"),
    "json_value": ("path", "pointer", "equals"),
    "json_schema": ("path", "schema"),
    "final_message_contains": (),
    "changes_within": ("allowed",),
    "changed": ("paths",),
    "unchanged": ("paths",),
    "command": ("argv",),
}


def validate_checks(checks: Sequence[Mapping[str, Any]]) -> None:
    """Reject a malformed check list before any check runs."""
    seen: set[str] = set()
    for index, check in enumerate(checks):
        if not isinstance(check, Mapping):
            raise CheckSpecError(f"Check {index} must be an object")
        kind = str(check.get("kind") or "")
        if kind not in _REQUIRED_FIELDS:
            raise CheckSpecError(f"Check {index} has unsupported kind {kind!r}")
        missing = [name for name in _REQUIRED_FIELDS[kind] if name not in check]
        if missing:
            raise CheckSpecError(f"Check {index} ({kind}) is missing: {', '.join(missing)}")
        if kind in {"file_contains", "final_message_contains"} and not (
            "text" in check or "regex" in check
        ):
            raise CheckSpecError(f"Check {index} ({kind}) needs 'text' or 'regex'")
        if "regex" in check:
            try:
                re.compile(str(check["regex"]))
            except re.error as exc:
                raise CheckSpecError(f"Check {index} has an invalid regex: {exc}") from exc
        if kind == "command":
            argv = check["argv"]
            if (not isinstance(argv, list) or not argv
                    or not all(isinstance(item, str) and item for item in argv)):
                raise CheckSpecError(f"Check {index} argv must be a non-empty list of strings")
        check_id = str(check.get("id") or f"{kind}-{index}")
        if check_id in seen:
            raise CheckSpecError(f"Duplicate check id: {check_id}")
        seen.add(check_id)


class VerificationEngine:
    """Run deterministic checks. The engine never executes model-authored commands:
    ``command`` checks come from the host-authored check list and run only when
    the caller enables them."""

    def __init__(
        self,
        workspace: Path,
        *,
        allow_commands: bool = False,
        command_timeout_seconds: float = 180.0,
        command_runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    ) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.allow_commands = allow_commands
        self.command_timeout_seconds = max(1.0, min(float(command_timeout_seconds), 1_800.0))
        self._run = command_runner or subprocess.run

    def _resolve(self, relative: str) -> Path:
        candidate = (self.workspace / relative).resolve()
        candidate.relative_to(self.workspace)  # ValueError when the path escapes
        return candidate

    def verify(
        self,
        checks: Sequence[Mapping[str, Any]],
        *,
        run_id: str = "",
        run_status: str | None = None,
        final_message: str = "",
        baseline: Mapping[str, str] | None = None,
        reviews: Sequence[Mapping[str, Any]] = (),
    ) -> VerificationReport:
        validate_checks(checks)
        results: list[CheckResult] = []
        for index, check in enumerate(checks):
            kind = str(check["kind"])
            check_id = str(check.get("id") or f"{kind}-{index}")
            try:
                results.append(self._run_check(
                    check_id, kind, check, run_status=run_status,
                    final_message=final_message, baseline=baseline,
                ))
            except ValueError as exc:
                results.append(CheckResult(
                    check_id, kind, VerificationStatus.FAILED, FailureReason.INVALID_CHECK,
                    f"Check could not be evaluated safely: {exc}",
                ))
            except OSError as exc:
                results.append(CheckResult(
                    check_id, kind, VerificationStatus.INCONCLUSIVE, FailureReason.UNREADABLE,
                    f"{type(exc).__name__}: {exc}",
                ))
        return VerificationReport(
            self._overall(results), run_id, str(self.workspace), tuple(results), utc_now(),
            reviews=tuple(dict(item) for item in reviews),
        )

    @staticmethod
    def _overall(results: Sequence[CheckResult]) -> VerificationStatus:
        if not results:
            # Nothing objective was checked, so nothing can be certified.
            return VerificationStatus.NEEDS_HUMAN_REVIEW
        statuses = {item.status for item in results}
        for status in (VerificationStatus.FAILED, VerificationStatus.BLOCKED,
                       VerificationStatus.INCONCLUSIVE, VerificationStatus.NEEDS_HUMAN_REVIEW):
            if status in statuses:
                return status
        return VerificationStatus.PASSED

    def _read_text(self, path: Path) -> str:
        if path.stat().st_size > MAX_ARTIFACT_BYTES:
            raise OSError(f"{path.name} exceeds the {MAX_ARTIFACT_BYTES}-byte verification limit")
        return path.read_text(encoding="utf-8", errors="replace")

    def _run_check(
        self, check_id: str, kind: str, check: Mapping[str, Any], *,
        run_status: str | None, final_message: str, baseline: Mapping[str, str] | None,
    ) -> CheckResult:
        passed, failed = VerificationStatus.PASSED, VerificationStatus.FAILED

        def result(status: VerificationStatus, reason: FailureReason = FailureReason.NONE,
                   detail: str = "", artifacts: dict[str, str] | None = None,
                   **evidence: Any) -> CheckResult:
            return CheckResult(check_id, kind, status, reason, detail, artifacts or {}, evidence)

        if kind == "run_completed":
            if run_status is None:
                return result(VerificationStatus.INCONCLUSIVE, FailureReason.RUN_NOT_COMPLETED,
                              "No run status was supplied to the verifier.")
            ok = run_status == "completed"
            return result(passed if ok else failed,
                          FailureReason.NONE if ok else FailureReason.RUN_NOT_COMPLETED,
                          f"run status is {run_status!r}", run_status=run_status)

        if kind == "final_message_contains":
            ok = self._matches(final_message, check)
            return result(passed if ok else failed,
                          FailureReason.NONE if ok else FailureReason.CLAIM_UNSUPPORTED,
                          "final message matched" if ok else "final message lacks the required content",
                          final_message_sha256=hashlib.sha256(final_message.encode("utf-8")).hexdigest())

        if kind in {"changes_within", "changed", "unchanged"}:
            if baseline is None:
                return result(VerificationStatus.INCONCLUSIVE, FailureReason.INVALID_CHECK,
                              "No baseline snapshot was captured before the run.")
            current = snapshot_workspace(self.workspace)
            diff = diff_snapshots(baseline, current)
            touched = sorted({*diff["added"], *diff["removed"], *diff["modified"]})
            if kind == "changes_within":
                patterns = [str(item) for item in check["allowed"]]
                outside = [name for name in touched
                           if not any(Path(name).match(pattern) or name == pattern
                                      or name.startswith(pattern.rstrip("/") + "/")
                                      for pattern in patterns)]
                return result(failed if outside else passed,
                              FailureReason.UNAUTHORIZED_CHANGE if outside else FailureReason.NONE,
                              ("changed outside the allowed set: " + ", ".join(outside[:20]))
                              if outside else f"{len(touched)} change(s), all within the allowed set",
                              {name: current.get(name, "") for name in touched}, diff=diff)
            names = [str(item) for item in check["paths"]]
            if kind == "changed":
                still = [name for name in names if name not in touched]
                return result(failed if still else passed,
                              FailureReason.NO_CHANGE if still else FailureReason.NONE,
                              ("not changed: " + ", ".join(still)) if still else "all listed paths changed",
                              {name: current.get(name, "") for name in names}, diff=diff)
            moved = [name for name in names if name in touched]
            return result(failed if moved else passed,
                          FailureReason.UNAUTHORIZED_CHANGE if moved else FailureReason.NONE,
                          ("unexpectedly changed: " + ", ".join(moved)) if moved
                          else "all listed paths are unchanged",
                          {name: current.get(name, "") for name in names}, diff=diff)

        if kind == "command":
            return self._command(check_id, check)

        relative = str(check["path"])
        path = self._resolve(relative)
        if kind == "file_absent":
            exists = path.exists()
            return result(failed if exists else passed,
                          FailureReason.ARTIFACT_UNEXPECTED if exists else FailureReason.NONE,
                          f"{relative} {'exists' if exists else 'is absent'}")
        if not path.is_file():
            return result(failed, FailureReason.ARTIFACT_MISSING, f"{relative} does not exist")
        artifacts = {relative: sha256_file(path)}
        if kind == "file_exists":
            minimum = int(check.get("min_bytes", 0))
            size = path.stat().st_size
            ok = size >= minimum
            return result(passed if ok else failed,
                          FailureReason.NONE if ok else FailureReason.CONTENT_MISMATCH,
                          f"{relative} is {size} byte(s)", artifacts, bytes=size)
        if kind == "file_sha256":
            ok = artifacts[relative] == str(check["sha256"]).lower()
            return result(passed if ok else failed,
                          FailureReason.NONE if ok else FailureReason.INTEGRITY_MISMATCH,
                          "digest matches" if ok else "digest differs from the expected value",
                          artifacts)
        text = self._read_text(path)
        if kind == "file_contains":
            ok = self._matches(text, check)
            return result(passed if ok else failed,
                          FailureReason.NONE if ok else FailureReason.CONTENT_MISMATCH,
                          f"{relative} {'contains' if ok else 'lacks'} the required content", artifacts)
        if kind == "file_equals":
            expected = str(check["content"])
            actual = text
            if check.get("strip", True):
                expected, actual = expected.strip(), actual.strip()
            ok = actual == expected
            return result(passed if ok else failed,
                          FailureReason.NONE if ok else FailureReason.CONTENT_MISMATCH,
                          f"{relative} {'matches' if ok else 'differs from'} the expected content",
                          artifacts)
        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            return result(failed, FailureReason.SCHEMA_VIOLATION,
                          f"{relative} is not valid JSON: {exc.msg}", artifacts)
        if kind == "json_schema":
            errors = sorted(
                jsonschema.Draft202012Validator(check["schema"]).iter_errors(document),
                key=lambda error: list(error.absolute_path),
            )
            return result(failed if errors else passed,
                          FailureReason.SCHEMA_VIOLATION if errors else FailureReason.NONE,
                          "; ".join(error.message for error in errors[:5]) or "schema satisfied",
                          artifacts)
        # json_value
        value: Any = document
        for token in [part for part in str(check["pointer"]).split("/") if part]:
            try:
                value = value[int(token)] if isinstance(value, list) else value[token]
            except (KeyError, IndexError, ValueError, TypeError):
                return result(failed, FailureReason.CONTENT_MISMATCH,
                              f"{relative} has no value at {check['pointer']}", artifacts)
        ok = value == check["equals"]
        return result(passed if ok else failed,
                      FailureReason.NONE if ok else FailureReason.CONTENT_MISMATCH,
                      f"value at {check['pointer']} is {value!r}", artifacts)

    @staticmethod
    def _matches(text: str, check: Mapping[str, Any]) -> bool:
        if "regex" in check:
            return re.search(str(check["regex"]), text, re.MULTILINE) is not None
        needle = str(check["text"])
        if check.get("case_sensitive", True):
            return needle in text
        return needle.casefold() in text.casefold()

    def _command(self, check_id: str, check: Mapping[str, Any]) -> CheckResult:
        argv = [str(item) for item in check["argv"]]
        display = list(argv)
        if argv[0] == "{python}":
            argv[0] = sys.executable
        expected = int(check.get("expect_exit", 0))
        if not self.allow_commands:
            return CheckResult(check_id, "command", VerificationStatus.BLOCKED,
                               FailureReason.COMMAND_NOT_PERMITTED,
                               "Command checks are disabled for this verification.",
                               evidence={"argv": display})
        cwd = self._resolve(str(check.get("cwd", ".")))
        timeout = min(self.command_timeout_seconds,
                      float(check.get("timeout_seconds", self.command_timeout_seconds)))
        try:
            completed = self._run(
                argv, cwd=cwd, capture_output=True, text=True, timeout=timeout,
                shell=False, check=False, stdin=subprocess.DEVNULL,
            )
        except FileNotFoundError:
            return CheckResult(check_id, "command", VerificationStatus.BLOCKED,
                               FailureReason.COMMAND_UNAVAILABLE,
                               f"Executable not found: {display[0]}", evidence={"argv": display})
        except subprocess.TimeoutExpired:
            return CheckResult(check_id, "command", VerificationStatus.INCONCLUSIVE,
                               FailureReason.COMMAND_TIMEOUT,
                               f"Command exceeded {timeout:.0f} seconds",
                               evidence={"argv": display})
        ok = completed.returncode == expected
        return CheckResult(
            check_id, "command",
            VerificationStatus.PASSED if ok else VerificationStatus.FAILED,
            FailureReason.NONE if ok else FailureReason.COMMAND_FAILED,
            f"exit code {completed.returncode} (expected {expected})",
            evidence={
                "argv": display, "exit_code": completed.returncode,
                "stdout_tail": (completed.stdout or "")[-COMMAND_OUTPUT_TAIL:],
                "stderr_tail": (completed.stderr or "")[-COMMAND_OUTPUT_TAIL:],
            },
        )
