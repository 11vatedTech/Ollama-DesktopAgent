"""Focused desktop UI checks that run headlessly on the Windows CI runner."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt, QThreadPool  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QFrame,
    QHBoxLayout,
    QMainWindow,
    QStackedWidget,
    QWidget,
)

from ollama_workstation.agents.execution_lease import WorkspaceAdmission  # noqa: E402
from ollama_workstation.agents.memory import MemoryStore  # noqa: E402
from ollama_workstation.agents.recovery import RecoveryStatus  # noqa: E402
from ollama_workstation.config import AppPaths, Settings  # noqa: E402
from ollama_workstation.ollama.client import OllamaClient  # noqa: E402
from ollama_workstation.profiling.profiler import ModelProfiler  # noqa: E402
from ollama_workstation.storage.repository import Repository  # noqa: E402
from ollama_workstation.ui.components import PageHeader, button  # noqa: E402
from ollama_workstation.ui.formatting import conversation_to_markdown, render_markdown  # noqa: E402
from ollama_workstation.ui.intelligence_core import (  # noqa: E402
    STATE_TRANSITIONS,
    IntelligenceCore,
    IntelligenceState,
    coerce_state,
    validate_transition,
)
from ollama_workstation.ui.pages.agents import AgentFoundryPage  # noqa: E402
from ollama_workstation.ui.pages.chat import ChatPage  # noqa: E402
from ollama_workstation.ui.pages.command_center import CommandCenterPage  # noqa: E402
from ollama_workstation.ui.pages.history import HistoryPage  # noqa: E402
from ollama_workstation.ui.pages.integrations import IntegrationHubPage  # noqa: E402
from ollama_workstation.ui.pages.laboratory import LaboratoryPage  # noqa: E402
from ollama_workstation.ui.pages.model_center import ModelCenterPage  # noqa: E402
from ollama_workstation.ui.pages.operations import OperationsPage  # noqa: E402
from ollama_workstation.ui.pages.settings import SettingsPage  # noqa: E402
from ollama_workstation.ui.pages.workforce import WorkforcePage  # noqa: E402
from ollama_workstation.ui.state import ActivityRegistry, AppContext, EventBus  # noqa: E402


@pytest.fixture(scope="session")
def qt_app() -> QApplication:
    app = QApplication.instance()
    return app if isinstance(app, QApplication) else QApplication([])


@pytest.fixture(autouse=True)
def _cleanup_widgets(qt_app: QApplication):
    yield
    for widget in QApplication.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    qt_app.processEvents()


@pytest.fixture()
def context(tmp_path: Path) -> AppContext:
    root = tmp_path / "app"
    paths = AppPaths(
        root=root,
        config_file=root / "settings.json",
        database_file=root / "state.sqlite3",
        workspaces_dir=root / "workspaces",
        profiles_dir=root / "profiles",
        logs_dir=root / "logs",
    )
    paths.ensure()
    settings = Settings(workspace_root=str(paths.workspaces_dir))
    client = OllamaClient("http://127.0.0.1:9", timeout=0.05)
    repository = Repository(paths.database_file)
    return AppContext(
        paths=paths,
        settings=settings,
        client=client,
        profiler=ModelProfiler(client, settings.keep_alive),
        repository=repository,
        memory_store=MemoryStore(repository),
        pool=QThreadPool(),
        events=EventBus(),
        activity=ActivityRegistry(),
    )


def _dispose(page: QWidget, app: QApplication) -> None:
    page.close()
    page.deleteLater()
    app.processEvents()


def _desktop_host(page: QWidget, app: QApplication) -> tuple[QMainWindow, QStackedWidget]:
    """Mirror the shell's 236 px sidebar so page widths match the desktop app."""
    host = QMainWindow()
    central = QWidget()
    layout = QHBoxLayout(central)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    sidebar = QFrame()
    sidebar.setFixedWidth(236)
    stack = QStackedWidget()
    stack.addWidget(page)
    layout.addWidget(sidebar)
    layout.addWidget(stack, 1)
    host.setCentralWidget(central)
    host.setMinimumSize(1120, 720)
    host.resize(1480, 940)
    host.show()
    QTest.qWait(30)
    app.processEvents()
    return host, stack


