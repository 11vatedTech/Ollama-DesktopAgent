"""Every request names its context window; compaction keeps the contract inside it.

Live probe (2026-11-16, Ollama 0.40.16, qwen3.5:9b): with no num_ctx a 6.7k-token message was
dropped silently and only 42 prompt tokens were evaluated, so a long agent run lost its task.
"""

from __future__ import annotations

import json
from typing import Any

from ollama_workstation.agents.runtime import (
    AgentRuntime,
    RunPolicy,
    compact_messages,
    conversation_char_budget,
    working_context,
)
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.models.schemas import ModelProfile


def test_working_context_prefers_explicit_and_respects_model_limit() -> None:
    assert working_context(None, 16384, None) == 16384
    assert working_context({"num_ctx": 32768}, 16384, None) == 32768
    assert working_context({"num_ctx": 32768}, 16384, 8192) == 8192
    assert working_context(None, 16384, 2048) == 4096  # never below the floor
    assert working_context({"num_ctx": "bad"}, 16384, None) == 16384


def test_budget_tracks_the_window() -> None:
    assert conversation_char_budget(16384, 240_000) < conversation_char_budget(32768, 240_000)
    assert conversation_char_budget(1_000_000, 240_000) == 240_000


def _conversation(results: int, size: int) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "SYSTEM"},
        {"role": "user", "content": "TASK: write report/total.txt"},
    ]
    for index in range(results):
        messages.append({"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": {"path": f"f{index}"}}}]})
        messages.append({"role": "tool", "tool_name": "read_file", "content": "x" * size})
    return messages


def test_compaction_elides_old_results_before_dropping_turns() -> None:
    messages = _conversation(10, 3_000)
    budget = len(json.dumps(messages)) // 2
    compacted, elided, dropped = compact_messages(messages, budget)
    assert compacted[:2] == messages[:2]
    assert elided > 0 and dropped == 0
    assert len(json.dumps(compacted)) <= budget
    assert compacted[-1]["content"] == "x" * 3_000  # the newest result is intact


def test_compaction_drops_turns_but_never_the_task() -> None:
    messages = _conversation(30, 3_000)
    compacted, _elided, dropped = compact_messages(messages, 12_000)
    assert dropped > 0
    assert compacted[0]["content"] == "SYSTEM"
    assert compacted[1]["content"] == "TASK: write report/total.txt"
    assert compacted[2]["content"].startswith("HOST NOTE")
    assert compacted[3]["role"] != "tool"  # no orphaned tool result


class RecordingClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def chat(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {"message": {"role": "assistant", "content": "Done."}}


def test_every_request_names_its_window(tmp_path) -> None:
    profile = ModelProfile(model_name="m:1", digest="d", generated_at="now",
                           agentability_mode="native_tools", context_length=262144)
    for runtime, policy, expected in (
        (AgentRuntime(RecordingClient()), None, 16384),
        (AgentRuntime(RecordingClient(), num_ctx=32768), None, 32768),
        (AgentRuntime(RecordingClient()), RunPolicy(options={"num_ctx": 8192}), 8192),
    ):
        runtime.run("m:1", profile, "Answer.", WorkspaceTools(tmp_path), policy=policy)
        assert runtime.client.calls[0]["options"]["num_ctx"] == expected  # type: ignore[attr-defined]


def test_direct_chat_window_grows_with_the_conversation_within_the_model() -> None:
    from ollama_workstation.agents.runtime import conversation_window

    short = [{"role": "user", "content": "hi"}]
    assert conversation_window(short, 262144) == 16384  # never Ollama's silent 4096
    long = [{"role": "user", "content": "x" * 60_000}]  # about 30k tokens at 2 chars/token
    window = conversation_window(long, 262144)
    assert window >= 32_768 and window % 8_192 == 0
    assert conversation_window(long, 8192) == 8192  # the model's limit is the ceiling
    huge = [{"role": "user", "content": "x" * 2_000_000}]
    assert conversation_window(huge, None) == 131_072
