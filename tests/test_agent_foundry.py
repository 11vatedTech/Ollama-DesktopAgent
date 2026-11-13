"""Agent Foundry: controller and desktop page, driven end to end over a real HTTP socket."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ollama_workstation.agentification.foundry import FoundryController  # noqa: E402
from ollama_workstation.agents.memory import MemoryStore  # noqa: E402
from ollama_workstation.config import AppPaths, Settings  # noqa: E402
from ollama_workstation.ui.pages.agents import AgentFoundryPage  # noqa: E402
from ollama_workstation.ui.state import ActivityRegistry, AppContext, EventBus  # noqa: E402


class _Admit:
    def require(self, model, num_ctx=None):
        return self.evaluate(model, num_ctx)

    def evaluate(self, model, num_ctx=None):
        from ollama_workstation.agentification.resources import AdmissionDecision
        return AdmissionDecision(model, "admit", ("fits (test)",), 1, None, 1, 1)


@pytest.fixture(scope="module")
def qt_app() -> QApplication:
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


@pytest.fixture
def app_context(tmp_path: Path, client, repository) -> AppContext:
    root = tmp_path / "app"
    paths = AppPaths(root, root / "settings.json", repository.database, root / "workspaces",
                     root / "profiles", root / "logs")
    paths.ensure()
    settings = Settings(workspace_root=str(paths.workspaces_dir))
    from ollama_workstation.profiling.profiler import ModelProfiler
    return AppContext(paths=paths, settings=settings, client=client,
                      profiler=ModelProfiler(client), repository=repository,
                      memory_store=MemoryStore(repository), pool=QThreadPool(),
                      events=EventBus(), activity=ActivityRegistry())


_STATUS = [lambda: ""]


def _wait(app: QApplication, predicate, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("timed out waiting for the UI; status: " + _STATUS[0]())
        QTest.qWait(25)
        app.processEvents()


def test_controller_lists_models_and_qualifies(client, repository) -> None:
    controller = FoundryController(client, repository, Settings())
    controller.admission = _Admit()
    rows = {row.name: row for row in controller.model_rows()}
    assert rows["json-only:latest"].qualification_status == "not qualified"
    record = controller.qualify("json-only:latest", trials=1)
    assert record.selected_harness == "structured_actions"
    row = {row.name: row for row in controller.model_rows()}["json-only:latest"]
    assert row.qualification_status in {"qualified", "provisional"}
    assert row.harness == "structured_actions" and row.attempts > 0


def test_foundry_page_full_lifecycle(qt_app, app_context, tmp_path) -> None:
    page = AgentFoundryPage(app_context)
    _STATUS[0] = lambda: f"{page.status.text()} busy={page._busy} active={app_context.pool.activeThreadCount()}/{app_context.pool.maxThreadCount()} agents={page.agents_table.rowCount()} run={page.run_agent.count()}"
    page.controller.admission = _Admit()
    page.controller.service.admission = _Admit()
    page.resize(1400, 900)
    page.show()
    try:
        page.refresh()
        _wait(qt_app, lambda: page.models_table.rowCount() > 0)
        names = [page.models_table.item(row, 0).text()
                 for row in range(page.models_table.rowCount())]
        target = names.index("native-pro:latest")
        page.models_table.selectRow(target)
        page.trials.setValue(1)
        page._qualify()
        _wait(qt_app, lambda: not page._busy, timeout=120)
        _wait(qt_app, lambda: page.models_table.item(target, 2) is not None
              and page.models_table.item(target, 2).text() in {"qualified", "provisional"})
        assert "Failed attempts" in page.model_detail.toHtml() or page.models_table.item(
            target, 4).text() != "-"

        page.agent_name.setText("Scribe")
        page.agent_model.setCurrentText("native-pro:latest")
        _wait(qt_app, lambda: not page._busy)
        page._create_agent()
        _wait(qt_app, lambda: page.agents_table.rowCount() == 1)
        page.agents_table.selectRow(0)
        _wait(qt_app, lambda: not page._busy)
        page._activate()
        _wait(qt_app, lambda: page.run_agent.count() == 1)

        workspace = tmp_path / "run-ws"
        page.run_workspace.setText(str(workspace))
        page.run_task.setPlainText(
            "Create a file named status.txt containing the text ready-ABC. Then report it.")
        page.run_checks.setPlainText(
            '[{"kind": "file_contains", "path": "status.txt", "text": "ready-ABC"}]')
        page.tabs.setCurrentIndex(2)
        _wait(qt_app, lambda: not page._busy)
        page._run()
        _wait(qt_app, lambda: "verification:" in page.run_log.toPlainText(), timeout=120)
        assert "VERIFIED" in page.run_log.toPlainText()
        assert (workspace / "status.txt").read_text(encoding="utf-8").strip() == "ready-ABC"

        page.tabs.setCurrentIndex(3)
        page.memory_new.setText("Release builds are signed on the CI machine.")
        _wait(qt_app, lambda: not page._busy)
        page._add_memory()
        _wait(qt_app, lambda: page.memory_table.rowCount() == 1)
        assert "signed" in page.memory_table.item(0, 1).text()
    finally:
        page.close()
        page.deleteLater()
        qt_app.processEvents()
