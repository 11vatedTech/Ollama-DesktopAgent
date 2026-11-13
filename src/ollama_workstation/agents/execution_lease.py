"""Runtime-owned execution leases.

A run may have at most one live writer. The repository provides the atomic
compare-and-set; this module keeps that lease alive while the agent is running
and makes loss of ownership an explicit, fail-closed condition. A lease is not a
security boundary by itself, but it prevents two processes from resuming or
executing the same durable run against the same workspace concurrently.
"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from pathlib import Path
from typing import Any

from ollama_workstation.agents.process_identity import current_process_identity


class LeaseError(RuntimeError):
    """Base class for execution-lease failures."""


class LeaseUnavailable(LeaseError):
    """Another live owner holds the run lease."""


class WorkspaceBusy(LeaseError):
    """Another live run already owns admission to this workspace."""

    def __init__(
        self, workspace: str, run_id: str, *,
        owner_id: str = "", expires_at: str = "",
    ) -> None:
        self.workspace = workspace
        self.run_id = run_id
        self.owner_id = owner_id
        self.expires_at = expires_at
        message = f"Workspace {workspace!r} already has a live execution admission."
        if expires_at:
            message += f" It expires at {expires_at}."
        super().__init__(message)


class LeaseLost(LeaseError):
    """This process no longer owns the lease and must stop mutating state."""


def new_owner_id(prefix: str = "agent") -> str:
    """Return a host/process-scoped owner identity suitable for the lease row."""
    return f"{socket.gethostname()}:{os.getpid()}:{prefix}:{uuid.uuid4().hex}"


class ExecutionLease:
    """Acquire, heartbeat and release one durable run lease.

    The heartbeat is deliberately conservative. If renewal fails because the
    lease expired or another owner claimed it, ``check()`` raises before the
    runtime performs another provider request, checkpoint or tool operation.
    A transient database error is treated as loss as well: refusing to continue
    is safer than risking concurrent writes.
    """

    def __init__(
        self,
        repository: Any,
        run_id: str,
        owner_id: str | None = None,
        *,
        ttl_seconds: int = 300,
        heartbeat_seconds: float | None = None,
        workspace: str | Path | None = None,
    ) -> None:
        if not run_id.strip():
            raise ValueError("run_id must be non-empty")
        self.repository = repository
        self.run_id = run_id
        self.owner_id = owner_id or new_owner_id()
        self.workspace = (
            str(Path(workspace).expanduser().resolve()) if workspace is not None else None
        )
        self.identity = current_process_identity()
        self._workspace_held = False
        self.ttl_seconds = max(5, min(int(ttl_seconds), 3_600))
        self.heartbeat_seconds = max(
            1.0, float(heartbeat_seconds or self.ttl_seconds / 3)
        )
        self._lost = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def __enter__(self) -> ExecutionLease:
        if not self.repository.acquire_run_lease(
            self.run_id, self.owner_id, ttl_seconds=self.ttl_seconds,
            **self.identity.as_lease_metadata(),
        ):
            raise LeaseUnavailable(
                f"Run {self.run_id} already has a live execution lease."
            )
        if self.workspace is not None:
            try:
                held = self.repository.acquire_workspace_lease(
                    self.workspace, self.run_id, self.owner_id,
                    ttl_seconds=self.ttl_seconds,
                    **self.identity.as_lease_metadata(),
                )
            except Exception:
                try:
                    self.repository.release_run_lease(self.run_id, self.owner_id)
                except Exception:
                    pass
                raise
            if not held:
                try:
                    self.repository.release_run_lease(self.run_id, self.owner_id)
                except Exception:
                    pass
                row = self.repository.get_workspace_lease(self.workspace) or {}
                raise WorkspaceBusy(
                    self.workspace, self.run_id,
                    owner_id=str(row.get("owner_id") or ""),
                    expires_at=str(row.get("expires_at") or ""),
                )
            self._workspace_held = True
        self._thread = threading.Thread(
            target=self._heartbeat,
            name=f"lease-{self.run_id[:8]}",
            daemon=True,
        )
        self._thread.start()
        return self

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            try:
                renewed = self.repository.renew_run_lease(
                    self.run_id, self.owner_id, ttl_seconds=self.ttl_seconds,
                    **self.identity.as_lease_metadata(),
                )
                workspace_renewed = (
                    self.workspace is None
                    or self.repository.renew_workspace_lease(
                        self.workspace, self.owner_id, ttl_seconds=self.ttl_seconds,
                        **self.identity.as_lease_metadata(),
                    )
                )
            except Exception:
                self._lost.set()
                return
            if not renewed or not workspace_renewed:
                self._lost.set()
                return

    def check(self) -> None:
        """Fail closed unless this process still owns a live lease."""
        if self._lost.is_set():
            raise LeaseLost("Execution lease was lost before the next operation.")
        try:
            renewed = self.repository.renew_run_lease(
                self.run_id, self.owner_id, ttl_seconds=self.ttl_seconds,
                **self.identity.as_lease_metadata(),
            )
            workspace_renewed = (
                self.workspace is None
                or self.repository.renew_workspace_lease(
                    self.workspace, self.owner_id, ttl_seconds=self.ttl_seconds,
                    **self.identity.as_lease_metadata(),
                )
            )
        except Exception as exc:
            self._lost.set()
            raise LeaseLost("Execution lease could not be renewed.") from exc
        if not renewed:
            self._lost.set()
            raise LeaseLost("Execution lease is no longer owned by this process.")
        if not workspace_renewed:
            self._lost.set()
            raise LeaseLost("Workspace admission is no longer owned by this process.")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.heartbeat_seconds + 1.0))
        with self._lock:
            if self._workspace_held and self.workspace is not None:
                try:
                    self.repository.release_workspace_lease(
                        self.workspace, self.owner_id
                    )
                except Exception:
                    # The workspace row will expire; always attempt the run
                    # release below rather than leaving both rows behind.
                    pass
            try:
                self.repository.release_run_lease(self.run_id, self.owner_id)
            except Exception:
                # A failed release leaves an expiring row; recovery remains
                # blocked until the TTL, which is safer than hiding failure.
                pass


class WorkspaceAdmission:
    """Hold the workspace-level admission for a top-level orchestration run.

    A workforce parent takes this admission and passes ``admit_workspace=False``
    to child agent runtimes, so children share one admitted workspace instead of
    competing with their parent. A standalone runtime uses an ``ExecutionLease``
    with ``workspace=...`` and therefore takes both the run and workspace rows.
    """

    def __init__(
        self,
        repository: Any,
        workspace: str | Path,
        run_id: str,
        owner_id: str | None = None,
        *,
        ttl_seconds: int = 900,
        heartbeat_seconds: float | None = None,
    ) -> None:
        if not run_id.strip():
            raise ValueError("run_id must be non-empty")
        self.repository = repository
        self.workspace = str(Path(workspace).expanduser().resolve())
        self.run_id = run_id
        self.owner_id = owner_id or new_owner_id("workspace")
        self.identity = current_process_identity()
        self.ttl_seconds = max(5, min(int(ttl_seconds), 3_600))
        self.heartbeat_seconds = max(
            1.0, float(heartbeat_seconds or self.ttl_seconds / 3)
        )
        self._lost = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def __enter__(self) -> WorkspaceAdmission:
        if not self.repository.acquire_workspace_lease(
            self.workspace, self.run_id, self.owner_id,
            ttl_seconds=self.ttl_seconds,
            **self.identity.as_lease_metadata(),
        ):
            row = self.repository.get_workspace_lease(self.workspace) or {}
            raise WorkspaceBusy(
                self.workspace, self.run_id,
                owner_id=str(row.get("owner_id") or ""),
                expires_at=str(row.get("expires_at") or ""),
            )
        self._thread = threading.Thread(
            target=self._heartbeat,
            name=f"workspace-lease-{self.run_id[:8]}",
            daemon=True,
        )
        self._thread.start()
        return self

    def _heartbeat(self) -> None:
        while not self._stop.wait(self.heartbeat_seconds):
            try:
                renewed = self.repository.renew_workspace_lease(
                    self.workspace, self.owner_id, ttl_seconds=self.ttl_seconds,
                    **self.identity.as_lease_metadata(),
                )
            except Exception:
                self._lost.set()
                return
            if not renewed:
                self._lost.set()
                return

    def check(self) -> None:
        if self._lost.is_set():
            raise LeaseLost("Workspace admission was lost before the next operation.")
        try:
            renewed = self.repository.renew_workspace_lease(
                self.workspace, self.owner_id, ttl_seconds=self.ttl_seconds,
                **self.identity.as_lease_metadata(),
            )
        except Exception as exc:
            self._lost.set()
            raise LeaseLost("Workspace admission could not be renewed.") from exc
        if not renewed:
            self._lost.set()
            raise LeaseLost("Workspace admission is no longer owned by this process.")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.heartbeat_seconds + 1.0))
        try:
            self.repository.release_workspace_lease(self.workspace, self.owner_id)
        except Exception:
            pass
