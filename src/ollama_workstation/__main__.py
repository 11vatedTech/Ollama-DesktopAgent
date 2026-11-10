"""Launch the desktop workstation with python -m ollama_workstation."""

from __future__ import annotations

import sys


def main() -> int:
    try:
        from ollama_workstation.ui.main_window import run_desktop
    except ImportError as exc:
        print("Desktop dependencies are missing. Install with: pip install -e '.[desktop]'", file=sys.stderr)
        print(f"Details: {exc}", file=sys.stderr)
        return 2
    return run_desktop()


if __name__ == "__main__":
    raise SystemExit(main())
