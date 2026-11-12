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
from typing import Any


class LeaseError(RuntimeError):
    """Base class for execution-lease failures."""


class LeaseUnavailable(LeaseError):
    """Another live owner holds the run lease."""


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
    ) -> None:
        if not run_id.strip():
            raise ValueError("run_id must be non-empty")
        self.repository = repository
        self.run_id = run_id
        self.owner_id = owner_id or new_owner_id()
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
            self.run_id, self.owner_id, ttl_seconds=self.ttl_seconds
        ):
            raise LeaseUnavailable(
                f"Run {self.run_id} already has a live execution lease."
            )
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
                    self.run_id, self.owner_id, ttl_seconds=self.ttl_seconds
                )
            except Exception:
                self._lost.set()
                return
            if not renewed:
                self._lost.set()
                return

    def check(self) -> None:
        """Fail closed unless this process still owns a live lease."""
        if self._lost.is_set():
            raise LeaseLost("Execution lease was lost before the next operation.")
        try:
            renewed = self.repository.renew_run_lease(
                self.run_id, self.owner_id, ttl_seconds=self.ttl_seconds
            )
        except Exception as exc:
            self._lost.set()
            raise LeaseLost("Execution lease could not be renewed.") from exc
        if not renewed:
            self._lost.set()
            raise LeaseLost("Execution lease is no longer owned by this process.")

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=max(1.0, self.heartbeat_seconds + 1.0))
        try:
            with self._lock:
                self.repository.release_run_lease(self.run_id, self.owner_id)
        except Exception:
            # A failed release leaves an expiring row; recovery remains blocked
            # until the TTL, which is safer than hiding the release failure.
            pass
