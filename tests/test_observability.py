from __future__ import annotations

import json
import logging
from pathlib import Path

from ollama_workstation.observability import configure_logging, log_event


def test_structured_logging_redacts_secret_values(tmp_path: Path) -> None:
    destination = configure_logging(tmp_path, level=logging.INFO)
    log_event(
        "workforce_event",
        "token=super-secret-value connected",
        run_id="run-1",
        data={"password": "also-secret", "nested": {"api_key": "hidden"}},
    )
    lines = destination.read_text(encoding="utf-8").splitlines()
    assert lines
    payload = json.loads(lines[-1])
    assert payload["event"] == "workforce_event"
    assert payload["run_id"] == "run-1"
    assert "super-secret-value" not in lines[-1]
    assert "also-secret" not in lines[-1]
    assert "hidden" not in lines[-1]
