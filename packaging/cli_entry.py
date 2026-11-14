"""Frozen entry point for the 11vated console executable."""

from __future__ import annotations

import sys

from ollama_workstation.cli import main

if __name__ == "__main__":
    if len(sys.argv) > 2 and sys.argv[1] == "--recovery-child":
        # Child process for acceptance scenario E (see agentification/_recovery_child.py).
        from ollama_workstation.agentification._recovery_child import main as child_main
        raise SystemExit(child_main(sys.argv[2:]))
    if len(sys.argv) > 1 and sys.argv[1] == "--practice-app":
        # Practice application for computer-use qualification and acceptance.
        from ollama_workstation.interaction.practice_app import main as practice_main
        raise SystemExit(practice_main())
    raise SystemExit(main())
