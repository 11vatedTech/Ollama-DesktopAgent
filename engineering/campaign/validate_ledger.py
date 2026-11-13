"""Validate EXECUTION_LEDGER.json structure and reconcile it with Git."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

LEDGER = Path(__file__).with_name("EXECUTION_LEDGER.json")
REQUIRED = ("campaign", "baseline_commit", "branch", "current_commit", "gates",
            "completed", "active", "next_action", "evidence", "updated_at")
GATES = [f"G{number}" for number in range(1, 16)]
STATUSES = {"OPEN", "PARTIAL", "VERIFIED"}


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False,
                          cwd=LEDGER.parent).stdout.strip()


def main() -> int:
    ledger = json.loads(LEDGER.read_text(encoding="utf-8"))
    problems = [f"missing key: {key}" for key in REQUIRED if key not in ledger]
    gates = ledger.get("gates", {})
    problems += [f"missing gate: {gate}" for gate in GATES if gate not in gates]
    problems += [f"{gate}: invalid status {value.get('status')!r}"
                 for gate, value in gates.items() if value.get("status") not in STATUSES]
    problems += [f"{gate}: VERIFIED without evidence" for gate, value in gates.items()
                 if value.get("status") == "VERIFIED" and not value.get("evidence")]
    action = ledger.get("next_action", {})
    if not action.get("command") and not action.get("files"):
        problems.append("next_action must name a command or files")
    for key in ("baseline_commit", "current_commit"):
        commit = str(ledger.get(key, ""))
        if commit and _git("cat-file", "-t", commit) != "commit":
            problems.append(f"{key} {commit[:12]} is not a commit in this repository")
    head = _git("rev-parse", "HEAD")
    current = str(ledger.get("current_commit", ""))
    if head and current and subprocess.run(
            ["git", "merge-base", "--is-ancestor", current, head],
            cwd=LEDGER.parent, check=False).returncode != 0:
        problems.append("current_commit is not an ancestor of HEAD")
    if head and current and head != current:
        ahead = _git("rev-list", "--count", f"{current}..{head}")
        print(f"note: HEAD is {ahead} commit(s) ahead of the ledger's current_commit")
    for problem in problems:
        print(f"LEDGER ERROR: {problem}", file=sys.stderr)
    if not problems:
        print("ledger ok")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
