"""Local host telemetry polling for the Command Center and System Lab."""

from __future__ import annotations

import os
import shutil
from threading import Event
from typing import Any

from PySide6.QtCore import QThread, Signal

from ollama_workstation.config import AppPaths
from ollama_workstation.profiling.telemetry import SystemTelemetry


class _CpuSampler:
    def __init__(self) -> None:
        self._last: tuple[int, int] | None = None
        self._windows: Any = None
        if os.name == "nt":
            try:
                import ctypes

                class FileTime(ctypes.Structure):
                    _fields_ = [
                        ("dwLowDateTime", ctypes.c_ulong),
                        ("dwHighDateTime", ctypes.c_ulong),
                    ]

                self._windows = (ctypes, FileTime)
            except (ImportError, AttributeError):
                self._windows = None

    def _windows_times(self) -> tuple[int, int, int] | None:
        if self._windows is None:
            return None
        ctypes, file_time = self._windows
        idle = file_time()
        kernel = file_time()
        user = file_time()
        try:
            if not ctypes.windll.kernel32.GetSystemTimes(
                ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
            ):
                return None
        except (AttributeError, OSError):
            return None

        def value(item: Any) -> int:
            return (int(item.dwHighDateTime) << 32) | int(item.dwLowDateTime)

        return value(idle), value(kernel), value(user)

    def _linux_times(self) -> tuple[int, int] | None:
        try:
            with open("/proc/stat", encoding="utf-8") as stream:
                fields = stream.readline().split()
            values = [int(item) for item in fields[1:]]
            if len(values) < 4:
                return None
            idle = values[3] + (values[4] if len(values) > 4 else 0)
            total = sum(values[:8])
            return idle, total
        except (OSError, ValueError):
            return None

    def sample(self) -> float | None:
        if os.name == "nt":
            times = self._windows_times()
            if times is None:
                return None
            idle, kernel, user = times
            total = kernel + user
            current = (idle, total)
            previous = self._last
            self._last = current
            if previous is None:
                return None
            idle_delta = current[0] - previous[0]
            total_delta = current[1] - previous[1]
            if total_delta <= 0:
                return None
            return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))
        times = self._linux_times()
        if times is None:
            return None
        current = times
        previous = self._last
        self._last = current
        if previous is None:
            return None
        idle_delta = current[0] - previous[0]
        total_delta = current[1] - previous[1]
        if total_delta <= 0:
            return None
        return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))


def collect_host_telemetry(paths: AppPaths, sampler: _CpuSampler | None = None) -> dict[str, Any]:
    """Collect one local snapshot; callers decide how often to poll it."""
    telemetry = SystemTelemetry()
    snapshot = telemetry.snapshot()
    if sampler is not None:
        snapshot["cpu_percent"] = sampler.sample()
    try:
        usage = shutil.disk_usage(paths.root)
        snapshot["disk"] = {
            "total_bytes": usage.total,
            "used_bytes": usage.used,
            "free_bytes": usage.free,
            "percent": round(usage.used / usage.total * 100.0, 1) if usage.total else None,
        }
    except OSError:
        snapshot["disk"] = {}
    return snapshot


class TelemetryPoller(QThread):
    sample_ready = Signal(dict)

    def __init__(
        self,
        paths: AppPaths,
        *,
        interval_seconds: float = 3.0,
        parent: object | None = None,
    ) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.paths = paths
        self.interval_seconds = max(1.0, interval_seconds)
        self._stop = Event()
        self._sampler = _CpuSampler()

    def run(self) -> None:
        # Publish the first available hardware sample immediately. The CPU delta
        # needs one priming read, but GPU, memory and disk are already valid and
        # must not remain blank until the first interval has elapsed.
        try:
            self.sample_ready.emit(collect_host_telemetry(self.paths, self._sampler))
        except Exception:
            pass
        while not self._stop.wait(self.interval_seconds):
            try:
                self.sample_ready.emit(collect_host_telemetry(self.paths, self._sampler))
            except Exception:
                continue

    def stop(self) -> None:
        self._stop.set()
        self.wait(4000)