def test_history_disables_resume_for_a_foreign_workspace_admission(
    qt_app: QApplication, context: AppContext
) -> None:
    workspace = context.paths.workspaces_dir / "held-workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).isoformat()
    context.repository.save_run_checkpoint(
        run_id="desktop-held-run",
        model_name="desktop:latest",
        model_digest="sha256:desktop",
        agent_title="Repository Cartographer",
        workspace=str(workspace.resolve()),
        harness_mode="native_tools",
        tool_manifest_sha256="manifest",
        state="ready",
        step=0,
        tool_calls=0,
        payload={
            "protocol": 2,
            "task": "Inspect the workspace",
            "messages": [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "user"},
            ],
            "pending_operations": [],
            "step_budget": 12,
            "resume_count": 0,
        },
        created_at=now,
        updated_at=now,
    )
    page = HistoryPage(context)
    try:
        with WorkspaceAdmission(
            context.repository, workspace, "holder-run", "holder-owner", ttl_seconds=60
        ):
            page.refresh()
            assert page._candidates[0].status is RecoveryStatus.WORKSPACE_BUSY
            assert page._candidates[0].workspace_run_id == "holder-run"
            assert not page.resume_button.isEnabled()
    finally:
        _dispose(page, qt_app)


def test_every_workspace_constructs_with_empty_local_state(qt_app: QApplication, context: AppContext) -> None:
    pages = [CommandCenterPage, ChatPage, ModelCenterPage, AgentFoundryPage, WorkforcePage,
             OperationsPage, IntegrationHubPage, LaboratoryPage, HistoryPage, SettingsPage]
    for page_type in pages:
        page = page_type(context)
        assert isinstance(page, QWidget)
        assert page.layout() is not None
        _dispose(page, qt_app)


def test_command_center_reflows_between_desktop_and_compact_widths(
    qt_app: QApplication, context: AppContext
) -> None:
    page = CommandCenterPage(context)
    page.resize(1244, 800)
    page.show()
    QTest.qWait(20)
    assert page._hero_narrow is False
    assert page._telemetry_columns == 5
    assert page._lower_columns == 3

    page.resize(900, 760)
    QTest.qWait(20)
    assert page._hero_narrow is True
    assert page._telemetry_columns == 3
    assert page._lower_columns == 2
    assert page.core.maximumWidth() <= 320
    assert page.hero_copy_widget.width() <= page.width()
    _dispose(page, qt_app)


def test_model_intelligence_compact_split_is_vertical_and_fits_width(
    qt_app: QApplication, context: AppContext
) -> None:
    page = ModelCenterPage(context)
    page.setFixedSize(692, 700)
    page.show()
    QTest.qWait(30)
    assert page.split.orientation() == Qt.Orientation.Vertical
    assert page.detail_scroll.width() <= page.width()
    assert page.detail_scroll.viewport().width() <= page.width()
    assert page.model_list.minimumHeight() == 96
    _dispose(page, qt_app)


def test_chat_context_panel_follows_available_width(qt_app: QApplication, context: AppContext) -> None:
    page = ChatPage(context)
    host, _stack = _desktop_host(page, qt_app)
    try:
        assert page.width() >= 1200
        assert not page.context_panel.isHidden()
        assert page.context_button.text() == "Hide context"

        host.resize(1120, 720)
        QTest.qWait(40)
        assert page.width() < 1030
        assert page.context_panel.isHidden()
        assert page.context_button.text() == "Show context"

        host.resize(1480, 940)
        QTest.qWait(40)
        assert page.width() >= 1200
        assert not page.context_panel.isHidden()
        assert page.context_button.text() == "Hide context"
    finally:
        host.close()
        host.deleteLater()
        qt_app.processEvents()


