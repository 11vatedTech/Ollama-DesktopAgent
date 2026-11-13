"""Local process identity and conservative liveness checks for lease reclamation.

A PID alone is not a stable owner identity because operating systems reuse PIDs.
For local leases we record the host, PID and a process-start token ("pid plus
process-start identity"). A lease can be reclaimed early only when the host is
local and the process is provably dead or the PID is provably a different
process. Remote owners and ambiguous/access-denied cases remain protected by
the normal expiry TTL.
"""

from __future__ import annotations

import os
import socket
import sys
from dataclasses import dataclass
from pathlib import Path

WINDOWS_STILL_ACTIVE = 259
WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WINDOWS_ERROR_ACCESS_DENIED = 5


@dataclass(frozen=True, slots=True)
class ProcessIdentity:
    host: str
    pid: int
    start_token: str

    def as_lease_metadata(self) -> dict[str, str | int]:
        return {
            "owner_host": self.host,
            "owner_pid": self.pid,
            "owner_start": self.start_token,
        }


def local_host() -> str:
    return socket.gethostname().strip().casefold()


def current_process_identity() -> ProcessIdentity:
    pid = os.getpid()
    return ProcessIdentity(local_host(), pid, _start_token(pid))


def parse_owner_id(owner_id: str) -> tuple[str, int]:
    """Best-effort identity from the legacy ``host:pid:...`` owner format."""
    host, separator, remainder = str(owner_id).partition(":")
    if not separator:
        return "", 0
    pid_text = remainder.split(":", 1)[0]
    try:
        pid = int(pid_text)
    except ValueError:
        return "", 0
    return host.strip().casefold(), pid


def owner_process_alive(host: str, pid: int, start_token: str = "") -> bool | None:
    """Return True/False for provable local liveness, or None when unknown."""
    normalized_host = str(host or "").strip().casefold()
    if normalized_host and normalized_host != local_host():
        return None
    if pid <= 0:
        return None
    if os.name == "nt":
        alive = _windows_alive(pid)
    else:
        alive = _posix_alive(pid)
    if alive is not True:
        return alive
    expected = str(start_token or "").strip()
    if expected:
        actual = _start_token(pid)
        if actual and actual != expected:
            return False
    return True


def _posix_alive(pid: int) -> bool | None:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def _windows_kernel32():
    import ctypes

    return ctypes.WinDLL("kernel32", use_last_error=True)


def _windows_handle(pid: int):
    import ctypes

    kernel32 = _windows_kernel32()
    handle = kernel32.OpenProcess(
        WINDOWS_PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
    )
    if handle:
        return kernel32, handle
    if ctypes.get_last_error() == WINDOWS_ERROR_ACCESS_DENIED:
        return kernel32, None
    return kernel32, 0


def _windows_alive(pid: int) -> bool | None:
    import ctypes
    from ctypes import wintypes

    kernel32, handle = _windows_handle(pid)
    if handle is None:
        return None
    if not handle:
        return False
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return None
        return exit_code.value == WINDOWS_STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _start_token(pid: int) -> str:
    if pid <= 0:
        return ""
    if sys.platform.startswith("linux"):
        return _linux_start_token(pid)
    if os.name == "nt":
        return _windows_start_token(pid)
    return ""


def _linux_start_token(pid: int) -> str:
    try:
        content = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except OSError:
        return ""
    try:
        after_name = content.rsplit(")", 1)[1].strip().split()
        # Field 22 (starttime) is index 19 after field 3 (state).
        return str(int(after_name[19]))
    except (IndexError, ValueError):
        return ""


def _windows_start_token(pid: int) -> str:
    import ctypes
    from ctypes import wintypes

    kernel32, handle = _windows_handle(pid)
    if not handle or handle is None:
        return ""
    try:
        creation = wintypes.FILETIME()
        exited = wintypes.FILETIME()
        kernel = wintypes.FILETIME()
        user = wintypes.FILETIME()
        if not kernel32.GetProcessTimes(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return ""
        return str((creation.dwHighDateTime << 32) | creation.dwLowDateTime)
    finally:
        kernel32.CloseHandle(handle)
