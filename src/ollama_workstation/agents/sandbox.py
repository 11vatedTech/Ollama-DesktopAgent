"""AppContainer sandbox for commands an agent runs (Windows).

The Job Object in :mod:`containment` bounds a command's process tree and
resources, but the command still runs with the user's token. This module adds
the filesystem and network boundary: the command starts inside a Windows
AppContainer (the isolation used for UWP apps and browser renderers).

An AppContainer process runs with a lowbox token. Every securable object it
opens is checked against the user's rights *and* the container's SID, so:

* **Filesystem**: it can read and write only where an ACL names the container
  SID (or ALL APPLICATION PACKAGES, which covers the Windows system folders and
  Program Files read-only). The user's profile, documents, OneDrive, SSH keys,
  browser data and every other workspace are denied.
* **Network**: with no capabilities granted it has no internet, no private
  network and no loopback access, so it cannot exfiltrate data or reach local
  services such as Ollama.
* **Other objects**: named pipes, the registry outside its own hive, other
  processes' windows and most kernel objects are denied by the same check.

The host grants the container SID modify access to the agent's workspace and
read/execute access to the runtime folders of each allow-listed executable
(for a Python virtual environment: the environment and its base interpreter).
Grants are recorded so they can be listed and revoked (``11vated sandbox``).

The process is created suspended, assigned to the Job Object, then resumed,
so both boundaries apply before its first instruction.
"""

from __future__ import annotations

import ctypes
import json
import msvcrt
import os
import subprocess
import time
from collections.abc import Iterable
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path
from threading import Lock
from typing import IO, Any

PROFILE_NAME = "11vated.agent.sandbox"
PROFILE_DISPLAY = "11vated agent command sandbox"

_E_ALREADY_EXISTS = 0x800700B7
_EXTENDED_STARTUPINFO_PRESENT = 0x00080000
_CREATE_UNICODE_ENVIRONMENT = 0x00000400
_CREATE_SUSPENDED = 0x00000004
_CREATE_NO_WINDOW = 0x08000000
_CREATE_NEW_PROCESS_GROUP = 0x00000200
_STARTF_USESTDHANDLES = 0x00000100
_PROC_THREAD_ATTRIBUTE_HANDLE_LIST = 0x00020002
_PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES = 0x00020009
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 0x102
_STILL_ACTIVE = 259
_INFINITE = 0xFFFFFFFF


class SandboxError(RuntimeError):
    """The AppContainer could not be created, granted or entered."""


class _SecurityCapabilities(ctypes.Structure):
    _fields_ = [("AppContainerSid", ctypes.c_void_p), ("Capabilities", ctypes.c_void_p),
                ("CapabilityCount", wintypes.DWORD), ("Reserved", wintypes.DWORD)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD), ("lpReserved", wintypes.LPWSTR), ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR), ("dwX", wintypes.DWORD), ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD), ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD), ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD), ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.c_void_p), ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE), ("hStdError", wintypes.HANDLE),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [("StartupInfo", _StartupInfo), ("lpAttributeList", ctypes.c_void_p)]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [("hProcess", wintypes.HANDLE), ("hThread", wintypes.HANDLE),
                ("dwProcessId", wintypes.DWORD), ("dwThreadId", wintypes.DWORD)]


def sandbox_supported() -> bool:
    return os.name == "nt"


def _win_error(action: str) -> SandboxError:
    code = ctypes.get_last_error()  # type: ignore[attr-defined,unused-ignore]
    return SandboxError(f"{action} failed: {ctypes.FormatError(code).strip()} ({code})")  # type: ignore[attr-defined,unused-ignore]


