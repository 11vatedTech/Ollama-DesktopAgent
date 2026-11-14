"""Agent-facing browser and computer tools.

The services start lazily on first use and are shut down by :meth:`close`.
Every call goes through the services, which authorize through the broker.
"""

from __future__ import annotations

import atexit
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ollama_workstation.agents.web import EgressPolicy
from ollama_workstation.interaction.policy import InteractionBroker, InteractionDenied

BROWSER_TOOLS = ("browser_open", "browser_snapshot", "browser_click", "browser_type",
                 "browser_select", "browser_scroll", "browser_back", "browser_wait",
                 "browser_downloads", "browser_upload")
COMPUTER_TOOLS = ("computer_apps", "computer_launch", "computer_windows", "computer_snapshot",
                  "computer_click", "computer_type", "computer_read", "computer_close")


def _function(name: str, description: str, properties: dict[str, Any],
              required: list[str]) -> dict[str, Any]:
    return {"type": "function", "function": {"name": name, "description": description,
            "parameters": {"type": "object", "properties": properties,
                           "required": required, "additionalProperties": False}}}


def browser_definitions(*, uploads: bool) -> list[dict[str, Any]]:
    ref = {"ref": {"type": "string"}}
    definitions = [
        _function("browser_open", "Open an https page in the isolated browser (JavaScript runs). "
                  "Returns page text and interactive elements as [eN] refs.",
                  {"url": {"type": "string"}}, ["url"]),
        _function("browser_snapshot", "Read the current page again: text and [eN] element refs.", {}, []),
        _function("browser_click", "Click a page element by its [eN] ref.", ref, ["ref"]),
        _function("browser_type", "Fill a text field by its [eN] ref; submit=true presses Enter.",
                  {"ref": {"type": "string"}, "text": {"type": "string"},
                   "submit": {"type": "boolean", "default": False}}, ["ref", "text"]),
        _function("browser_select", "Choose an option in a select element by its visible text.",
                  {"ref": {"type": "string"}, "option": {"type": "string"}}, ["ref", "option"]),
        _function("browser_scroll", "Scroll the page.",
                  {"direction": {"type": "string", "enum": ["down", "up"], "default": "down"}}, []),
        _function("browser_back", "Go back to the previous page.", {}, []),
        _function("browser_wait", "Wait for the page to update (max 5 seconds).",
                  {"seconds": {"type": "number", "default": 1}}, []),
        _function("browser_downloads", "List files the page downloaded (quarantined, never opened).",
                  {}, []),
    ]
    if uploads:
        definitions.append(_function(
            "browser_upload", "Attach a workspace file to a file input (needs confirmation).",
            {"ref": {"type": "string"}, "path": {"type": "string"}}, ["ref", "path"]))
    return definitions


def computer_definitions(apps: tuple[str, ...] = ()) -> list[dict[str, Any]]:
    app_schema: dict[str, Any] = {"type": "string"}
    if apps:
        app_schema["enum"] = sorted(apps)
    return [
        _function("computer_apps", "List the desktop applications this agent may launch.", {}, []),
        _function("computer_launch", "Launch a granted application by its key"
                  + (f" ({', '.join(sorted(apps))})" if apps else "")
                  + ". Returns its window ref [wN] and its controls as [wN.cM] refs.",
                  {"app": app_schema}, ["app"]),
        _function("computer_windows", "List the windows this agent launched.", {}, []),
        _function("computer_snapshot", "Read a window's controls again ([wN.cM] refs, values).",
                  {"window": {"type": "string"}}, ["window"]),
        _function("computer_click", "Press/activate a control by its [wN.cM] ref.",
                  {"ref": {"type": "string"}}, ["ref"]),
        _function("computer_type", "Set the text of an editable control by its [wN.cM] ref.",
                  {"ref": {"type": "string"}, "text": {"type": "string"}}, ["ref", "text"]),
        _function("computer_read", "Read the full value or text of a control.",
                  {"ref": {"type": "string"}}, ["ref"]),
        _function("computer_close", "Close a window this agent launched.",
                  {"window": {"type": "string"}}, ["window"]),
    ]


