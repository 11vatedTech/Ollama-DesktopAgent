"""Small QRunnable adapter for keeping blocking operations off the Qt UI thread."""
from __future__ import annotations

import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QRunnable, Signal


class WorkerSignals(QObject):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(str)
    finished = Signal()


class FunctionWorker(QRunnable):
    def __init__(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> None:
        super().__init__()
        self.function = function
        self.args = args
        self.kwargs = kwargs
        self.signals = WorkerSignals()

    def run(self) -> None:
        def safe_emit(signal: Signal, value: object) -> None:
            try:
                signal.emit(value)
            except RuntimeError:
                # The owning window may have been destroyed while a blocking
                # provider or filesystem operation was still finishing.
                pass

        try:
            result = self.function(*self.args, **self.kwargs)
            safe_emit(self.signals.result, result)
        except Exception as exc:
            safe_emit(self.signals.error, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
        finally:
            try:
                self.signals.finished.emit()
            except RuntimeError:
                pass
