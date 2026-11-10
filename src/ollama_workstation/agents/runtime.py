"""Bounded Ollama agent loop with native-tool and structured-action adapters."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from ollama_workstation.agents.tools import ToolError, WorkspaceTools
from ollama_workstation.models.schemas import ModelProfile, utc_now
from ollama_workstation.ollama.client import OllamaClient


@dataclass(slots=True)
class AgentEvent:
    kind: str
    message: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=utc_now)


@dataclass(slots=True)
class AgentResult:
    run_id: str
    status: str
    final_message: str
    steps: int
    trace: list[AgentEvent]


class AgentRuntime:
    MAX_TOOL_RESULT_CHARS = 12_000
    MAX_CONVERSATION_CHARS = 240_000

    def __init__(self, client: OllamaClient, *, max_steps: int = 12,
                 keep_alive: str = "5m", repository: Any | None = None) -> None:
        self.client = client
        self.max_steps = max(1, min(max_steps, 40))
        self.keep_alive = keep_alive
        self.repository = repository

    def run(self, model: str, profile: ModelProfile, task: str, tools: WorkspaceTools,
            *, event: Callable[[AgentEvent], None] | None = None) -> AgentResult:
        emit = event or (lambda _item: None)
        run_id = str(uuid.uuid4())
        trace: list[AgentEvent] = []

        def record(kind: str, message: str, **data: Any) -> None:
            item = AgentEvent(kind, message, data)
            trace.append(item)
            emit(item)

        def finish(status: str, final_message: str, steps: int) -> AgentResult:
            result = AgentResult(run_id, status, final_message, steps, trace)
            if self.repository is not None:
                try:
                    self.repository.save_run(
                        run_id, model, profile.professional_title, task, status,
                        trace[0].timestamp if trace else utc_now(), utc_now(),
                        [{"kind": item.kind, "message": item.message, "data": item.data,
                          "timestamp": item.timestamp} for item in trace],
                    )
                except Exception as exc:
                    record("persistence_error", f"Execution finished but trace persistence failed: {exc}")
            return result

        tool_specs = tools.definitions()
        tool_names = [entry["function"]["name"] for entry in tool_specs]
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": self._system_prompt(profile)},
            {"role": "user", "content": task},
        ]
        native_tools = profile.agentability_mode == "native_tools"
        structured = profile.agentability_mode == "structured_actions"
        record("started", f"Agent started as {profile.professional_title}.",
               model=model, mode=profile.agentability_mode, workspace=str(tools.workspace))

        for step in range(1, self.max_steps + 1):
            current_size = sum(len(str(message.get("content") or "")) for message in messages)
            if current_size > self.MAX_CONVERSATION_CHARS:
                messages = [messages[0], messages[1], *messages[-8:]]
                record("context_compacted", "Older turns removed to respect the local context budget.",
                       previous_chars=current_size, retained_messages=len(messages))
                current_size = sum(len(str(message.get("content") or "")) for message in messages)
                if current_size > self.MAX_CONVERSATION_CHARS:
                    record("context_limit", "Current task context exceeds runtime budget.")
                    return finish("context_limit", "Stopped because task context exceeded the safe runtime budget.", step - 1)
            record("thinking", f"Requesting model response ({step}/{self.max_steps}).", step=step)
            try:
                response = self.client.chat(
                    model, messages, tools=tool_specs if native_tools else None,
                    output_format=self._action_schema(tool_names) if structured else None,
                    keep_alive=self.keep_alive,
                )
            except Exception as exc:
                record("error", f"Ollama request failed: {exc}")
                return finish("failed", str(exc), step - 1)

            assistant = response.get("message") or {}
            messages.append({"role": "assistant", **assistant})
            calls = assistant.get("tool_calls") or []
            if not calls and structured:
                calls, final = self._decode_structured_action(str(assistant.get("content") or ""))
                if final is not None:
                    record("completed", "Agent returned a final response.")
                    return finish("completed", final, step)
            if not calls:
                calls, guided_final = self._decode_guided_action(
                    str(assistant.get("content") or ""), tool_names)
                if guided_final is not None:
                    record("completed", "Agent returned a final response.")
                    return finish("completed", guided_final, step)
            if not calls:
                final = str(assistant.get("content") or "").strip()
                if final.startswith("FINAL:"):
                    final = final[6:].strip()
                if not final:
                    record("error", "Model returned neither a tool call nor a final response.")
                    return finish("failed", "The model returned an empty response.", step)
                record("completed", "Agent returned a final response.")
                return finish("completed", final, step)

            for call in calls:
                function = call.get("function") or {}
                name = str(function.get("name") or "")
                arguments = function.get("arguments") or {}
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except json.JSONDecodeError:
                        arguments = {}
                if name not in tool_names or not isinstance(arguments, dict):
                    output: dict[str, Any] = {"error": "Unknown tool or invalid arguments."}
                    record("tool_rejected", f"Rejected invalid tool request: {name}.", tool=name)
                else:
                    try:
                        record("tool_started", f"Executing {name}.", tool=name, arguments=arguments)
                        output = tools.invoke(name, arguments)
                        record("tool_completed", f"Completed {name}.", tool=name, result=output)
                    except ToolError as exc:
                        output = {"error": str(exc)}
                        record("tool_denied", str(exc), tool=name)
                encoded_output = json.dumps(output, ensure_ascii=False)
                if len(encoded_output) > self.MAX_TOOL_RESULT_CHARS:
                    encoded_output = json.dumps({
                        "truncated": True, "preview": encoded_output[:self.MAX_TOOL_RESULT_CHARS],
                        "original_characters": len(encoded_output),
                        "note": "Tool result was truncated by the host context budget.",
                    }, ensure_ascii=False)
                messages.append({"role": "tool", "tool_name": name, "content": encoded_output})

        record("limit_reached", f"Stopped at the configured {self.max_steps}-step limit.")
        return finish("step_limit", "Execution stopped at the maximum step limit. Review the trace and continue with a new task.", self.max_steps)

    @staticmethod
    def _system_prompt(profile: ModelProfile) -> str:
        return (
            f"You are {profile.professional_title}. Your assigned specialization is: "
            f"{profile.primary_specialization}\n\n"
            "You are operating inside a controlled local agent workstation. Complete the user's "
            "objective using only the provided tools and assigned workspace. Inspect before editing. "
            "Do not claim actions were completed unless tool results establish that fact. If a tool "
            "fails, inspect the error and choose a safe recovery. Never attempt to bypass permissions, "
            "read outside the workspace, access secrets, or invent tool results. Ask for clarification "
            "when an irreversible or ambiguous action is required. Finish with a concise report of "
            "changes, tests and remaining limitations."
        )

    @staticmethod
    def _decode_guided_action(text: str, allowed_names: list[str]) -> tuple[list[dict[str, Any]], str | None]:
        stripped = text.strip()
        if stripped.startswith("FINAL:"):
            return [], stripped[6:].strip()
        candidate = stripped
        if candidate.startswith("ACTION:"):
            candidate = candidate[len("ACTION:"):].strip()
        elif not candidate.startswith("{"):
            return [], None
        try:
            payload, _ = json.JSONDecoder().raw_decode(candidate)
        except json.JSONDecodeError:
            return [], None
        if not isinstance(payload, dict):
            return [], None
        name = payload.get("name") or payload.get("tool")
        arguments = payload.get("arguments") or payload.get("args") or {}
        if name not in allowed_names or not isinstance(arguments, dict):
            return [], None
        return [{"function": {"name": name, "arguments": arguments}}], None

    @staticmethod
    def _action_schema(tool_names: list[str]) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["tool", "final"]},
                "name": {"type": "string", "enum": tool_names},
                "arguments": {"type": "object"},
                "message": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    @staticmethod
    def _decode_structured_action(text: str) -> tuple[list[dict[str, Any]], str | None]:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            return [], None
        if not isinstance(payload, dict):
            return [], None
        if payload.get("action") == "final":
            return [], str(payload.get("message") or "")
        if payload.get("action") == "tool" and isinstance(payload.get("name"), str):
            return [{"function": {"name": payload["name"],
                                  "arguments": payload.get("arguments") or {}}}], None
        return [], None
