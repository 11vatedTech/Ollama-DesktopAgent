from __future__ import annotations

import json
import time
from threading import Event

import httpx
import pytest

from ollama_workstation.ollama.client import OllamaClient, OllamaError


class FakeStreamResponse:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self.events = events
        self.closed = False

    def __enter__(self) -> FakeStreamResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        self.closed = True

    def raise_for_status(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def iter_lines(self):
        for event in self.events:
            if self.closed:
                return
            yield json.dumps(event)

    @property
    def text(self) -> str:
        return ""


def test_streaming_chat_assembles_content_tool_calls_and_metrics(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeStreamResponse([
        {"message": {"role": "assistant", "content": "Inspect "}, "done": False},
        {
            "message": {
                "role": "assistant",
                "content": "the workspace",
                "tool_calls": [{
                    "function": {"name": "list_files", "arguments": {"path": "."}}
                }],
            },
            "done": False,
        },
        {
            "message": {"role": "assistant", "content": ""},
            "done": True,
            "eval_count": 18,
            "eval_duration": 900_000_000,
            "prompt_eval_count": 120,
        },
    ])
    captured: dict[str, object] = {}

    def fake_stream(method: str, url: str, **kwargs: object) -> FakeStreamResponse:
        captured.update({"method": method, "url": url, **kwargs})
        return response

    monkeypatch.setattr(httpx, "stream", fake_stream)
    result = OllamaClient().chat(
        "local:latest", [{"role": "user", "content": "Inspect"}],
        tools=[{"type": "function"}], cancel_event=Event(),
    )
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:11434/api/chat"
    assert captured["json"]["stream"] is True
    assert result["message"]["content"] == "Inspect the workspace"
    assert result["message"]["tool_calls"][0]["function"]["name"] == "list_files"
    assert result["eval_count"] == 18
    assert result["prompt_eval_count"] == 120


def test_streaming_chat_closes_response_when_cancelled(monkeypatch: pytest.MonkeyPatch) -> None:
    response = FakeStreamResponse([])
    cancelled = Event()

    def fake_stream(*_args: object, **_kwargs: object) -> FakeStreamResponse:
        def wait_until_closed():
            while not response.closed:
                time.sleep(0.01)
                yield ""
        response.iter_lines = wait_until_closed
        return response

    monkeypatch.setattr(httpx, "stream", fake_stream)
    client = OllamaClient(timeout=30)
    import threading

    trigger = threading.Thread(
        target=lambda: (time.sleep(0.15), cancelled.set()), daemon=True
    )
    trigger.start()
    with pytest.raises(OllamaError, match="cancelled"):
        client.chat("local:latest", [{"role": "user", "content": "Long task"}],
                    cancel_event=cancelled)
    trigger.join(timeout=1)
    assert response.closed
