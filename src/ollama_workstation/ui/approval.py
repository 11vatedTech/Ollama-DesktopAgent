"""Ask the person at the desktop before an agent overwrites, edits or runs something.

Agent runs execute on a worker thread; a dialog must run on the UI thread. The worker
calls :meth:`ApprovalBridge.ask`, which posts the question to the UI thread and waits.
Cancelling the run, closing the dialog, or no answer within the timeout counts as a
refusal, so an unattended prompt can never turn into an implicit "yes".

Before this existed, chat agent mode gave its tools no approval callback and every
overwrite was refused silently (rendered QA, session 9).
"""

from __future__ import annotations

from collections.abc import Callable
from threading import Event, Lock

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtWidgets import QMessageBox, QWidget

ALLOW_ONCE = "once"
ALLOW_RUN = "run"
DENY = "deny"
DEFAULT_TIMEOUT_SECONDS = 600.0

Prompter = Callable[[str], str]


def dialog_prompter(parent: QWidget | None) -> Prompter:
    def prompt(description: str) -> str:
        box = QMessageBox(parent)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle("Agent approval")
        box.setText("The agent asks to do this:")
        box.setInformativeText(description)
        once = box.addButton("Allow once", QMessageBox.ButtonRole.AcceptRole)
        run = box.addButton("Allow for this run", QMessageBox.ButtonRole.YesRole)
        deny = box.addButton("Deny", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(deny)
        box.setEscapeButton(deny)
        box.exec()
        clicked = box.clickedButton()
        return ALLOW_ONCE if clicked is once else ALLOW_RUN if clicked is run else DENY

    return prompt


class ApprovalBridge(QObject):
    """One bridge per agent run; create it on the UI thread."""

    _requested = Signal(str, object)

    def __init__(self, prompter: Prompter, *, cancel_event: Event | None = None,
                 timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS) -> None:
        super().__init__()
        self._prompter = prompter
        self._cancel_event = cancel_event
        self._timeout = max(1.0, float(timeout_seconds))
        self._allow_all = False
        self._denied: set[str] = set()
        self._lock = Lock()
        self.decisions: list[tuple[str, str]] = []  # (description, decision) for the trace
        self._requested.connect(self._prompt)

    def ask(self, description: str) -> bool:
        """Called from the worker thread; blocks until answered, cancelled or timed out."""
        with self._lock:
            if self._allow_all:
                self.decisions.append((description, ALLOW_RUN))
                return True
            # A "no" stands for the rest of the run: a model that retries a refused
            # request must not make the person answer it again (live, session 9: the
            # same denied edit was asked four times).
            if description in self._denied:
                self.decisions.append((description, DENY))
                return False
        reply: dict[str, object] = {"done": Event(), "decision": DENY}
        self._requested.emit(description, reply)
        done = reply["done"]
        assert isinstance(done, Event)
        waited = 0.0
        while not done.wait(0.1):
            waited += 0.1
            if (self._cancel_event is not None and self._cancel_event.is_set()) \
                    or waited >= self._timeout:
                self.decisions.append((description, DENY))
                return False
        decision = str(reply["decision"])
        with self._lock:
            if decision == ALLOW_RUN:
                self._allow_all = True
            elif decision not in {ALLOW_ONCE, ALLOW_RUN}:
                self._denied.add(description)
            self.decisions.append((description, decision))
        return decision in {ALLOW_ONCE, ALLOW_RUN}

    @Slot(str, object)
    def _prompt(self, description: str, reply: object) -> None:
        assert isinstance(reply, dict)
        try:
            reply["decision"] = self._prompter(description)
        except Exception:  # noqa: BLE001 - a broken prompt must refuse, not crash the UI
            reply["decision"] = DENY
        finally:
            done = reply["done"]
            assert isinstance(done, Event)
            done.set()
