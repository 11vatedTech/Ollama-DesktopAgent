"""Recovery contract for durable agent runs.

This module is the single source of truth for checkpoint eligibility. The agent
runtime, the CLI and the desktop recovery panel all use it, so a run cannot be
"resumable" in one entry point and blocked in another.

A checkpoint is a redacted, size-bounded snapshot of the conversation taken at a
safe execution boundary. Restoration is fail-closed: a corrupted payload, an
altered binding column, an unsupported protocol, a stale snapshot, a superseded
run or an interrupted tool operation is never executed automatically.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

CHECKPOINT_PROTOCOL = 2
DEFAULT_MAX_CHECKPOINT_AGE_SECONDS = 72 * 60 * 60
FUTURE_TIMESTAMP_TOLERANCE_SECONDS = 300
MAX_STEP_BUDGET = 200
MAX_TOOL_CALL_COUNTER = 10_000

RESUMABLE_STATES = frozenset({"ready", "awaiting_model"})
REVIEW_STATES = frozenset({"tool_in_progress", "needs_review"})
ALL_CHECKPOINT_STATES = RESUMABLE_STATES | REVIEW_STATES

BINDING_FIELDS = (
    "model_name",
    "model_digest",
    "agent_title",
    "workspace",
    "harness_mode",
    "tool_manifest_sha256",
)


class CheckpointRejected(ValueError):
    """Base class for a checkpoint that must not be used."""


class CheckpointIntegrityError(CheckpointRejected):
    """The checkpoint is malformed or failed an integrity check."""


class CheckpointNotResumable(CheckpointRejected):
    """The checkpoint is intact but its state or binding forbids resumption."""


class CheckpointStale(CheckpointNotResumable):
    """The checkpoint is older than the recovery window."""


class CheckpointSuperseded(CheckpointNotResumable):
    """The run already reached a terminal status."""


class CheckpointExhausted(CheckpointNotResumable):
    """The original step budget was fully consumed."""


class RecoveryStatus(StrEnum):
    RESUMABLE = "resumable"
    LEASED = "leased"
    WORKSPACE_BUSY = "workspace_busy"
    NEEDS_REVIEW = "needs_review"
    STALE = "stale"
    CORRUPT = "corrupt"
    SUPERSEDED = "superseded"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class RecoveryCandidate:
    run_id: str
    status: RecoveryStatus
    reason: str
    task: str
    model_name: str
    model_digest: str
    agent_title: str
    workspace: str
    harness_mode: str
    step: int
    step_budget: int
    tool_calls: int
    updated_at: str
    pending_operations: tuple[str, ...] = ()
    lease_active: bool = False
    lease_expires_at: str = ""
    workspace_active: bool = False
    workspace_expires_at: str = ""
    workspace_run_id: str = ""

    @property
    def resumable(self) -> bool:
        return self.status is RecoveryStatus.RESUMABLE

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status.value,
            "reason": self.reason,
            "task": self.task,
            "model_name": self.model_name,
            "model_digest": self.model_digest,
            "agent_title": self.agent_title,
            "workspace": self.workspace,
            "harness_mode": self.harness_mode,
            "step": self.step,
            "step_budget": self.step_budget,
            "tool_calls": self.tool_calls,
            "updated_at": self.updated_at,
            "pending_operations": list(self.pending_operations),
            "lease_active": self.lease_active,
            "lease_expires_at": self.lease_expires_at,
            "workspace_active": self.workspace_active,
            "workspace_expires_at": self.workspace_expires_at,
            "workspace_run_id": self.workspace_run_id,
        }


def _safe_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _utc(value: datetime | None = None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        return current.replace(tzinfo=UTC)
    return current.astimezone(UTC)


def parse_checkpoint_time(value: Any) -> datetime:
    """Parse a stored ISO-8601 timestamp, rejecting ambiguous or naive values."""
    if not isinstance(value, str) or not value.strip():
        raise CheckpointIntegrityError("Checkpoint timestamp is missing")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise CheckpointIntegrityError("Checkpoint timestamp is not valid ISO-8601") from exc
    if parsed.tzinfo is None:
        raise CheckpointIntegrityError("Checkpoint timestamp has no timezone")
    return parsed.astimezone(UTC)


def _payload(checkpoint: Any) -> dict[str, Any]:
    if not isinstance(checkpoint, dict):
        raise CheckpointIntegrityError("Checkpoint record is not an object")
    payload = checkpoint.get("payload")
    if not isinstance(payload, dict):
        raise CheckpointIntegrityError("Checkpoint payload is missing or malformed")
    return payload


def _pending_names(payload: dict[str, Any]) -> tuple[str, ...]:
    operations = payload.get("pending_operations") or []
    if not isinstance(operations, list):
        raise CheckpointIntegrityError("Checkpoint pending operations are malformed")
    names: list[str] = []
    for item in operations[:32]:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise CheckpointIntegrityError("Checkpoint pending operation is malformed")
        names.append(item["name"][:120] or "unknown")
    return tuple(names)


def verify_checkpoint_structure(
    checkpoint: Any,
    *,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_CHECKPOINT_AGE_SECONDS,
) -> dict[str, Any]:
    """Validate protocol, state, payload shape, counters and freshness.

    Binding to the requested model/workspace/harness/tool manifest is verified
    separately by :func:`verify_resume_checkpoint`. Nothing here mutates state.
    """
    payload = _payload(checkpoint)
    protocol = payload.get("protocol")
    if isinstance(protocol, bool) or protocol != CHECKPOINT_PROTOCOL:
        raise CheckpointIntegrityError(
            f"Unsupported checkpoint protocol: {protocol!r}; expected {CHECKPOINT_PROTOCOL}"
        )
    if not isinstance(checkpoint.get("run_id"), str) or not checkpoint["run_id"]:
        raise CheckpointIntegrityError("Checkpoint has no run identifier")

    status = checkpoint.get("run_status")
    if status == "completed":
        raise CheckpointSuperseded("Run already completed; no recovery is required")

    state = checkpoint.get("state")
    if state in REVIEW_STATES:
        raise CheckpointNotResumable(
            "Run stopped during a tool operation and requires human review; "
            "automatic replay is blocked"
        )
    if state not in RESUMABLE_STATES:
        raise CheckpointIntegrityError(f"Unsupported checkpoint state: {state!r}")
    if _pending_names(payload):
        raise CheckpointIntegrityError(
            "A resumable checkpoint cannot contain pending tool operations"
        )

    step = checkpoint.get("step")
    if isinstance(step, bool) or not isinstance(step, int) or step < 0:
        raise CheckpointIntegrityError("Checkpoint step counter is invalid")
    tool_calls = checkpoint.get("tool_calls")
    if (
        isinstance(tool_calls, bool)
        or not isinstance(tool_calls, int)
        or not 0 <= tool_calls <= MAX_TOOL_CALL_COUNTER
    ):
        raise CheckpointIntegrityError("Checkpoint tool-call counter is invalid")

    budget = payload.get("step_budget")
    if (
        isinstance(budget, bool)
        or not isinstance(budget, int)
        or not 1 <= budget <= MAX_STEP_BUDGET
    ):
        raise CheckpointIntegrityError("Checkpoint step budget is invalid")
    if step >= budget:
        raise CheckpointExhausted(
            f"Step budget exhausted ({step}/{budget}); start a new run with a larger budget"
        )

    resume_count = payload.get("resume_count", 0)
    if isinstance(resume_count, bool) or not isinstance(resume_count, int) or resume_count < 0:
        raise CheckpointIntegrityError("Checkpoint resume counter is invalid")

    messages = payload.get("messages")
    if (
        not isinstance(messages, list)
        or len(messages) < 2
        or not all(isinstance(item, dict) for item in messages)
        or messages[0].get("role") != "system"
        or messages[1].get("role") != "user"
    ):
        raise CheckpointIntegrityError(
            "Checkpoint does not contain a valid system/user conversation prefix"
        )
    if not isinstance(payload.get("task"), str):
        raise CheckpointIntegrityError("Checkpoint task binding is missing")

    updated_at = parse_checkpoint_time(checkpoint.get("updated_at"))
    current = _utc(now)
    if updated_at - current > timedelta(seconds=FUTURE_TIMESTAMP_TOLERANCE_SECONDS):
        raise CheckpointStale(
            "Checkpoint timestamp is in the future; the local clock or database is untrusted"
        )
    if max_age_seconds > 0 and current - updated_at > timedelta(seconds=max_age_seconds):
        hours = max_age_seconds / 3600
        raise CheckpointStale(
            f"Checkpoint is stale (older than the {hours:g}-hour recovery window)"
        )
    return payload


def verify_resume_checkpoint(
    checkpoint: Any,
    *,
    binding: dict[str, str],
    task: str,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_CHECKPOINT_AGE_SECONDS,
) -> dict[str, Any]:
    """Validate a checkpoint against the exact execution binding it will resume."""
    payload = verify_checkpoint_structure(
        checkpoint, now=now, max_age_seconds=max_age_seconds
    )
    mismatches = [
        field for field in BINDING_FIELDS if checkpoint.get(field) != binding.get(field)
    ]
    if mismatches:
        raise CheckpointNotResumable(
            "Execution binding changed since the checkpoint: " + ", ".join(mismatches)
        )
    if payload.get("task") != task:
        raise CheckpointNotResumable(
            "Saved task does not match this request; checkpoints are task-bound"
        )
    return payload


def classify_checkpoint(
    checkpoint: dict[str, Any],
    *,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_CHECKPOINT_AGE_SECONDS,
) -> RecoveryCandidate:
    """Classify an integrity-verified checkpoint for display and eligibility."""
    payload = checkpoint.get("payload") if isinstance(checkpoint.get("payload"), dict) else {}
    pending = ()
    try:
        pending = _pending_names(payload)
    except CheckpointIntegrityError:
        pending = ()
    base = RecoveryCandidate(
        run_id=str(checkpoint.get("run_id") or ""),
        status=RecoveryStatus.CORRUPT,
        reason="",
        task=str(payload.get("task") or ""),
        model_name=str(checkpoint.get("model_name") or ""),
        model_digest=str(checkpoint.get("model_digest") or ""),
        agent_title=str(checkpoint.get("agent_title") or ""),
        workspace=str(checkpoint.get("workspace") or ""),
        harness_mode=str(checkpoint.get("harness_mode") or ""),
        step=_safe_int(checkpoint.get("step")),
        step_budget=_safe_int(payload.get("step_budget")),
        tool_calls=_safe_int(checkpoint.get("tool_calls")),
        updated_at=str(checkpoint.get("updated_at") or ""),
        pending_operations=pending,
    )
    try:
        verify_checkpoint_structure(
            checkpoint, now=now, max_age_seconds=max_age_seconds
        )
    except CheckpointStale as exc:
        return replace(base, status=RecoveryStatus.STALE, reason=str(exc))
    except CheckpointSuperseded as exc:
        return replace(base, status=RecoveryStatus.SUPERSEDED, reason=str(exc))
    except CheckpointExhausted as exc:
        return replace(base, status=RecoveryStatus.EXHAUSTED, reason=str(exc))
    except CheckpointIntegrityError as exc:
        return replace(base, status=RecoveryStatus.CORRUPT, reason=str(exc))
    except CheckpointNotResumable as exc:
        return replace(base, status=RecoveryStatus.NEEDS_REVIEW, reason=str(exc))
    return replace(
        base,
        status=RecoveryStatus.RESUMABLE,
        reason="Safe execution boundary verified; binding is checked again on resume",
    )


def _corrupt_candidate(metadata: dict[str, Any], reason: str) -> RecoveryCandidate:
    return RecoveryCandidate(
        run_id=str(metadata.get("run_id") or ""),
        status=RecoveryStatus.CORRUPT,
        reason=reason,
        task=str(metadata.get("task") or ""),
        model_name=str(metadata.get("model_name") or ""),
        model_digest=str(metadata.get("model_digest") or ""),
        agent_title=str(metadata.get("agent_title") or ""),
        workspace=str(metadata.get("workspace") or ""),
        harness_mode=str(metadata.get("harness_mode") or ""),
        step=_safe_int(metadata.get("step")),
        step_budget=0,
        tool_calls=_safe_int(metadata.get("tool_calls")),
        updated_at=str(metadata.get("updated_at") or ""),
    )


def load_recovery_candidates(
    repository: Any,
    *,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_MAX_CHECKPOINT_AGE_SECONDS,
    limit: int = 100,
    reconcile: bool = True,
) -> list[RecoveryCandidate]:
    """Load, verify and classify durable checkpoints for an entry point.

    ``reconcile`` durably moves an interrupted ``tool_in_progress`` checkpoint to
    ``needs_review``. The classification is conservative even if that write
    fails: an interrupted tool is never offered as resumable.
    """
    candidates: list[RecoveryCandidate] = []
    for metadata in repository.list_run_checkpoints(limit):
        run_id = str(metadata.get("run_id") or "")
        try:
            checkpoint = repository.get_run_checkpoint(run_id)
        except CheckpointRejected as exc:
            candidates.append(_corrupt_candidate(metadata, str(exc)))
            continue
        except (OSError, ValueError) as exc:
            candidates.append(_corrupt_candidate(metadata, str(exc)))
            continue
        if checkpoint is None:
            continue
        if reconcile and checkpoint.get("state") == "tool_in_progress":
            try:
                repository.mark_run_checkpoint_needs_review(run_id)
                checkpoint = {**checkpoint, "state": "needs_review"}
            except (OSError, ValueError):
                checkpoint = {**checkpoint, "state": "needs_review"}
        candidate = classify_checkpoint(
            checkpoint, now=now, max_age_seconds=max_age_seconds
        )
        try:
            lease = repository.get_run_lease(run_id)
        except (AttributeError, OSError, ValueError):
            lease = None
        if lease:
            lease_active = bool(lease.get("active"))
            candidate = replace(
                candidate,
                lease_active=lease_active,
                lease_expires_at=str(lease.get("expires_at") or ""),
            )
            if lease_active and candidate.resumable:
                candidate = replace(
                    candidate,
                    status=RecoveryStatus.LEASED,
                    reason=(
                        "Another workstation process holds a live execution lease; "
                        "resume is blocked until it is released or expires."
                    ),
                )
        workspace_lease = None
        if candidate.workspace:
            try:
                workspace_lease = repository.get_workspace_lease(candidate.workspace)
            except (AttributeError, OSError, ValueError):
                workspace_lease = None
        if workspace_lease:
            workspace_active = bool(workspace_lease.get("active"))
            candidate = replace(
                candidate,
                workspace_active=workspace_active,
                workspace_expires_at=str(workspace_lease.get("expires_at") or ""),
                workspace_run_id=str(workspace_lease.get("run_id") or ""),
            )
            if workspace_active and candidate.status is RecoveryStatus.RESUMABLE:
                candidate = replace(
                    candidate,
                    status=RecoveryStatus.WORKSPACE_BUSY,
                    reason=(
                        "Another live run holds admission to this workspace; resume "
                        "is blocked until the workspace holder finishes or expires."
                    ),
                )
        candidates.append(candidate)
    return candidates


def checkpoint_json_preview(candidate: RecoveryCandidate) -> str:
    """Compact, log-safe one-line summary used by CLI output."""
    return json.dumps(candidate.to_dict(), ensure_ascii=False, sort_keys=True)
