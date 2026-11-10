from pathlib import Path
from threading import Event

from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.storage.repository import Repository


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []
        self.messages = []

    def chat(self, model, messages, **kwargs):
        self.payloads.append(kwargs)
        self.messages.append(messages)
        return self.responses.pop(0)

def test_native_tool_agent_inspects_workspace_and_persists_trace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("hello", encoding="utf-8")
    client = FakeClient([
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "read_file", "arguments": {"path": "hello.txt"}}}
        ]}},
        {"message": {"role": "assistant", "content": "The file says hello."}},
    ])
    profile = ModelProfile(model_name="fake:latest", digest="abc", generated_at="now",
                           professional_title="Repository Cartographer", agentability_mode="native_tools")
    repository = Repository(tmp_path / "state.sqlite3")
    result = AgentRuntime(client, repository=repository).run(
        "fake:latest", profile, "Read hello.txt", WorkspaceTools(workspace))
    assert result.status == "completed"
    assert result.final_message == "The file says hello."
    assert any(event.kind == "tool_completed" for event in result.trace)
    assert repository.list_runs()[0]["status"] == "completed"
    journal = repository.list_run_events(result.run_id)
    assert journal[0]["kind"] == "started"
    assert journal[-1]["kind"] == "completed"
    assert len(journal) == len(result.trace)
    assert client.payloads[0]["tools"]

def test_guided_agent_can_use_action_text_fallback(tmp_path: Path) -> None:
    client = FakeClient([
        {"message": {"role": "assistant", "content":
            'ACTION: {"name":"list_files","arguments":{"path":"."}}'}},
        {"message": {"role": "assistant", "content": "FINAL: Workspace inspected."}},
    ])
    profile = ModelProfile(model_name="weak-tools:latest", digest="def", generated_at="now",
                           professional_title="General Intelligence Associate", agentability_mode="guided")
    result = AgentRuntime(client).run(
        "weak-tools:latest", profile, "List the workspace", WorkspaceTools(tmp_path / "workspace"))
    assert result.status == "completed"
    assert result.final_message == "Workspace inspected."
    assert any(event.kind == "tool_completed" for event in result.trace)



def test_guided_harness_receives_explicit_host_tool_catalogue(tmp_path: Path) -> None:
    client = FakeClient([
        {"message": {"role": "assistant", "content": "FINAL: No changes required."}},
    ])
    profile = ModelProfile(
        model_name="guided:latest", digest="digest", generated_at="now",
        professional_title="General Intelligence Associate", agentability_mode="guided",
    )
    result = AgentRuntime(client).run(
        "guided:latest", profile, "Inspect the workspace", WorkspaceTools(tmp_path / "workspace")
    )
    assert result.status == "completed"
    system_prompt = client.messages[0][0]["content"]
    assert "HOST-APPROVED TOOL CATALOGUE" in system_prompt
    assert '"name": "list_files"' in system_prompt
    assert 'ACTION: {"name":"EXACT_TOOL_NAME"' in system_prompt
    assert "FINAL: your concise result" in system_prompt


def test_structured_action_harness_receives_json_protocol_and_tool_schema(tmp_path: Path) -> None:
    client = FakeClient([
        {"message": {"role": "assistant", "content":
            '{"action":"tool","name":"list_files","arguments":{"path":"."}}'}},
        {"message": {"role": "assistant", "content":
            '{"action":"final","message":"Workspace listing completed."}'}},
    ])
    profile = ModelProfile(
        model_name="structured:latest", digest="digest", generated_at="now",
        professional_title="Workflow Operations Specialist",
        agentability_mode="structured_actions",
    )
    result = AgentRuntime(client).run(
        "structured:latest", profile, "List the workspace", WorkspaceTools(tmp_path / "workspace")
    )
    assert result.status == "completed"
    assert result.final_message == "Workspace listing completed."
    system_prompt = client.messages[0][0]["content"]
    assert "Return exactly one JSON action object" in system_prompt
    assert '"name": "list_files"' in system_prompt
    assert client.payloads[0]["output_format"]["properties"]["action"]["enum"] == ["tool", "final"]



def test_pre_cancelled_agent_persists_cancelled_run_without_model_call(tmp_path: Path) -> None:
    client = FakeClient([])
    profile = ModelProfile(
        model_name="fake:latest", digest="abc", generated_at="now",
        professional_title="General Intelligence Associate", agentability_mode="guided",
    )
    repository = Repository(tmp_path / "cancel.sqlite3")
    cancel_event = Event()
    cancel_event.set()
    result = AgentRuntime(client, repository=repository).run(
        "fake:latest", profile, "Do not start", WorkspaceTools(tmp_path / "workspace"),
        cancel_event=cancel_event,
    )
    assert result.status == "cancelled"
    assert result.steps == 0
    assert client.messages == []
    assert repository.list_runs()[0]["status"] == "cancelled"


def test_cancellation_after_model_response_prevents_tool_side_effect(tmp_path: Path) -> None:
    cancel_event = Event()

    class CancelAfterResponse(FakeClient):
        def chat(self, model, messages, **kwargs):
            response = super().chat(model, messages, **kwargs)
            cancel_event.set()
            return response

    workspace = tmp_path / "workspace"
    client = CancelAfterResponse([
        {"message": {"role": "assistant", "content": "", "tool_calls": [
            {"function": {"name": "write_file",
                          "arguments": {"path": "new.txt", "content": "must not be written"}}}
        ]}},
    ])
    profile = ModelProfile(
        model_name="fake:latest", digest="abc", generated_at="now",
        professional_title="Native Tool Operations Specialist", agentability_mode="native_tools",
    )
    result = AgentRuntime(client).run(
        "fake:latest", profile, "Write a file", WorkspaceTools(workspace),
        cancel_event=cancel_event,
    )
    assert result.status == "cancelled"
    assert not (workspace / "new.txt").exists()
    assert not any(event.kind == "tool_completed" for event in result.trace)
