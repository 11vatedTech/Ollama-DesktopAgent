"""OS-enforced containment for commands an agent runs.

On Windows every agent command runs inside a Job Object:

* the process is created suspended and assigned to the job *before* its first
  instruction executes, so no descendant can be spawned outside the job;
* ``KILL_ON_JOB_CLOSE`` terminates every remaining descendant when the command
  finishes, times out or is cancelled - background processes cannot outlive it;
* an active-process cap stops fork bombs, and a job-wide committed-memory cap
  stops a runaway allocation from exhausting the workstation;
* UI restrictions deny clipboard read/write, desktop switching, global atoms,
  display/system-parameter changes and logoff/shutdown.

What this is not: a filesystem or network sandbox. A contained process still
runs with the user's token and can read or write anything the user can, and can
open network connections. Path containment in ``WorkspaceTools`` governs the
agent's *tools*; it does not constrain code the agent chooses to execute. That
residual risk is why command execution stays off by default and every command
requires explicit approval.

On other platforms :func:`create_containment` returns ``None`` and callers fall
back to process-group termination.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
from ctypes import wintypes
from dataclasses import dataclass
from typing import Any

DEFAULT_MAX_PROCESSES = 32
DEFAULT_MAX_MEMORY_BYTES = 2 * 1024**3

CREATE_SUSPENDED = 0x00000004
CREATE_NEW_PROCESS_GROUP = 0x00000200

_JobObjectBasicUIRestrictions = 4
_JobObjectExtendedLimitInformation = 9

_JOB_OBJECT_LIMIT_ACTIVE_PROCESS = 0x00000008
_JOB_OBJECT_LIMIT_JOB_MEMORY = 0x00000200
_JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION = 0x00000400
_JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

_JOB_OBJECT_UILIMIT_HANDLES = 0x00000001
_JOB_OBJECT_UILIMIT_READCLIPBOARD = 0x00000002
_JOB_OBJECT_UILIMIT_WRITECLIPBOARD = 0x00000004
_JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS = 0x00000008
_JOB_OBJECT_UILIMIT_DISPLAYSETTINGS = 0x00000010
_JOB_OBJECT_UILIMIT_GLOBALATOMS = 0x00000020
_JOB_OBJECT_UILIMIT_DESKTOP = 0x00000040
_JOB_OBJECT_UILIMIT_EXITWINDOWS = 0x00000080

UI_RESTRICTIONS = (
    _JOB_OBJECT_UILIMIT_HANDLES | _JOB_OBJECT_UILIMIT_READCLIPBOARD
    | _JOB_OBJECT_UILIMIT_WRITECLIPBOARD | _JOB_OBJECT_UILIMIT_SYSTEMPARAMETERS
    | _JOB_OBJECT_UILIMIT_DISPLAYSETTINGS | _JOB_OBJECT_UILIMIT_GLOBALATOMS
    | _JOB_OBJECT_UILIMIT_DESKTOP | _JOB_OBJECT_UILIMIT_EXITWINDOWS
)


class ContainmentError(RuntimeError):
    """The OS refused to create or apply the containment boundary."""


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_ulonglong) for name in (
        "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
        "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class _BasicLimitInformation(ctypes.Structure):
    _fields_ = [
        ("PerProcessUserTimeLimit", ctypes.c_longlong),
        ("PerJobUserTimeLimit", ctypes.c_longlong),
        ("LimitFlags", wintypes.DWORD),
        ("MinimumWorkingSetSize", ctypes.c_size_t),
        ("MaximumWorkingSetSize", ctypes.c_size_t),
        ("ActiveProcessLimit", wintypes.DWORD),
        ("Affinity", ctypes.c_size_t),
        ("PriorityClass", wintypes.DWORD),
        ("SchedulingClass", wintypes.DWORD),
    ]


class _ExtendedLimitInformation(ctypes.Structure):
    _fields_ = [
        ("BasicLimitInformation", _BasicLimitInformation),
        ("IoInfo", _IoCounters),
        ("ProcessMemoryLimit", ctypes.c_size_t),
        ("JobMemoryLimit", ctypes.c_size_t),
        ("PeakProcessMemoryUsed", ctypes.c_size_t),
        ("PeakJobMemoryUsed", ctypes.c_size_t),
    ]


class _UiRestrictions(ctypes.Structure):
    _fields_ = [("UIRestrictionsClass", wintypes.DWORD)]


def _kernel32() -> Any:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (ctypes.c_void_p, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD)
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.TerminateJobObject.restype = wintypes.BOOL
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.QueryInformationJobObject.restype = wintypes.BOOL
    kernel32.QueryInformationJobObject.argtypes = (
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD))
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32


def _error(action: str) -> ContainmentError:
    code = ctypes.get_last_error()  # type: ignore[attr-defined,unused-ignore]
    return ContainmentError(f"{action} failed: {ctypes.FormatError(code)} ({code})")  # type: ignore[attr-defined,unused-ignore]


@dataclass(slots=True)
class JobLimits:
    max_processes: int = DEFAULT_MAX_PROCESSES
    max_memory_bytes: int = DEFAULT_MAX_MEMORY_BYTES


class WindowsJob:
    """A configured Job Object. Use as a context manager; closing it kills the tree."""

    def __init__(self, limits: JobLimits | None = None) -> None:
        self.limits = limits or JobLimits()
        self._kernel32 = _kernel32()
        handle = self._kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise _error("CreateJobObject")
        self._handle: int | None = handle
        try:
            self._configure()
        except BaseException:
            self.close()
            raise

    def _configure(self) -> None:
        info = _ExtendedLimitInformation()
        info.BasicLimitInformation.LimitFlags = (
            _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_DIE_ON_UNHANDLED_EXCEPTION
            | _JOB_OBJECT_LIMIT_ACTIVE_PROCESS | _JOB_OBJECT_LIMIT_JOB_MEMORY)
        info.BasicLimitInformation.ActiveProcessLimit = max(1, self.limits.max_processes)
        info.JobMemoryLimit = max(64 * 1024**2, self.limits.max_memory_bytes)
        if not self._kernel32.SetInformationJobObject(
                self._handle, _JobObjectExtendedLimitInformation,
                ctypes.byref(info), ctypes.sizeof(info)):
            raise _error("SetInformationJobObject(limits)")
        ui = _UiRestrictions(UI_RESTRICTIONS)
        if not self._kernel32.SetInformationJobObject(
                self._handle, _JobObjectBasicUIRestrictions, ctypes.byref(ui), ctypes.sizeof(ui)):
            raise _error("SetInformationJobObject(ui)")

    @staticmethod
    def creationflags() -> int:
        return CREATE_SUSPENDED | CREATE_NEW_PROCESS_GROUP

    def adopt(self, process: subprocess.Popen[Any]) -> None:
        """Assign a process started with :meth:`creationflags` and let it run.

        The process is still suspended, so it cannot have created children.
        If assignment fails the process is killed before it executes anything.
        """
        handle = int(process._handle)  # type: ignore[attr-defined,unused-ignore]  # noqa: SLF001
        if not self._kernel32.AssignProcessToJobObject(self._handle, handle):
            error = _error("AssignProcessToJobObject")
            process.kill()
            raise error
        ntdll = ctypes.WinDLL("ntdll")  # type: ignore[attr-defined,unused-ignore]
        ntdll.NtResumeProcess.argtypes = (wintypes.HANDLE,)
        ntdll.NtResumeProcess.restype = ctypes.c_long
        status = int(ntdll.NtResumeProcess(handle))
        if status != 0:
            process.kill()
            raise ContainmentError(f"NtResumeProcess failed: NTSTATUS {status & 0xFFFFFFFF:#x}")

    def adopt_handle(self, process_handle: int) -> None:
        """Assign a suspended process by raw handle; the caller resumes or kills it."""
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise _error("AssignProcessToJobObject")

    def terminate(self, exit_code: int = 1) -> None:
        if self._handle:
            self._kernel32.TerminateJobObject(self._handle, exit_code)

    def peak_memory_bytes(self) -> int:
        info = _ExtendedLimitInformation()
        if self._handle and self._kernel32.QueryInformationJobObject(
                self._handle, _JobObjectExtendedLimitInformation, ctypes.byref(info),
                ctypes.sizeof(info), None):
            return int(info.PeakJobMemoryUsed)
        return 0

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)  # KILL_ON_JOB_CLOSE ends the tree
            self._handle = None

    def __enter__(self) -> WindowsJob:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()


def containment_supported() -> bool:
    return os.name == "nt"


def create_containment(limits: JobLimits | None = None) -> WindowsJob | None:
    """A new Job Object on Windows; ``None`` where Job Objects do not exist."""
    if not containment_supported():
        return None
    return WindowsJob(limits)
