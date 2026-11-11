"""Windows stdio supervisor for host-owned MCP process-tree cleanup.

The SDK already attempts a Job Object for its direct child, but shutdown races
can leave grandchildren alive while the direct child exits. This wrapper is the
SDK's direct child: it owns a second kill-on-close Job Object containing the
real server. If the SDK kills the wrapper, the wrapper's job handle closes and
the server tree is reaped by Windows.

The wrapper is a transparent byte proxy. It writes nothing to stdout except the
server's own protocol output.

Usage (constructed by MCPServerSpec.client_target):
    python -m ollama_workstation.agents.mcp_stdio_wrapper -- COMMAND [ARG...]
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Any


def _create_job() -> Any | None:
    try:
        import win32job
    except ImportError:
        return None
    try:
        job = win32job.CreateJobObject(None, "")
        info = win32job.QueryInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation
        )
        info["BasicLimitInformation"]["LimitFlags"] |= (
            win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        )
        win32job.SetInformationJobObject(
            job, win32job.JobObjectExtendedLimitInformation, info
        )
        return job
    except Exception:
        return None


def _assign_to_job(job: Any, pid: int) -> None:
    try:
        import win32api
        import win32con
        import win32job

        handle = win32api.OpenProcess(
            win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
            False,
            pid,
        )
        if handle:
            try:
                win32job.AssignProcessToJobObject(job, handle)
            finally:
                win32api.CloseHandle(handle)
    except Exception:
        # The SDK's own job remains in place as the fallback.
        pass


def _close_job(job: Any | None) -> None:
    if job is None:
        return
    try:
        import win32api
        import win32job

        try:
            win32job.TerminateJobObject(job, 1)
        finally:
            win32api.CloseHandle(job)
    except Exception:
        pass


def _pump_stdin(child: subprocess.Popen[bytes]) -> None:
    assert child.stdin is not None
    try:
        while True:
            chunk = os.read(0, 65_536)
            if not chunk:
                break
            child.stdin.write(chunk)
            child.stdin.flush()
    except (BrokenPipeError, OSError):
        pass
    finally:
        try:
            child.stdin.close()
        except OSError:
            pass


def _pump_stdout(child: subprocess.Popen[bytes]) -> None:
    assert child.stdout is not None
    try:
        while True:
            chunk = os.read(child.stdout.fileno(), 65_536)
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            sys.stdout.buffer.flush()
    except (BrokenPipeError, OSError):
        pass


def main() -> int:
    if len(sys.argv) < 3 or sys.argv[1] != "--":
        print("mcp_stdio_wrapper: expected -- COMMAND [ARG...]", file=sys.stderr)
        return 2
    command = sys.argv[2:]
    job = _create_job()
    try:
        child = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=None,
            bufsize=0,
            env=os.environ.copy(),
        )
    except OSError as exc:
        _close_job(job)
        print(f"mcp_stdio_wrapper: failed to start server: {exc}", file=sys.stderr)
        return 127
    if job is not None:
        _assign_to_job(job, child.pid)
    threading.Thread(target=_pump_stdin, args=(child,), daemon=True).start()
    stdout_pump = threading.Thread(target=_pump_stdout, args=(child,), daemon=True)
    stdout_pump.start()
    try:
        code = int(child.wait())
        # Drain remaining protocol output before the wrapper exits.
        stdout_pump.join(timeout=2.0)
        return code
    finally:
        _close_job(job)


if __name__ == "__main__":
    raise SystemExit(main())
