"""Dependency-free host and NVIDIA telemetry for local model profiling."""

from __future__ import annotations

import csv
import io
import os
import platform
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any


class SystemTelemetry:
    """Collect a best-effort local hardware snapshot without network access."""

    @staticmethod
    def _memory() -> dict[str, int | None]:
        if os.name == "nt":
            try:
                import ctypes

                class MemoryStatus(ctypes.Structure):
                    _fields_ = [
                        ("dwLength", ctypes.c_ulong),
                        ("dwMemoryLoad", ctypes.c_ulong),
                        ("ullTotalPhys", ctypes.c_ulonglong),
                        ("ullAvailPhys", ctypes.c_ulonglong),
                        ("ullTotalPageFile", ctypes.c_ulonglong),
                        ("ullAvailPageFile", ctypes.c_ulonglong),
                        ("ullTotalVirtual", ctypes.c_ulonglong),
                        ("ullAvailVirtual", ctypes.c_ulonglong),
                        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                    ]

                status = MemoryStatus()
                status.dwLength = ctypes.sizeof(MemoryStatus)
                if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                    return {
                        "total_bytes": int(status.ullTotalPhys),
                        "available_bytes": int(status.ullAvailPhys),
                        "used_bytes": int(status.ullTotalPhys - status.ullAvailPhys),
                    }
            except (AttributeError, OSError, ValueError):
                pass
        meminfo = "/proc/meminfo"
        try:
            values: dict[str, int] = {}
            with open(meminfo, encoding="utf-8") as stream:
                for line in stream:
                    key, raw = line.split(":", 1)
                    if key in {"MemTotal", "MemAvailable"}:
                        values[key] = int(raw.strip().split()[0]) * 1024
            total = values.get("MemTotal")
            available = values.get("MemAvailable")
            if total is not None and available is not None:
                return {"total_bytes": total, "available_bytes": available,
                        "used_bytes": total - available}
        except (OSError, ValueError):
            pass
        return {"total_bytes": None, "available_bytes": None, "used_bytes": None}

    @staticmethod
    def _optional_int(value: str) -> int | None:
        """Parse one NVIDIA telemetry value without invalidating the remaining fields."""
        try:
            return int(value.strip())
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _nvidia_gpus() -> list[dict[str, Any]]:
        executable = shutil.which("nvidia-smi")
        if not executable:
            return []
        command = [
            executable,
            "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
            "--format=csv,noheader,nounits",
        ]
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, timeout=3,
                check=False, shell=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return []
        if result.returncode != 0:
            return []
        gpus: list[dict[str, Any]] = []
        for row in csv.reader(io.StringIO(result.stdout)):
            if len(row) != 6:
                continue
            numeric = [SystemTelemetry._optional_int(value) for value in row[1:]]
            if not any(value is not None for value in numeric):
                continue
            gpus.append({
                "name": row[0].strip(),
                "memory_total_mib": numeric[0],
                "memory_used_mib": numeric[1],
                "memory_free_mib": numeric[2],
                "utilization_percent": numeric[3],
                "temperature_c": numeric[4],
                "source": "nvidia-smi",
            })
        return gpus

    def snapshot(self) -> dict[str, Any]:
        return {
            "captured_at": datetime.now(UTC).isoformat(),
            "os": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
            "logical_cpu_count": os.cpu_count(),
            "system_memory": self._memory(),
            "nvidia_gpus": self._nvidia_gpus(),
        }
