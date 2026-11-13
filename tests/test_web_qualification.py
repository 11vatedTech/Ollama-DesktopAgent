"""Web tools in harness qualification: offline test web, conditional tasks, and
fingerprints that leave every existing qualification valid."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ollama_workstation.agentification import harness as h
from ollama_workstation.agentification.gate import QUALIFIABLE_TOOLS
from ollama_workstation.agentification.tasks import instantiate

# The agent-core suite fingerprint recorded by every qualification made before
# the web tasks existed (live records on the reference workstation). If this
# changes, every stored qualification silently stops matching its suite.
PRE_WEB_SUITE_SHA256_PREFIX = "6dc7b6886dbe"


def _effective(factory: h.ToolFactory) -> tuple[h.TaskSpec, ...]:
    with tempfile.TemporaryDirectory() as directory:
        names = {item["function"]["name"] for item in factory(Path(directory) / "w", None).definitions()}
    return tuple(spec for spec in h.core_suite() if all(t in names for t in spec.requires_tools))


def test_web_tasks_do_not_change_existing_catalogues() -> None:
    for factory in (h.default_tool_factory, h.shell_tool_factory):
        tasks = _effective(factory)
        assert not any(spec.web_pages for spec in tasks)
        assert h.suite_fingerprint(tasks).startswith(PRE_WEB_SUITE_SHA256_PREFIX)


def test_web_catalogue_adds_research_and_injection_tasks() -> None:
    tasks = _effective(h.web_tool_factory)
    ids = [spec.task_id for spec in tasks]
    assert ids[-2:] == ["web_lookup", "web_injection"]
    assert {spec.category for spec in tasks} >= {"web_research", "injection_resistance"}
    assert not h.suite_fingerprint(tasks).startswith(PRE_WEB_SUITE_SHA256_PREFIX)
    assert {"web_search", "web_fetch"} <= QUALIFIABLE_TOOLS


def test_offline_web_serves_seeded_pages_through_the_production_tools(tmp_path: Path) -> None:
    spec = next(item for item in h.core_suite() if item.task_id == "web_lookup")
    instance = instantiate(spec, 7)
    workspace = tmp_path / "workspace"
    instance.materialize(workspace)
    for relative, content in instance.web_pages.items():
        target = tmp_path / h.WEB_SITE_DIRECTORY / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    tools = h.web_tool_factory(workspace, None)
    found = tools.invoke("web_search", {"query": "Corvid release notes"})
    urls = [item["url"] for item in found["results"]]
    assert f"https://{h.QUALIFY_WEB_HOST}/releases/corvid.html" in urls
    page = tools.invoke("web_fetch", {"url": urls[0] if "corvid" in urls[0] else urls[1]})
    assert instance.values["code"] in page["text"]
    assert not list(workspace.rglob("*.html")), "web pages must never land in the workspace"
