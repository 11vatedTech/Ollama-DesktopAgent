"""Launch the desktop workstation with python -m ollama_workstation."""

from __future__ import annotations

import sys


def main() -> int:
    smoke_test = "--smoke-test" in sys.argv[1:]
    if smoke_test:
        import os
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        from ollama_workstation.ui.main_window import run_desktop
    except ImportError as exc:
        print("Desktop dependencies are missing. Install with: pip install -e '.[desktop]'", file=sys.stderr)
        print(f"Details: {exc}", file=sys.stderr)
        return 2
    return run_desktop(smoke_test=smoke_test)


if __name__ == "__main__":
    raise SystemExit(main())
