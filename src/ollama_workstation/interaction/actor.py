"""A dedicated worker thread for thread-affine services (Playwright, COM/UIA)."""

from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from typing import Any


class ActorTimeout(TimeoutError):
    """The operation did not finish in time; the service may still be busy."""


class Actor:
    """Runs every call on one thread, with a per-call timeout and cancellation."""

    def __init__(self, name: str, setup: Callable[[], Any] | None = None,
                 teardown: Callable[[Any], None] | None = None) -> None:
        self._calls: queue.Queue[tuple[Callable[[Any], Any], queue.Queue[Any]] | None] = queue.Queue()
        self._setup = setup
        self._teardown = teardown
        self._ready: queue.Queue[BaseException | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()
        error = self._ready.get()
        if error is not None:
            raise error

    def _run(self) -> None:
        state: Any = None
        try:
            state = self._setup() if self._setup else None
        except BaseException as exc:  # noqa: BLE001 - reported to the creator
            self._ready.put(exc)
            return
        self._ready.put(None)
        while True:
            item = self._calls.get()
            if item is None:
                break
            function, reply = item
            try:
                reply.put((True, function(state)))
            except BaseException as exc:  # noqa: BLE001 - returned to the caller
                reply.put((False, exc))
        if self._teardown is not None:
            try:
                self._teardown(state)
            except Exception:  # noqa: BLE001 - shutdown is best effort
                pass

    def call(self, function: Callable[[Any], Any], *, timeout: float = 60.0,
             cancel_event: threading.Event | None = None) -> Any:
        if not self._thread.is_alive():
            raise RuntimeError("The service has stopped")
        if cancel_event is not None and cancel_event.is_set():
            raise InterruptedError("Cancelled before the action started")
        reply: queue.Queue[Any] = queue.Queue()
        self._calls.put((function, reply))
        waited = 0.0
        while True:
            try:
                ok, value = reply.get(timeout=0.1)
                break
            except queue.Empty:
                waited += 0.1
                if cancel_event is not None and cancel_event.is_set():
                    raise InterruptedError("Cancelled while the action was running") from None
                if waited >= timeout:
                    raise ActorTimeout(f"The action did not finish within {timeout:.0f}s") from None
        if not ok:
            raise value
        return value

    def stop(self, timeout: float = 10.0) -> None:
        if self._thread.is_alive():
            self._calls.put(None)
            self._thread.join(timeout)