def _apis() -> dict[str, Any]:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    userenv = ctypes.WinDLL("userenv", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined,unused-ignore]
    k = kernel32
    k.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    k.InitializeProcThreadAttributeList.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t))
    k.UpdateProcThreadAttribute.restype = wintypes.BOOL
    k.UpdateProcThreadAttribute.argtypes = (
        ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t,
        ctypes.c_void_p, ctypes.c_void_p)
    k.DeleteProcThreadAttributeList.restype = None
    k.DeleteProcThreadAttributeList.argtypes = (ctypes.c_void_p,)
    k.CreateProcessW.restype = wintypes.BOOL
    k.CreateProcessW.argtypes = (
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wintypes.BOOL,
        wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR, ctypes.c_void_p,
        ctypes.POINTER(_ProcessInformation))
    k.ResumeThread.restype = wintypes.DWORD
    k.ResumeThread.argtypes = (wintypes.HANDLE,)
    k.WaitForSingleObject.restype = wintypes.DWORD
    k.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
    k.GetExitCodeProcess.restype = wintypes.BOOL
    k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k.TerminateProcess.restype = wintypes.BOOL
    k.TerminateProcess.argtypes = (wintypes.HANDLE, wintypes.UINT)
    k.CloseHandle.restype = wintypes.BOOL
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    k.LocalFree.restype = ctypes.c_void_p
    k.LocalFree.argtypes = (ctypes.c_void_p,)
    userenv.CreateAppContainerProfile.restype = ctypes.c_long
    userenv.CreateAppContainerProfile.argtypes = (
        wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p))
    userenv.DeriveAppContainerSidFromAppContainerName.restype = ctypes.c_long
    userenv.DeriveAppContainerSidFromAppContainerName.argtypes = (
        wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_void_p))
    userenv.GetAppContainerFolderPath.restype = ctypes.c_long
    userenv.GetAppContainerFolderPath.argtypes = (wintypes.LPCWSTR, ctypes.POINTER(wintypes.LPWSTR))
    advapi32.ConvertSidToStringSidW.restype = wintypes.BOOL
    advapi32.ConvertSidToStringSidW.argtypes = (ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR))
    advapi32.FreeSid.restype = ctypes.c_void_p
    advapi32.FreeSid.argtypes = (ctypes.c_void_p,)
    ole32 = ctypes.WinDLL("ole32")  # type: ignore[attr-defined,unused-ignore]
    ole32.CoTaskMemFree.restype = None
    ole32.CoTaskMemFree.argtypes = (ctypes.c_void_p,)
    return {"kernel32": k, "userenv": userenv, "advapi32": advapi32, "ole32": ole32}


@dataclass(frozen=True, slots=True)
class Grant:
    path: str
    access: str  # "modify" or "read"

    def to_dict(self) -> dict[str, str]:
        return {"path": self.path, "access": self.access}


