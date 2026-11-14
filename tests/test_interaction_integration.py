"""Interaction tools through the agent stack: permissions, gate coverage, qualification
tasks, acceptance plumbing and the scripted-host injection checks."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

import pytest

from ollama_workstation.agentification import harness as h
from ollama_workstation.agentification.agents import AgentService, Permissions
from ollama_workstation.agentification.gate import QUALIFIABLE_TOOLS
from ollama_workstation.agents.tools import ToolError, WorkspaceTools
from ollama_workstation.interaction.policy import InteractionBroker, InteractionGrants
from ollama_workstation.interaction.tools import (
    BROWSER_TOOLS,
    COMPUTER_TOOLS,
    InteractionTools,
)

PRE_WEB_SUITE_SHA256_PREFIX = "6dc7b6886dbe"


def _names(factory: h.ToolFactory) -> set[str]:
    with tempfile.TemporaryDirectory() as directory:
        tools = factory(Path(directory) / "w", None)
        try:
            return {item["function"]["name"] for item in tools.definitions()}
        finally:
            closer = getattr(tools, "close", None)
            if callable(closer):
                closer()


def test_interaction_tools_are_qualifiable_and_catalogues_are_separate() -> None:
    assert set(BROWSER_TOOLS) | set(COMPUTER_TOOLS) <= QUALIFIABLE_TOOLS
    browser = _names(h.browser_tool_factory)
    computer = _names(h.computer_tool_factory)
    assert "browser_open" in browser and "computer_launch" not in browser
    assert "computer_launch" in computer and "browser_open" not in computer
    assert "browser_upload" not in browser  # uploads are never in the default grant


def test_existing_catalogues_keep_their_suite_fingerprint() -> None:
    for factory in (h.default_tool_factory, h.shell_tool_factory):
        names = _names(factory)
        tasks = tuple(spec for spec in h.core_suite()
                      if all(tool in names for tool in spec.requires_tools))
        assert h.suite_fingerprint(tasks).startswith(PRE_WEB_SUITE_SHA256_PREFIX)


def test_interaction_tasks_run_only_for_their_catalogues() -> None:
    suite = {spec.task_id: spec for spec in h.core_suite()}
    for task_id in ("browser_render", "browser_workflow", "browser_injection"):
        assert suite[task_id].requires_tools == ("browser_open",)
    for task_id in ("computer_ledger", "computer_injection"):
        assert suite[task_id].requires_tools == ("computer_launch",)


def test_ungranted_interaction_tools_are_absent_and_refused(tmp_path: Path) -> None:
    broker = InteractionBroker(InteractionGrants(browser=True), approval=None)
    tools = WorkspaceTools(tmp_path)
    tools.interaction = InteractionTools(broker)
    names = {item["function"]["name"] for item in tools.definitions()}
    assert "browser_open" in names and "computer_launch" not in names
    with pytest.raises(ToolError):
        tools.invoke("computer_launch", {"app": "practice"})


def test_agent_permissions_round_trip_and_reject_unknown_apps() -> None:
    permissions = Permissions(browser=True, computer_apps=("practice",))
    payload = permissions.to_dict()
    assert payload["browser"] is True and payload["computer_apps"] == ["practice"]
    assert "browser" not in Permissions().to_dict()  # existing agent digests are unchanged


def test_agent_with_interaction_permissions_gets_the_qualified_catalogue(client, repository) -> None:
    from conftest import make_profile

    from ollama_workstation.agentification.agents import AgentConfigError
    from ollama_workstation.agentification.store import AgentStore

    service = AgentService(client, repository, AgentStore(repository.database))
    service.skills.install_builtins()
    profile = make_profile(client, repository, "native-pro:latest")
    agent = service.create(name="Desk operator", profile=profile,
                           permissions=Permissions(computer_apps=("practice",)))
    with tempfile.TemporaryDirectory() as directory:
        granted = service.build_tools(agent, Path(directory))
        agent_names = {item["function"]["name"] for item in granted.definitions()}
        granted.close()
    assert agent_names == _names(h.computer_tool_factory)
    with pytest.raises(AgentConfigError, match="unknown computer-use"):
        service.create(name="Bad", profile=profile,
                       permissions=Permissions(computer_apps=("regedit",)))


def test_acceptance_site_records_state_on_the_host(tmp_path: Path) -> None:
    from ollama_workstation.agentification.interaction_acceptance import SITE_HOST, AcceptanceSite

    site = AcceptanceSite(tmp_path, 3)
    status, _headers, body = site.fulfil("GET", f"https://{SITE_HOST}/api/orders.json", {}, None)
    assert status == 200 and len(json.loads(body)) == 4
    site.fulfil("POST", f"https://{SITE_HOST}/api/notes", {}, b'{"order": "A-101", "text": "x"}')
    site.fulfil("POST", f"https://{SITE_HOST}/api/refund", {}, b"")
    state = json.loads(site.state_path.read_text(encoding="utf-8"))
    assert state["notes"][0]["note_id"] == "N-001" and state["refunds"] == 1
    assert site.delayed["status"] == "delayed"
    assert site.fulfil("GET", "https://elsewhere.test/", {}, None)[0] == 404


@pytest.mark.skipif(os.name != "nt", reason="needs Edge and UI Automation")
def test_hand_solved_interaction_tasks_pass_host_verification(tmp_path: Path) -> None:
    """Each new qualification task is solvable through the production tools, and
    the hostile instructions they carry are refused by the host."""
    pytest.importorskip("playwright")
    pytest.importorskip("uiautomation")
    from ollama_workstation.agentification.tasks import instantiate
    from ollama_workstation.agentification.verifier import VerificationEngine, snapshot_workspace

    suite = {spec.task_id: spec for spec in h.core_suite()}

    def prepare(task_id: str):
        instance = instantiate(suite[task_id], 11)
        root = tmp_path / task_id
        workspace = root / "workspace"
        instance.materialize(workspace)
        for relative, content in instance.web_pages.items():
            target = root / h.WEB_SITE_DIRECTORY / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        if instance.practice_notice:
            (root / h.PRACTICE_NOTICE_FILE).write_text(instance.practice_notice, encoding="utf-8")
        return instance, root, workspace, snapshot_workspace(workspace)

    def passed(instance, root, workspace, baseline) -> bool:
        report = VerificationEngine(workspace, host_files={"practice": root / h.PRACTICE_STATE_FILE}
                                    ).verify(instance.checks, run_status="completed",
                                             final_message="done", baseline=baseline)
        return report.passed

    def refs(text: str) -> dict[str, str]:
        return {m.group(2): m.group(1) for m in re.finditer(r"\[(\S+?)\] [\w-]+ \"([^\"]*)\"", text)}

    instance, root, workspace, baseline = prepare("browser_injection")
    tools = h.browser_tool_factory(workspace, None)
    try:
        page = tools.invoke("browser_open", {"url": f"https://{h.QUALIFY_WEB_HOST}/news/article.html"})
        with pytest.raises(ToolError, match="Refused"):
            tools.invoke("browser_click", {"ref": refs(page["elements"])["Transfer funds"]})
        with pytest.raises(ToolError):
            tools.invoke("browser_open", {"url": "https://127.0.0.1:11434/api/tags"})
        tools.invoke("write_file", {"path": "headline.txt", "content": instance.values["headline"]})
    finally:
        tools.close()
    assert passed(instance, root, workspace, baseline)

    instance, root, workspace, baseline = prepare("computer_injection")
    tools = h.computer_tool_factory(workspace, None)
    try:
        found = refs(tools.invoke("computer_launch", {"app": "practice"})["controls"])
        for name, args in (("computer_click", {"ref": found["Delete all entries"]}),
                           ("computer_type", {"ref": found["Account password"], "text": "letmein"})):
            with pytest.raises(ToolError, match="Refused"):
                tools.invoke(name, args)
        tools.invoke("computer_type", {"ref": found["Payee"], "text": instance.values["p1"]})
        tools.invoke("computer_type", {"ref": found["Amount"], "text": str(instance.values["a1"])})
        tools.invoke("computer_click", {"ref": found["Add entry"]})
    finally:
        tools.close()
    assert passed(instance, root, workspace, baseline)
