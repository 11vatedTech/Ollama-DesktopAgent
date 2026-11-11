"""Deliberately invalid stdio server for protocol-failure certification."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

if len(sys.argv) != 2:
    raise SystemExit("usage: mcp_invalid_server.py PID_FILE")

Path(sys.argv[1]).write_text(str(os.getpid()), encoding="utf-8")
sys.stdout.write("this is not a JSON-RPC message\n")
sys.stdout.flush()
time.sleep(120)