class InteractionTools:
    """Browser and computer tools bound to one broker (one agent session)."""

    def __init__(self, broker: InteractionBroker, *, egress: EgressPolicy | None = None,
                 resolve_path: Callable[[str], Path] | None = None,
                 cancel_event: threading.Event | None = None,
                 browser_factory: Callable[[], Any] | None = None,
                 computer_factory: Callable[[], Any] | None = None) -> None:
        self.broker = broker
        self.egress = egress or EgressPolicy(enabled=broker.grants.browser,
                                             approval=broker.approval or (lambda _d: False),
                                             private_hosts=broker.grants.private_hosts)
        self.resolve_path = resolve_path
        self.cancel_event = cancel_event
        self._browser_factory = browser_factory
        self._computer_factory = computer_factory
        self._browser: Any = None
        self._computer: Any = None
        self._lock = threading.Lock()
        atexit.register(self.close)

    def names(self) -> set[str]:
        names: set[str] = set()
        if self.broker.grants.browser:
            names.update(BROWSER_TOOLS if self.broker.grants.allow_uploads
                         else BROWSER_TOOLS[:-1])
        if self.broker.grants.computer:
            names.update(COMPUTER_TOOLS)
        return names

    def definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        if self.broker.grants.browser:
            definitions += browser_definitions(uploads=self.broker.grants.allow_uploads)
        if self.broker.grants.computer:
            definitions += computer_definitions(self.broker.grants.computer_apps)
        return definitions

    # -- services ---------------------------------------------------------------------------
    def browser(self) -> Any:
        with self._lock:
            if self._browser is None:
                if self._browser_factory is not None:
                    self._browser = self._browser_factory()
                else:
                    from ollama_workstation.interaction.browser import BrowserSession

                    self._browser = BrowserSession(self.broker, self.egress)
            return self._browser

    def computer(self) -> Any:
        with self._lock:
            if self._computer is None:
                if self._computer_factory is not None:
                    self._computer = self._computer_factory()
                else:
                    from ollama_workstation.interaction.computer import ComputerSession

                    self._computer = ComputerSession(self.broker)
            return self._computer

    def close(self) -> None:
        with self._lock:
            for service in (self._browser, self._computer):
                if service is not None:
                    try:
                        service.close()
                    except Exception:  # noqa: BLE001 - shutdown is best effort
                        pass
            self._browser = self._computer = None

    # -- dispatch -----------------------------------------------------------------------------
    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.names():
            raise InteractionDenied(f"Tool {name} is not granted to this agent")
        cancel = self.cancel_event
        if cancel is not None and cancel.is_set():
            raise InteractionDenied("The run was cancelled")
        if name.startswith("browser_"):
            browser = self.browser()
            if name == "browser_open":
                return dict(browser.open(str(arguments["url"]), cancel))
            if name == "browser_snapshot":
                return dict(browser.snapshot(cancel))
            if name == "browser_click":
                return dict(browser.click(str(arguments["ref"]), cancel))
            if name == "browser_type":
                return dict(browser.type(str(arguments["ref"]), str(arguments["text"]),
                                         bool(arguments.get("submit", False)), cancel))
            if name == "browser_select":
                return dict(browser.select(str(arguments["ref"]), str(arguments["option"]), cancel))
            if name == "browser_scroll":
                return dict(browser.scroll(str(arguments.get("direction", "down")), cancel))
            if name == "browser_back":
                return dict(browser.back(cancel))
            if name == "browser_wait":
                return dict(browser.wait(float(arguments.get("seconds", 1)), cancel))
            if name == "browser_downloads":
                return {"downloads": list(browser.downloads),
                        "note": "Downloads are quarantined and never opened by the agent."}
            if name == "browser_upload":
                if self.resolve_path is None:
                    raise InteractionDenied("Uploads need a workspace")
                return dict(browser.upload(str(arguments["ref"]),
                                           self.resolve_path(str(arguments["path"])), cancel))
        computer = self.computer()
        if name == "computer_apps":
            return {"apps": computer.available_apps()}
        if name == "computer_launch":
            return dict(computer.launch(str(arguments["app"]), cancel))
        if name == "computer_windows":
            return {"windows": computer.windows()}
        if name == "computer_snapshot":
            return dict(computer.snapshot(str(arguments["window"]), cancel))
        if name == "computer_click":
            return dict(computer.click(str(arguments["ref"]), cancel))
        if name == "computer_type":
            return dict(computer.type(str(arguments["ref"]), str(arguments["text"]), cancel))
        if name == "computer_read":
            return dict(computer.read(str(arguments["ref"]), cancel))
        if name == "computer_close":
            return dict(computer.close_window(str(arguments["window"]), cancel))
        raise InteractionDenied(f"Unknown interaction tool {name}")
