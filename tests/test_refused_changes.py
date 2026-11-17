"""A refused change must never be recorded as done (live finding, session 8).

`11vated agent run` without --yes refused an edit of app/config.py; the model said so,
then wrote notes/summary.md stating the new value it had failed to set.
"""

from __future__ import annotations

from typing import Any

from ollama_workstation.agentification.verifier import (
    VerificationEngine,
    VerificationStatus,
    with_refused_claims,
)
from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import ApprovalDenied, WorkspaceTools
from ollama_workstation.models.schemas import ModelProfile

TASK = ("Change TIMEOUT in app/config.py to 45 and write a one-line summary of the file "
        "to notes/summary.md.")


class ScriptedClient:
    def __init__(self, *responses: dict[str, Any]) -> None:
        self.responses = list(responses)
        self.messages: list[list[dict[str, Any]]] = []

    def chat(self, model: str, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.messages.append([dict(item) for item in messages])
        return self.responses.pop(0)


def _call(name: str, **arguments: Any) -> dict[str, Any]:
    return {"message": {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": name, "arguments": arguments}}]}}


def _final(text: str) -> dict[str, Any]:
    return {"message": {"role": "assistant", "content": text}}


def _profile() -> ModelProfile:
    return ModelProfile(model_name="m:1", digest="d", generated_at="now",
                        agentability_mode="native_tools")


def _workspace(tmp_path):
    (tmp_path / "app").mkdir()
    (tmp_path / "app" / "config.py").write_text("TIMEOUT = 30\nRETRIES = 3\n", encoding="utf-8")
    return tmp_path


def _run(tmp_path, *responses: dict[str, Any]):
    tools = WorkspaceTools(_workspace(tmp_path), allow_edit=True, approval=lambda _d: False)
    client = ScriptedClient(*responses)
    return AgentRuntime(client, max_steps=8).run("m:1", _profile(), TASK, tools), client


def test_refusal_is_explicit_to_the_model_and_raised_as_its_own_error(tmp_path) -> None:
    direct = tmp_path / "direct"
    direct.mkdir()
    tools = WorkspaceTools(_workspace(direct), allow_edit=True, approval=lambda _d: False)
    try:
        tools.edit_file("app/config.py", "TIMEOUT = 30", "TIMEOUT = 45")
    except ApprovalDenied as exc:
        assert "not changed" in str(exc)
    else:  # pragma: no cover - the refusal is the point of the test
        raise AssertionError("edit was not refused")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    result, client = _run(
        run_dir,
        _call("edit_file", path="app/config.py", old_string="TIMEOUT = 30",
              new_string="TIMEOUT = 45"),
        _call("write_file", path="notes/summary.md", content="Config with TIMEOUT=45."),
        _final("I could not change TIMEOUT; the edit was refused."),
    )
    tool_message = client.messages[1][-1]
    assert '"refused": true' in tool_message["content"]
    assert '"performed": false' in tool_message["content"]
    assert (run_dir / "app" / "config.py").read_text(encoding="utf-8").startswith("TIMEOUT = 30")
    assert result.status == "completed"
    # Even with a truthful final answer, the record names what was refused.
    assert "[Host record] Refused and not performed: edit_file app/config.py" in result.final_message


def test_artifact_recording_a_refused_change_is_flagged_and_annotated(tmp_path) -> None:
    result, _client = _run(
        tmp_path,
        _call("edit_file", path="app/config.py", old_string="TIMEOUT = 30",
              new_string="TIMEOUT = 45"),
        _call("write_file", path="notes/summary.md",
              content="Configuration file with TIMEOUT=45, RETRIES=3."),
        _final("Done."),
    )
    claims = result.usage["refused_change_claims"]
    assert claims[0]["artifact"] == "notes/summary.md"
    assert claims[0]["refused_path"] == "app/config.py"
    assert result.usage["refused_operations"] == [{"tool": "edit_file", "path": "app/config.py"}]
    # The completion record cannot read as success for what was refused.
    assert "[Host record] Refused and not performed: edit_file app/config.py" in result.final_message
    assert "notes/summary.md records a change that was refused" in result.final_message
    kinds = [event.kind for event in result.trace]
    assert "operation_refused" in kinds and "refused_change_claimed" in kinds


def test_truthful_summary_after_refusal_is_not_flagged(tmp_path) -> None:
    result, _client = _run(
        tmp_path,
        _call("edit_file", path="app/config.py", old_string="TIMEOUT = 30",
              new_string="TIMEOUT = 45"),
        _call("write_file", path="notes/summary.md",
              content="Configuration with TIMEOUT = 30 and RETRIES = 3; the edit was refused."),
        _final("The edit was refused."),
    )
    assert "refused_change_claims" not in result.usage
    assert result.usage["refused_operations"] == [{"tool": "edit_file", "path": "app/config.py"}]


def test_remedied_refusal_leaves_no_record(tmp_path) -> None:
    answers = iter([False, True])
    tools = WorkspaceTools(_workspace(tmp_path), allow_edit=True,
                           approval=lambda _d: next(answers))
    client = ScriptedClient(
        _call("edit_file", path="app/config.py", old_string="TIMEOUT = 30",
              new_string="TIMEOUT = 45"),
        _call("edit_file", path="app/config.py", old_string="TIMEOUT = 30",
              new_string="TIMEOUT = 45"),
        _call("write_file", path="notes/summary.md", content="TIMEOUT = 45"),
        _final("Done."),
    )
    result = AgentRuntime(client, max_steps=8).run("m:1", _profile(), TASK, tools)
    assert "refused_operations" not in result.usage
    assert "refused_change_claims" not in result.usage
    assert "[Host record]" not in result.final_message


def test_verification_fails_when_an_artifact_records_a_refused_change(tmp_path) -> None:
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "summary.md").write_text("TIMEOUT=45", encoding="utf-8")
    passing = VerificationEngine(tmp_path).verify(
        [{"kind": "file_exists", "path": "notes/summary.md"}], run_status="completed")
    assert passing.passed
    report = with_refused_claims(passing, [{"artifact": "notes/summary.md",
                                            "refused_tool": "edit_file",
                                            "refused_path": "app/config.py",
                                            "fragments": ["timeout=45"]}])
    assert report.status is VerificationStatus.FAILED and not report.passed
    assert "claim_unsupported" in report.failure_reasons
    assert with_refused_claims(passing, []) is passing


def test_gate_does_not_demand_a_file_whose_write_was_refused(tmp_path) -> None:
    (tmp_path / "report.md").write_text("old", encoding="utf-8")
    tools = WorkspaceTools(tmp_path, approval=lambda _d: False)
    client = ScriptedClient(
        _call("write_file", path="report.md", content="new findings for the report"),
        _final("The overwrite of report.md was refused, so it is unchanged."),
    )
    result = AgentRuntime(client, max_steps=4).run(
        "m:1", _profile(), "Update report.md with the new findings.", tools)
    kinds = [event.kind for event in result.trace]
    assert "deliverables_declared" in kinds and "completion_refused" not in kinds
    assert result.usage["refused_operations"] == [{"tool": "write_file", "path": "report.md"}]
    assert (tmp_path / "report.md").read_text(encoding="utf-8") == "old"


def test_a_repeated_refusal_tells_the_model_to_stop_and_report(tmp_path) -> None:
    edit = _call("edit_file", path="app/config.py", old_string="TIMEOUT = 30",
                 new_string="TIMEOUT = 45")
    # notes/summary.md is still owed, so the deliverable gate asks twice more.
    result, client = _run(tmp_path, edit, edit,
                          *[_final("The edit was refused twice.") for _ in range(3)])
    first = client.messages[1][-1]["content"]
    second = client.messages[2][-1]["content"]
    assert "refused again" not in first
    assert "refused again" in second and "final answer now" in second
    assert result.status == "completed"


LIVE_PARAPHRASE = ("app/config.py - TIMEOUT was set to 45 (edit operation on config.py was "
                   "refused by system; only summary created)")


def test_live_paraphrase_is_flagged_as_a_possible_claim_on_any_terminal_status(tmp_path) -> None:
    """The literal detector missed this live summary (session 9, Deny drive)."""
    edit = _call("edit_file", path="app/config.py", old_string="TIMEOUT = 30",
                 new_string="TIMEOUT = 45")
    tools = WorkspaceTools(_workspace(tmp_path), allow_edit=True, approval=lambda _d: False)
    client = ScriptedClient(edit, _call("write_file", path="notes/summary.md",
                                        content=LIVE_PARAPHRASE),
                            _call("list_files", path="."))
    result = AgentRuntime(client, max_steps=3).run("m:1", _profile(), TASK, tools)
    assert result.status == "step_limit"
    assert "refused_change_claims" not in result.usage  # not a literal match
    possible = result.usage["refused_change_possible_claims"][0]
    assert possible["identifier"] == "timeout" and possible["value"] == "45"
    assert "may describe a refused change as done" in result.final_message
    assert "Refused and not performed: edit_file app/config.py" in result.final_message


def test_value_mention_rules() -> None:
    from ollama_workstation.agents.runtime import _changed_values, _mentions_value

    assert _changed_values("edit_file", {"old_string": "TIMEOUT = 30\nRETRIES = 3",
                                         "new_string": "TIMEOUT = 45\nRETRIES = 3"}) \
        == [("timeout", "45")]
    assert _changed_values("write_file", {"content": "TIMEOUT = 45"}) == []
    assert _mentions_value(LIVE_PARAPHRASE, "timeout", "45")
    # An honest note can match: this is why the signal warns and never fails verification.
    assert _mentions_value("Setting TIMEOUT to 45 was refused.", "timeout", "45")
    assert not _mentions_value("TIMEOUT stays 30; the change was refused.", "timeout", "45")
    assert not _mentions_value("TIMEOUT = 30, port 4500", "timeout", "45")
