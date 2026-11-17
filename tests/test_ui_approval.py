"""Desktop approvals for agent runs: asked on the UI thread, waited for on the worker.

Session 9 rendered QA: chat agent mode gave its tools no approval callback, so every
overwrite or edit was refused silently.
"""

from __future__ import annotations

import os
import time
from threading import Event, Thread

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
QtWidgets = pytest.importorskip("PySide6.QtWidgets")

from ollama_workstation.ui.approval import (  # noqa: E402
    ALLOW_ONCE,
    ALLOW_RUN,
    DENY,
    ApprovalBridge,
)


@pytest.fixture(scope="module")
def app():
    return QtWidgets.QApplication.instance() or QtWidgets.QApplication([])


def _ask_from_worker(app, bridge: ApprovalBridge, descriptions: list[str],
                     limit: float = 5.0) -> list[bool]:
    answers: list[bool] = []
    worker = Thread(target=lambda: answers.extend(bridge.ask(item) for item in descriptions))
    worker.start()
    deadline = time.monotonic() + limit
    while worker.is_alive() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.01)
    worker.join(1)
    assert not worker.is_alive(), "worker still waiting"
    return answers


def test_answers_cross_threads_and_allow_once_asks_again(app) -> None:
    asked: list[str] = []
    bridge = ApprovalBridge(lambda text: asked.append(text) or ALLOW_ONCE)
    assert _ask_from_worker(app, bridge, ["Overwrite a.py", "Overwrite b.py"]) == [True, True]
    assert asked == ["Overwrite a.py", "Overwrite b.py"]


def test_allow_for_this_run_stops_asking(app) -> None:
    asked: list[str] = []
    bridge = ApprovalBridge(lambda text: asked.append(text) or ALLOW_RUN)
    assert _ask_from_worker(app, bridge, ["one", "two", "three"]) == [True, True, True]
    assert asked == ["one"]
    assert [decision for _, decision in bridge.decisions] == [ALLOW_RUN] * 3


def test_deny_and_a_broken_prompt_both_refuse(app) -> None:
    assert _ask_from_worker(app, ApprovalBridge(lambda _t: DENY), ["x"]) == [False]

    def broken(_text: str) -> str:
        raise RuntimeError("dialog failed")

    assert _ask_from_worker(app, ApprovalBridge(broken), ["x"]) == [False]


def test_cancel_or_timeout_without_an_answer_refuses(app) -> None:
    cancel = Event()
    # A prompt that never answers: the UI thread is not pumped for it.
    silent = ApprovalBridge(lambda _t: ALLOW_ONCE, cancel_event=cancel)
    result: list[bool] = []
    worker = Thread(target=lambda: result.append(silent.ask("Overwrite x")))
    worker.start()
    time.sleep(0.3)
    cancel.set()
    worker.join(2)
    assert result == [False]
    timed = ApprovalBridge(lambda _t: ALLOW_ONCE, timeout_seconds=1.0)
    late: list[bool] = []
    started = time.monotonic()
    waiting = Thread(target=lambda: late.append(timed.ask("Overwrite y")))
    waiting.start()
    waiting.join(3)  # the UI thread is not pumped, so no answer can arrive
    assert late == [False]
    assert time.monotonic() - started < 3.0


def test_on_the_ui_thread_the_prompt_runs_directly_without_deadlock(app) -> None:
    assert ApprovalBridge(lambda _t: ALLOW_ONCE).ask("Overwrite z") is True


def test_a_denial_stands_for_the_run_without_asking_again(app) -> None:
    """Live session 9: a model retried a denied edit and the person was asked four times."""
    asked: list[str] = []
    bridge = ApprovalBridge(lambda text: asked.append(text) or DENY)
    answers = _ask_from_worker(app, bridge, ["Overwrite a.py", "Overwrite a.py", "Overwrite b.py"])
    assert answers == [False, False, False]
    assert asked == ["Overwrite a.py", "Overwrite b.py"]