class AppContainerProfile:
    """The agent sandbox's AppContainer profile, created once per user."""

    _lock = Lock()
    _cached: AppContainerProfile | None = None

    def __init__(self, name: str = PROFILE_NAME) -> None:
        if not sandbox_supported():
            raise SandboxError("AppContainer sandboxing requires Windows")
        self.name = name
        self._api = _apis()
        sid = ctypes.c_void_p()
        result = self._api["userenv"].CreateAppContainerProfile(
            name, PROFILE_DISPLAY, PROFILE_DISPLAY, None, 0, ctypes.byref(sid))
        if result & 0xFFFFFFFF == _E_ALREADY_EXISTS:
            result = self._api["userenv"].DeriveAppContainerSidFromAppContainerName(
                name, ctypes.byref(sid))
        if result != 0 or not sid.value:
            raise SandboxError(f"Creating the AppContainer profile failed (HRESULT {result & 0xFFFFFFFF:#x})")
        self._sid = sid
        text = wintypes.LPWSTR()
        if not self._api["advapi32"].ConvertSidToStringSidW(sid, ctypes.byref(text)):
            raise _win_error("ConvertSidToStringSid")
        self.sid_string = str(text.value)
        self._api["kernel32"].LocalFree(text)
        folder = wintypes.LPWSTR()
        if self._api["userenv"].GetAppContainerFolderPath(self.sid_string, ctypes.byref(folder)) != 0:
            raise SandboxError("Could not locate the AppContainer storage folder")
        self.folder = Path(str(folder.value))
        self._api["ole32"].CoTaskMemFree(folder)
        self.temp_dir = self.folder / "Temp"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def shared(cls) -> AppContainerProfile:
        with cls._lock:
            if cls._cached is None:
                cls._cached = cls()
            return cls._cached

    @property
    def sid_pointer(self) -> ctypes.c_void_p:
        return self._sid

    # -- ACL grants ---------------------------------------------------------------
    @staticmethod
    def _icacls() -> str:
        return str(Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "icacls.exe")

    def has_grant(self, path: Path) -> bool:
        completed = subprocess.run([self._icacls(), str(path)], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", check=False, timeout=60,
                                   creationflags=_CREATE_NO_WINDOW)
        return completed.returncode == 0 and self.sid_string.lower() in completed.stdout.lower()

    def grant(self, path: Path, access: str) -> bool:
        """Give the container SID inheritable access to ``path``; ``True`` if newly granted."""
        path = path.resolve()
        if not path.exists():
            raise SandboxError(f"Cannot grant sandbox access to a missing path: {path}")
        if self.has_grant(path):
            return False
        rights = {"modify": "M", "read": "RX"}[access]
        spec = f"*{self.sid_string}:(OI)(CI)({rights})" if path.is_dir() else f"*{self.sid_string}:({rights})"
        completed = subprocess.run([self._icacls(), str(path), "/grant", spec, "/Q", "/C"],
                                   capture_output=True, text=True, encoding="utf-8",
                                   errors="replace", check=False, timeout=600,
                                   creationflags=_CREATE_NO_WINDOW)
        if completed.returncode != 0:
            raise SandboxError(f"Granting sandbox access to {path} failed: "
                               f"{(completed.stdout + completed.stderr).strip()[:300]}")
        GrantLedger.default().record(Grant(str(path), access), self.sid_string)
        return True

    def revoke(self, path: Path) -> bool:
        completed = subprocess.run([self._icacls(), str(path), "/remove:g", f"*{self.sid_string}",
                                    "/T", "/Q", "/C"], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", check=False, timeout=600,
                                   creationflags=_CREATE_NO_WINDOW)
        GrantLedger.default().forget(str(path))
        return completed.returncode == 0