def test_intelligence_core_renders_every_state_and_honors_reduced_motion(
    qt_app: QApplication,
) -> None:
    core = IntelligenceCore(caption=False)
    core.resize(240, 240)
    for state in IntelligenceState:
        core.set_state(state, "test boundary")
        assert core.state is state
        image = QImage(240, 240, QImage.Format.Format_ARGB32)
        image.fill(0)
        core.render(image)
        assert not image.isNull()

    core.set_motion(reduced=True, intensity=0.3)
    assert core._timer.interval() == 750
    core.set_motion(reduced=False, intensity=0.9)
    assert core._timer.interval() == 16
    assert coerce_state("executing") is IntelligenceState.TOOL_EXECUTION
    assert coerce_state("delegating") is IntelligenceState.WORKFORCE_DELEGATION
    assert validate_transition(IntelligenceState.STANDBY, IntelligenceState.VERIFYING)
    assert IntelligenceState.GENERATING in STATE_TRANSITIONS[IntelligenceState.THINKING]
    core.close()


def test_page_header_stacks_actions_instead_of_clipping() -> None:
    header = PageHeader(
        "test", "Responsive heading", "A subtitle that remains readable.",
        actions=[button("Primary action"), button("Secondary action"), button("More")],
    )
    header.resize(1200, 120)
    header.show()
    QApplication.processEvents()
    assert not header._stacked
    header.resize(700, 180)
    QApplication.processEvents()
    assert header._stacked
    header.close()


def test_conversation_export_is_escaped_and_preserves_artifact_refs() -> None:
    rendered = conversation_to_markdown(
        "Test session",
        [
            {"role": "user", "content": "Inspect <unsafe>"},
            {"role": "assistant", "content": "Created result", "status": "completed",
             "artifacts": ["results/out.md"]},
        ],
        model="qwen2.5-coder:7b",
    )
    assert "# Test session" in rendered
    assert "## User" in rendered
    assert "Inspect <unsafe>" in rendered
    assert "results/out.md" in rendered
    assert "qwen2.5-coder:7b" in rendered


def test_markdown_escapes_untrusted_html_and_never_emits_javascript_links() -> None:
    rendered = render_markdown(
        "<script>alert('x')</script> [unsafe](javascript:alert(1))\n\n"
        "```py\nprint('safe')\n```"
    )
    assert "<script>" not in rendered
    assert "&lt;script&gt;" in rendered
    assert 'href="javascript' not in rendered
    assert "<pre" in rendered
    assert ">PY<" in rendered


def test_conversation_repository_list_get_and_delete(tmp_path: Path) -> None:
    repository = Repository(tmp_path / "conversations.sqlite3")
    messages = [{"role": "user", "content": "Inspect the local workspace."}]
    repository.save_conversation(
        "conv-1", "qwen2.5-coder:7b", "Workspace inspection",
        "2026-11-11T10:00:00+00:00", messages,
    )
    repository.save_conversation(
        "conv-2", "qwen2.5-coder:7b", "Newer conversation",
        "2026-11-11T10:05:00+00:00", [{"role": "user", "content": "Second"}],
    )
    listed = repository.list_conversations()
    assert [item["conversation_id"] for item in listed] == ["conv-2", "conv-1"]
    assert listed[1]["preview"] == "Inspect the local workspace."
    assert listed[1]["message_count"] == 1
    loaded = repository.get_conversation("conv-1")
    assert loaded is not None
    assert loaded["messages"] == messages
    assert repository.delete_conversation("conv-1") is True
    assert repository.get_conversation("conv-1") is None
    assert repository.delete_conversation("conv-1") is False


def test_settings_shows_effective_numeric_and_workspace_values(
    qt_app: QApplication, context: AppContext
) -> None:
    page = SettingsPage(context)
    assert page.timeout.text() == "120"
    assert page.max_steps.text() == "12"
    assert page.workspace.text() == str(context.workspace_path())
    _dispose(page, qt_app)
