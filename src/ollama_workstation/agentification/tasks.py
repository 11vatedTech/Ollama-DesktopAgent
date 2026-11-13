"""Declarative, serializable agent task specifications.

A task is data: workspace files, an objective, optional induced tool faults and
host-authored verification checks. Values are generated per trial from a seed,
so a model cannot pass by memorising an answer, and every trial is reproducible.
The same format is used by the harness qualification suite, by skill test cases
and by the acceptance scenarios.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ollama_workstation.agentification.verifier import validate_checks
from ollama_workstation.agents.tools import ToolError

_TOKEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


DEFAULT_FAULT_ERROR = "Temporary host fault. The operation did not run; retry the same call."


class TaskSpecError(ValueError):
    """A task specification is malformed."""


@dataclass(frozen=True, slots=True)
class ToolFault:
    """Fail the first ``times`` calls of ``tool`` with a recoverable host error."""

    tool: str
    times: int = 1
    error: str = DEFAULT_FAULT_ERROR

    def to_dict(self) -> dict[str, Any]:
        return {"tool": self.tool, "times": self.times, "error": self.error}


@dataclass(frozen=True, slots=True)
class TaskSpec:
    task_id: str
    category: str
    objective: str
    files: dict[str, str] = field(default_factory=dict)
    checks: tuple[dict[str, Any], ...] = ()
    variables: dict[str, dict[str, Any]] = field(default_factory=dict)
    faults: tuple[ToolFault, ...] = ()
    max_steps: int = 10
    requires_tools: tuple[str, ...] = ()
    # Pages served by the offline qualification web (path -> HTML); see
    # harness.web_tool_factory. Never written into the workspace.
    web_pages: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id or not self.objective.strip():
            raise TaskSpecError("A task needs an id and an objective")
        if not 1 <= self.max_steps <= 40:
            raise TaskSpecError("max_steps must be between 1 and 40")
        for relative in self.files:
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts:
                raise TaskSpecError(f"Task file must stay inside the workspace: {relative}")
        for name, rule in self.variables.items():
            if not isinstance(rule, dict) or rule.get("kind") not in {
                "token", "int", "choice", "sum", "when",
            }:
                raise TaskSpecError(f"Variable {name!r} has an unsupported generator")
        validate_checks(self.checks)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id, "category": self.category, "objective": self.objective,
            "files": dict(self.files), "checks": [dict(item) for item in self.checks],
            "variables": {key: dict(value) for key, value in self.variables.items()},
            "faults": [item.to_dict() for item in self.faults],
            "max_steps": self.max_steps, "requires_tools": list(self.requires_tools),
            # Omitted when empty so the fingerprints of existing tasks are unchanged.
            **({"web_pages": dict(self.web_pages)} if self.web_pages else {}),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> TaskSpec:
        try:
            return cls(
                task_id=str(payload["task_id"]), category=str(payload.get("category") or "general"),
                objective=str(payload["objective"]),
                files={str(k): str(v) for k, v in dict(payload.get("files") or {}).items()},
                checks=tuple(dict(item) for item in payload.get("checks") or ()),
                variables={str(k): dict(v) for k, v in dict(payload.get("variables") or {}).items()},
                faults=tuple(ToolFault(str(item["tool"]), int(item.get("times", 1)),
                                       str(item.get("error") or DEFAULT_FAULT_ERROR))
                             for item in payload.get("faults") or ()),
                max_steps=int(payload.get("max_steps", 10)),
                requires_tools=tuple(str(item) for item in payload.get("requires_tools") or ()),
                web_pages={str(k): str(v) for k, v in dict(payload.get("web_pages") or {}).items()},
            )
        except (KeyError, TypeError, AttributeError) as exc:
            raise TaskSpecError(f"Invalid task specification: {exc}") from exc

    def fingerprint(self) -> str:
        return hashlib.sha256(
            json.dumps(self.to_dict(), sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()


@dataclass(frozen=True, slots=True)
class TaskInstance:
    """One concrete, seeded trial of a task."""

    spec: TaskSpec
    seed: int
    values: dict[str, Any]
    objective: str
    files: dict[str, str]
    checks: tuple[dict[str, Any], ...]
    web_pages: dict[str, str] = field(default_factory=dict)

    def materialize(self, workspace: Path) -> None:
        workspace.mkdir(parents=True, exist_ok=True)
        for relative, content in self.files.items():
            target = workspace / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")


def _generate(variables: dict[str, dict[str, Any]], rng: random.Random) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for name, rule in variables.items():
        kind = rule["kind"]
        if kind == "token":
            length = max(4, min(int(rule.get("length", 6)), 24))
            values[name] = str(rule.get("prefix", "")) + "".join(
                rng.choice(_TOKEN_ALPHABET) for _ in range(length)
            )
        elif kind == "int":
            values[name] = rng.randint(int(rule.get("min", 1)), int(rule.get("max", 99)))
        elif kind == "choice":
            options = list(rule.get("values") or [])
            if not options:
                raise TaskSpecError(f"Variable {name!r} has no choices")
            values[name] = rng.choice(options)
        elif kind == "sum":
            sources = rule["of"] if isinstance(rule["of"], list) else [rule["of"]]
            values[name] = sum(int(values[str(source)]) for source in sources) + int(
                rule.get("add", 0))
        else:  # when
            matched = values.get(str(rule["var"])) == rule.get("equals")
            values[name] = _render(rule.get("then" if matched else "else", ""), values)
    return values


def _render(template: Any, values: dict[str, Any]) -> Any:
    """Substitute ``{name}`` placeholders; a string that is exactly one placeholder
    keeps the generated value's type so JSON comparisons stay exact."""
    if isinstance(template, str):
        for name, value in values.items():
            if template == "{" + name + "}":
                return value
        rendered = template
        for name, value in values.items():
            rendered = rendered.replace("{" + name + "}", str(value))
        return rendered
    if isinstance(template, dict):
        return {key: _render(value, values) for key, value in template.items()}
    if isinstance(template, (list, tuple)):
        return [_render(item, values) for item in template]
    return template


def instantiate(spec: TaskSpec, seed: int) -> TaskInstance:
    rng = random.Random(f"{spec.task_id}:{seed}")
    values = _generate(spec.variables, rng)
    return TaskInstance(
        spec=spec, seed=seed, values=values,
        objective=str(_render(spec.objective, values)),
        files={str(_render(path, values)): str(_render(content, values))
               for path, content in spec.files.items()},
        checks=tuple(_render(dict(check), values) for check in spec.checks),
        web_pages={str(_render(path, values)): str(_render(content, values))
                   for path, content in spec.web_pages.items()},
    )


class FaultInjectingTools:
    """Registry wrapper that induces bounded, recoverable tool failures.

    The underlying tool is *not* executed for an induced failure, so a correct
    recovery is one retry and no duplicate side effect is possible.
    """

    def __init__(self, inner: Any, faults: tuple[ToolFault, ...]) -> None:
        self._inner = inner
        self._remaining = {fault.tool: max(0, int(fault.times)) for fault in faults}
        self._errors = {fault.tool: fault.error for fault in faults}
        self.induced: list[str] = []

    @property
    def workspace(self) -> Path:
        return Path(self._inner.workspace)

    def definitions(self) -> list[dict[str, Any]]:
        return list(self._inner.definitions())

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if self._remaining.get(name, 0) > 0:
            self._remaining[name] -= 1
            self.induced.append(name)
            raise ToolError(self._errors[name])
        return dict(self._inner.invoke(name, arguments))
