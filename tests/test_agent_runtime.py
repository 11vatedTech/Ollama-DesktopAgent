from pathlib import Path

from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.storage.repository import Repository


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.payloads = []
    def chat(self, model, messages, **kwargs):
        self.payloads.append(kwargs)
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