class GrantLedger:
    """Paths the sandbox SID was granted, so an operator can list and revoke them."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = Lock()

    @classmethod
    def default(cls) -> GrantLedger:
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "11vated-sandbox"
        return cls(base / "grants.json")

    def entries(self) -> list[dict[str, str]]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        return [item for item in data.get("grants", []) if isinstance(item, dict)]

    def _write(self, entries: list[dict[str, str]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"grants": entries}, indent=2), encoding="utf-8")
        os.replace(temporary, self.path)

    def record(self, grant: Grant, sid: str) -> None:
        with self._lock:
            # Folders that no longer exist carry no ACL; drop them as we go.
            entries = [item for item in self.entries()
                       if item.get("path") != grant.path and Path(str(item.get("path"))).exists()]
            entries.append({**grant.to_dict(), "sid": sid,
                            "granted_at": time.strftime("%Y-%m-%dT%H:%M:%S")})
            self._write(entries)

    def forget(self, path: str) -> None:
        with self._lock:
            self._write([item for item in self.entries() if item.get("path") != path])


def runtime_read_roots(executable: Path) -> list[Path]:
    """Folders an executable needs to read to run: its own folder and, for a Python
    virtual environment, the environment root and its base interpreter."""
    executable = executable.resolve()
    roots = [executable.parent]
    for candidate in (executable.parent, executable.parent.parent):
        config = candidate / "pyvenv.cfg"
        if config.is_file():
            roots.append(candidate)
            for line in config.read_text(encoding="utf-8", errors="replace").splitlines():
                key, _, value = line.partition("=")
                if key.strip().lower() == "home" and value.strip():
                    home = Path(value.strip())
                    roots.append(home)
                    # uv/py layouts keep Lib next to python.exe; POSIX-style under bin/.
                    if home.name.lower() in {"bin", "scripts"}:
                        roots.append(home.parent)
            break
    unique: list[Path] = []
    for root in roots:
        if root.exists() and not _is_system_readable(root) and root not in unique:
            unique.append(root)
    return unique


def _is_system_readable(path: Path) -> bool:
    """Windows and Program Files already grant ALL APPLICATION PACKAGES read access."""
    lowered = str(path).lower()
    prefixes = [os.environ.get(name, "") for name in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)")]
    return any(prefix and lowered.startswith(prefix.lower()) for prefix in prefixes)


class SandboxedProcess:
    """A process running in the AppContainer, with the subset of ``Popen`` the
    command supervisor uses (pid, stdout/stderr, poll, wait, kill, returncode)."""

    def __init__(self, process_handle: int, pid: int, stdout: IO[str], stderr: IO[str],
                 kernel32: Any) -> None:
        self._handle: int | None = process_handle
        self.pid = pid
        self.stdout = stdout
        self.stderr = stderr
        self.returncode: int | None = None
        self._kernel32 = kernel32

    def poll(self) -> int | None:
        if self.returncode is None and self._handle:
            if self._kernel32.WaitForSingleObject(self._handle, 0) == _WAIT_OBJECT_0:
                code = wintypes.DWORD()
                self._kernel32.GetExitCodeProcess(self._handle, ctypes.byref(code))
                self.returncode = int(code.value)
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is not None:
            return self.returncode
        milliseconds = _INFINITE if timeout is None else max(0, int(timeout * 1000))
        if self._handle and self._kernel32.WaitForSingleObject(self._handle, milliseconds) == _WAIT_TIMEOUT:
            raise subprocess.TimeoutExpired("sandboxed command", timeout or 0)
        result = self.poll()
        return -1 if result is None else result

    def kill(self) -> None:
        if self._handle and self.poll() is None:
            self._kernel32.TerminateProcess(self._handle, 1)

    terminate = kill

    def close(self) -> None:
        if self._handle:
            self._kernel32.CloseHandle(self._handle)
            self._handle = None


def _environment_block(environment: dict[str, str]) -> ctypes.Array[ctypes.c_wchar]:
    entries = "".join(f"{key}={value}\0" for key, value in
                      sorted(environment.items(), key=lambda item: item[0].upper()))
    return ctypes.create_unicode_buffer(entries + "\0")


def launch(argv: list[str], *, cwd: Path, environment: dict[str, str], job: Any,
           profile: AppContainerProfile | None = None) -> SandboxedProcess:
    """Start ``argv`` in the AppContainer, inside ``job`` (a containment.WindowsJob).

    The child inherits exactly three handles (null stdin and the two output
    pipes). It is assigned to the job while suspended, then resumed.
    """
    profile = profile or AppContainerProfile.shared()
    api = profile._api  # noqa: SLF001
    kernel32 = api["kernel32"]
    environment = {**environment, "TEMP": str(profile.temp_dir), "TMP": str(profile.temp_dir),
                   "LOCALAPPDATA": str(profile.folder)}
    environment.pop("USERPROFILE", None)

    out_read, out_write = os.pipe()
    err_read, err_write = os.pipe()
    null_fd = os.open(os.devnull, os.O_RDONLY)
    child_fds = (null_fd, out_write, err_write)
    child_handles = [msvcrt.get_osfhandle(fd) for fd in child_fds]
    for handle in child_handles:
        os.set_handle_inheritable(handle, True)

    attribute_size = ctypes.c_size_t()
    kernel32.InitializeProcThreadAttributeList(None, 2, 0, ctypes.byref(attribute_size))
    attribute_buffer = ctypes.create_string_buffer(attribute_size.value)
    attributes = ctypes.cast(attribute_buffer, ctypes.c_void_p)
    process_info = _ProcessInformation()
    started = False
    try:
        if not kernel32.InitializeProcThreadAttributeList(attributes, 2, 0, ctypes.byref(attribute_size)):
            raise _win_error("InitializeProcThreadAttributeList")
        try:
            capabilities = _SecurityCapabilities(profile.sid_pointer.value, None, 0, 0)
            if not kernel32.UpdateProcThreadAttribute(
                    attributes, 0, _PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES,
                    ctypes.byref(capabilities), ctypes.sizeof(capabilities), None, None):
                raise _win_error("UpdateProcThreadAttribute(security capabilities)")
            handle_array = (wintypes.HANDLE * 3)(*child_handles)
            if not kernel32.UpdateProcThreadAttribute(
                    attributes, 0, _PROC_THREAD_ATTRIBUTE_HANDLE_LIST, handle_array,
                    ctypes.sizeof(handle_array), None, None):
                raise _win_error("UpdateProcThreadAttribute(handle list)")
            startup = _StartupInfoEx()
            startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
            startup.StartupInfo.dwFlags = _STARTF_USESTDHANDLES
            startup.StartupInfo.hStdInput = child_handles[0]
            startup.StartupInfo.hStdOutput = child_handles[1]
            startup.StartupInfo.hStdError = child_handles[2]
            startup.lpAttributeList = attributes
            command_line = ctypes.create_unicode_buffer(subprocess.list2cmdline(argv))
            block = _environment_block(environment)
            flags = (_EXTENDED_STARTUPINFO_PRESENT | _CREATE_UNICODE_ENVIRONMENT | _CREATE_SUSPENDED
                     | _CREATE_NO_WINDOW | _CREATE_NEW_PROCESS_GROUP)
            if not kernel32.CreateProcessW(argv[0], command_line, None, None, True, flags,
                                           ctypes.cast(block, ctypes.c_void_p), str(cwd),
                                           ctypes.byref(startup), ctypes.byref(process_info)):
                raise _win_error("CreateProcess in AppContainer")
            started = True
        finally:
            kernel32.DeleteProcThreadAttributeList(attributes)
    finally:
        for fd in child_fds:
            os.close(fd)
        if not started:
            os.close(out_read)
            os.close(err_read)

    try:
        job.adopt_handle(int(process_info.hProcess))
        if kernel32.ResumeThread(process_info.hThread) == 0xFFFFFFFF:
            raise _win_error("ResumeThread")
    except BaseException:
        kernel32.TerminateProcess(process_info.hProcess, 1)
        kernel32.CloseHandle(process_info.hThread)
        kernel32.CloseHandle(process_info.hProcess)
        os.close(out_read)
        os.close(err_read)
        raise
    kernel32.CloseHandle(process_info.hThread)
    stdout = open(out_read, encoding="utf-8", errors="replace", closefd=True)  # noqa: SIM115
    stderr = open(err_read, encoding="utf-8", errors="replace", closefd=True)  # noqa: SIM115
    return SandboxedProcess(int(process_info.hProcess), int(process_info.dwProcessId),
                            stdout, stderr, kernel32)


def prepare(workspace: Path, executables: Iterable[Path], extra_read_roots: Iterable[Path] = (),
            profile: AppContainerProfile | None = None) -> AppContainerProfile:
    """Create the profile and grant the workspace and runtime roots (idempotent)."""
    profile = profile or AppContainerProfile.shared()
    profile.grant(workspace, "modify")
    for executable in executables:
        for root in runtime_read_roots(executable):
            profile.grant(root, "read")
    for root in extra_read_roots:
        profile.grant(Path(root), "read")
    return profile
