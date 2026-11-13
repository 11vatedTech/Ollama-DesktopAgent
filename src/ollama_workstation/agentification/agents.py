"""Versioned agent definitions and the gate that keeps unqualified agents out of use.

An agent is a reproducible configuration: an exact model artifact, the harness
that artifact qualified for, a role, a granted tool set, permissions, pinned
skills, memory and budget policies. Editing an agent creates a new version; the
active version and its evidence stay untouched until the new version passes the
same readiness gate.
"""

from __future__ import annotations

import re
import sys
import tempfile
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from pathlib import Path
from threading import Event
from typing import Any

from ollama_workstation.agentification.harness import (
    HarnessLab,
    HarnessQualification,
    qualification_issues,
)
from ollama_workstation.agentification.skills import SkillError, SkillRegistry, render_skills
from ollama_workstation.agentification.store import AgentStore, canonical_sha256
from ollama_workstation.agentification.verifier import (
    VerificationEngine,
    VerificationReport,
    snapshot_workspace,
)
from ollama_workstation.agents.memory import memory_store_for
from ollama_workstation.agents.runtime import (
    HARNESS_MODES,
    AgentEvent,
    AgentResult,
    AgentRuntime,
    RunPolicy,
    tool_manifest_sha256,
)
from ollama_workstation.agents.tools import ToolError, WorkspaceTools
from ollama_workstation.models.identity import ROLE_DEFINITIONS
from ollama_workstation.models.schemas import ModelProfile, ModelRef, utc_now

AGENT_SCHEMA_VERSION = "1.0"
WORKSPACE_TOOLS = ("list_files", "read_file", "search_text", "write_file")
ROLE_KEYS = frozenset(definition.key for definition in ROLE_DEFINITIONS)
_NAME_PATTERN = re.compile(r"^[\w][\w .:@()+-]{1,79}$")


class AgentConfigError(ValueError):
    """An agent definition is structurally invalid."""


class AgentNotReady(RuntimeError):
    """The agent failed the readiness gate. ``issues`` lists every blocking reason."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("Agent is not ready: " + "; ".join(issues))
        self.issues = list(issues)


@dataclass(frozen=True, slots=True)
class Permissions:
    write: bool = True
    shell: bool = False
    allowed_commands: tuple[str, ...] = ()
    sensitive_files: bool = False
    # "ask" defers to the host approval callback; "deny" refuses without asking.
    overwrite: str = "ask"
    command: str = "ask"
    # Read-only git inspection tools (git_status, git_diff, git_log).
    git: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = {"write": self.write, "shell": self.shell,
                   "allowed_commands": list(self.allowed_commands),
                   "sensitive_files": self.sensitive_files,
                   "overwrite": self.overwrite, "command": self.command}
        if self.git:  # omitted when off so existing agent digests are unchanged
            payload["git"] = True
        return payload


@dataclass(frozen=True, slots=True)
class Budget:
    max_steps: int = 12
    max_seconds: float = 900.0
    max_tokens: int | None = None
    max_tool_calls: int = 48

    def to_dict(self) -> dict[str, Any]:
        return {"max_steps": self.max_steps, "max_seconds": self.max_seconds,
                "max_tokens": self.max_tokens, "max_tool_calls": self.max_tool_calls}


@dataclass(frozen=True, slots=True)
class AgentDefinition:
    agent_id: str
    version: int
    name: str
    model_name: str
    model_digest: str
    role: str
    role_title: str
    instructions: str = ""
    harness: str | None = None  # None: use the harness the qualification selected
    qualification_id: str | None = None
    permissions: Permissions = field(default_factory=Permissions)
    budget: Budget = field(default_factory=Budget)
    skills: tuple[dict[str, Any], ...] = ()
    memory: dict[str, Any] = field(default_factory=lambda: {"enabled": False})
    context: dict[str, Any] = field(default_factory=dict)
    recovery: dict[str, Any] = field(
        default_factory=lambda: {"resume_safe_boundaries": True, "replay_interrupted_tools": False})
    verification: dict[str, Any] = field(default_factory=lambda: {"require_checks": False})
    allow_provisional: bool = False
    repair_attempts: int = 2
    created_at: str = field(default_factory=utc_now)
    parent_version: int | None = None
    schema_version: str = AGENT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version, "agent_id": self.agent_id,
            "version": self.version, "name": self.name, "model_name": self.model_name,
            "model_digest": self.model_digest, "role": self.role, "role_title": self.role_title,
            "instructions": self.instructions, "harness": self.harness,
            "qualification_id": self.qualification_id,
            "permissions": self.permissions.to_dict(), "budget": self.budget.to_dict(),
            "skills": [dict(item) for item in self.skills], "memory": dict(self.memory),
            "context": dict(self.context), "recovery": dict(self.recovery),
            "verification": dict(self.verification),
            "allow_provisional": self.allow_provisional,
            "repair_attempts": self.repair_attempts, "created_at": self.created_at,
            "parent_version": self.parent_version,
        }

    @property
    def config_sha256(self) -> str:
        return canonical_sha256(self.to_dict())

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> AgentDefinition:
        try:
            permissions = dict(payload.get("permissions") or {})
            budget = dict(payload.get("budget") or {})
            definition = cls(
                agent_id=str(payload["agent_id"]), version=int(payload["version"]),
                name=str(payload["name"]), model_name=str(payload["model_name"]),
                model_digest=str(payload["model_digest"]), role=str(payload["role"]),
                role_title=str(payload.get("role_title") or ""),
                instructions=str(payload.get("instructions") or ""),
                harness=payload.get("harness"), qualification_id=payload.get("qualification_id"),
                permissions=Permissions(
                    write=bool(permissions.get("write", True)),
                    shell=bool(permissions.get("shell", False)),
                    allowed_commands=tuple(str(x) for x in permissions.get("allowed_commands") or ()),
                    sensitive_files=bool(permissions.get("sensitive_files", False)),
                    overwrite=str(permissions.get("overwrite", "ask")),
                    command=str(permissions.get("command", "ask")),
                    git=bool(permissions.get("git", False)),
                ),
                budget=Budget(
                    max_steps=int(budget.get("max_steps", 12)),
                    max_seconds=float(budget.get("max_seconds", 900.0)),
                    max_tokens=(None if budget.get("max_tokens") is None
                                else int(budget["max_tokens"])),
                    max_tool_calls=int(budget.get("max_tool_calls", 48)),
                ),
                skills=tuple(dict(item) for item in payload.get("skills") or ()),
                memory=dict(payload.get("memory") or {"enabled": False}),
                context=dict(payload.get("context") or {}),
                recovery=dict(payload.get("recovery") or {}),
                verification=dict(payload.get("verification") or {}),
                allow_provisional=bool(payload.get("allow_provisional", False)),
                repair_attempts=int(payload.get("repair_attempts", 2)),
                created_at=str(payload.get("created_at") or utc_now()),
                parent_version=payload.get("parent_version"),
                schema_version=str(payload.get("schema_version") or AGENT_SCHEMA_VERSION),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise AgentConfigError(f"Invalid agent definition: {exc}") from exc
        validate_definition(definition)
        return definition


def validate_definition(agent: AgentDefinition) -> None:
    problems: list[str] = []
    if not _NAME_PATTERN.match(agent.name):
        problems.append("name must be 2-80 characters of letters, digits, spaces or . : @ ( ) + -")
    if not agent.model_name.strip():
        problems.append("a model is required")
    if not agent.model_digest.strip():
        problems.append("the exact model digest is required")
    if agent.role not in ROLE_KEYS:
        problems.append(f"unknown role {agent.role!r}; expected one of {', '.join(sorted(ROLE_KEYS))}")
    if agent.harness is not None and agent.harness not in HARNESS_MODES:
        problems.append(f"unknown harness {agent.harness!r}")
    if len(agent.instructions) > 8_000:
        problems.append("instructions exceed 8000 characters")
    if agent.permissions.overwrite not in {"ask", "deny"}:
        problems.append("permissions.overwrite must be 'ask' or 'deny'")
    if agent.permissions.command not in {"ask", "deny"}:
        problems.append("permissions.command must be 'ask' or 'deny'")
    if agent.permissions.shell and not agent.permissions.allowed_commands:
        problems.append("shell permission requires an explicit allowed_commands list")
    if not 1 <= agent.budget.max_steps <= 40:
        problems.append("budget.max_steps must be between 1 and 40")
    if not 1 <= agent.budget.max_seconds <= 86_400:
        problems.append("budget.max_seconds must be between 1 and 86400")
    if agent.budget.max_tokens is not None and agent.budget.max_tokens < 1:
        problems.append("budget.max_tokens must be positive")
    if not 0 <= agent.budget.max_tool_calls <= 64:
        problems.append("budget.max_tool_calls must be between 0 and 64")
    if not 0 <= agent.repair_attempts <= 5:
        problems.append("repair_attempts must be between 0 and 5")
    for reference in agent.skills:
        if not isinstance(reference.get("skill_id"), str) or "version" not in reference:
            problems.append("each skill reference needs a skill_id and a pinned version")
    if problems:
        raise AgentConfigError("; ".join(problems))


class GrantedTools:
    """Host-side permission layer over workspace tools.

    The catalogue the model sees is the qualified catalogue; a permission the
    agent does not hold is refused here, by the host, whatever the model asks.
    """

    def __init__(self, inner: WorkspaceTools, permissions: Permissions) -> None:
        self._inner = inner
        self._permissions = permissions

    @property
    def workspace(self) -> Path:
        return self._inner.workspace

    def definitions(self) -> list[dict[str, Any]]:
        return self._inner.definitions()

    def invoke(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name == "write_file" and not self._permissions.write:
            raise ToolError("This agent does not hold the write permission.")
        if name == "run_command" and not self._permissions.shell:
            raise ToolError("This agent does not hold the shell permission.")
        return self._inner.invoke(name, arguments)


@dataclass(frozen=True, slots=True)
class AgentRunOutcome:
    result: AgentResult
    report: VerificationReport | None
    agent: AgentDefinition
    qualification_id: str

    @property
    def verified(self) -> bool:
        return self.report is not None and self.report.passed

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.result.run_id, "status": self.result.status,
            "final_message": self.result.final_message, "steps": self.result.steps,
            "usage": dict(self.result.usage),
            "agent": {"agent_id": self.agent.agent_id, "version": self.agent.version,
                      "name": self.agent.name, "config_sha256": self.agent.config_sha256},
            "qualification_id": self.qualification_id,
            "verification": None if self.report is None else self.report.to_dict(),
            "verified": self.verified,
        }


class AgentService:
    """Create, version, gate and execute agents."""

    def __init__(self, client: Any, repository: Any, store: AgentStore, *,
                 keep_alive: str = "5m", admission: Any | None = None) -> None:
        self.client = client
        # Optional AdmissionController. When present, every run is admitted
        # against measured RAM/VRAM before the model is loaded.
        self.admission = admission
        self.repository = repository
        self.store = store
        self.keep_alive = keep_alive
        self.skills = SkillRegistry(store)

    # -- definitions ------------------------------------------------------------
    def create(self, *, name: str, profile: ModelProfile, role: str | None = None,
               allow_unearned_role: bool = False, **options: Any) -> AgentDefinition:
        if self.store.find_agent_id(name) is not None:
            raise AgentConfigError(f"An agent named {name!r} already exists")
        chosen_role = role or (profile.recommended_roles[0] if profile.recommended_roles
                               else "general_assistant")
        earned = {*profile.recommended_roles, "general_assistant"}
        if chosen_role in ROLE_KEYS and chosen_role not in earned:
            if not allow_unearned_role:
                raise AgentConfigError(
                    f"Role {chosen_role!r} is not supported by this model's measured profile "
                    f"(earned: {', '.join(sorted(earned))}). Profile the model, choose an earned "
                    "role, or pass allow_unearned_role to record an explicit override."
                )
            options["context"] = {**dict(options.get("context") or {}), "role_override": True}
        title = options.pop("role_title", None) or next(
            (definition.title for definition in ROLE_DEFINITIONS if definition.key == chosen_role),
            profile.professional_title,
        )
        agent = AgentDefinition(
            agent_id=str(uuid.uuid4()), version=1, name=name.strip(),
            model_name=profile.model_name, model_digest=profile.digest,
            role=chosen_role, role_title=str(title), **options,
        )
        validate_definition(agent)
        self.store.save_agent_version(agent.to_dict())
        return agent

    def get(self, reference: str, version: int | None = None) -> AgentDefinition:
        agent_id = self.store.find_agent_id(reference)
        raw = self.store.get_agent_version(agent_id, version) if agent_id else None
        if raw is None:
            raise KeyError(f"Unknown agent: {reference}")
        return AgentDefinition.from_dict(raw)

    def state(self, reference: str) -> dict[str, Any]:
        agent_id = self.store.find_agent_id(reference)
        state = self.store.get_agent_state(agent_id) if agent_id else None
        if state is None:
            raise KeyError(f"Unknown agent: {reference}")
        return state

    def update(self, reference: str, **changes: Any) -> AgentDefinition:
        """Create the next version. The active version is not affected."""
        if self.state(reference)["status"] == "retired":
            raise AgentConfigError("A retired agent cannot be changed; duplicate it instead")
        current = self.get(reference)
        updated = replace(
            current, **changes, version=current.version + 1,
            parent_version=current.version, created_at=utc_now(),
        )
        validate_definition(updated)
        self.store.save_agent_version(updated.to_dict())
        return updated

    def duplicate(self, reference: str, new_name: str) -> AgentDefinition:
        if self.store.find_agent_id(new_name) is not None:
            raise AgentConfigError(f"An agent named {new_name!r} already exists")
        source = self.get(reference)
        copy = replace(source, agent_id=str(uuid.uuid4()), version=1, name=new_name.strip(),
                       parent_version=None, created_at=utc_now())
        validate_definition(copy)
        self.store.save_agent_version(copy.to_dict())
        return copy

    def disable(self, reference: str, note: str = "") -> None:
        state = self.state(reference)
        self.store.set_agent_state(state["agent_id"], "disabled", state["active_version"], note)

    def retire(self, reference: str, note: str = "") -> None:
        state = self.state(reference)
        self.store.set_agent_state(state["agent_id"], "retired", None, note)

    # -- tools and readiness ------------------------------------------------------
    @staticmethod
    def build_tools(agent: AgentDefinition, workspace: Path, *,
                    approval: Callable[[str], bool] | None = None,
                    cancel_event: Event | None = None) -> GrantedTools:
        ask = approval or (lambda _description: False)
        permissions = agent.permissions

        def decide(description: str) -> bool:
            if description.startswith("Overwrite") and permissions.overwrite == "deny":
                return False
            if description.startswith("Run approved executable") and permissions.command == "deny":
                return False
            return bool(ask(description))

        commands = tuple(sys.executable if item == "{python}" else item
                         for item in permissions.allowed_commands)
        inner = WorkspaceTools(
            workspace, allow_shell=permissions.shell, allowed_commands=commands,
            approval=decide, cancel_event=cancel_event,
            allow_sensitive_files=permissions.sensitive_files,
            allow_git=permissions.git,
        )
        if permissions.shell and "{python}" in permissions.allowed_commands:
            inner.allowed_command_paths["python"] = Path(sys.executable).resolve()
        return GrantedTools(inner, permissions)

    def tool_manifest(self, agent: AgentDefinition) -> str:
        """Digest of the catalogue this agent would be given. The catalogue depends
        only on granted permissions, never on the workspace location."""
        with tempfile.TemporaryDirectory(prefix="11vated-manifest-") as directory:
            return tool_manifest_sha256(self.build_tools(agent, Path(directory)).definitions())

    def _installed(self, model_name: str) -> ModelRef | None:
        for item in self.client.list_models():
            if str(item.get("name") or item.get("model") or "") == model_name:
                return ModelRef(model_name, digest=str(item.get("digest") or ""))
        return None

    def resolve_qualification(self, agent: AgentDefinition) -> HarnessQualification | None:
        raw = (self.store.get_qualification(agent.qualification_id) if agent.qualification_id
               else self.store.latest_qualification(
                   agent.model_name, agent.model_digest, self.tool_manifest(agent)))
        return None if raw is None else HarnessQualification.from_dict(raw)

    def readiness(self, agent: AgentDefinition) -> list[str]:
        """Every reason this exact configuration must not run. Empty means ready."""
        issues: list[str] = []
        try:
            installed = self._installed(agent.model_name)
            runtime_version: str | None = str(self.client.version())
        except Exception as exc:
            return [f"Ollama is unreachable, so the model cannot be verified: {exc}"]
        if installed is None:
            issues.append(f"model {agent.model_name} is not installed")
        elif installed.digest != agent.model_digest:
            issues.append("the installed model digest differs from the agent's bound digest; "
                          "profile and qualify the new artifact")
        qualification = self.resolve_qualification(agent)
        if qualification is None:
            issues.append("no harness qualification exists for this model digest and tool set; "
                          "run: 11vated qualify " + agent.model_name
                          + (" --shell" if agent.permissions.shell else ""))
        else:
            harness = agent.harness or qualification.selected_harness
            if harness is None:
                issues.append("qualification found no harness this model can operate reliably")
            issues.extend(qualification_issues(
                qualification, model_digest=agent.model_digest,
                tool_manifest=self.tool_manifest(agent), runtime_version=runtime_version,
                harness=harness,
            ))
            result = qualification.harnesses.get(harness or "")
            if (result is not None and result.status == "provisional"
                    and not agent.allow_provisional):
                issues.append(
                    f"harness {harness} is only provisionally qualified "
                    f"({result.passed}/{result.attempts}); set allow_provisional to accept the risk")
        try:
            skills = self.skills.resolve([dict(item) for item in agent.skills])
        except SkillError as exc:
            issues.append(str(exc))
            skills = []
        granted = {*WORKSPACE_TOOLS, *(("run_command",) if agent.permissions.shell else ())}
        held = {"read", *(("write",) if agent.permissions.write else ()),
                *(("shell",) if agent.permissions.shell else ())}
        for skill in skills:
            if self.skills.status(skill.skill_id, skill.version) == "retired":
                issues.append(f"skill {skill.skill_id} v{skill.version} is retired")
            missing_tools = set(skill.required_tools) - granted
            if missing_tools:
                issues.append(f"skill {skill.skill_id} needs tools the agent is not granted: "
                              + ", ".join(sorted(missing_tools)))
            missing_permissions = set(skill.permissions) - held
            if missing_permissions:
                issues.append(f"skill {skill.skill_id} needs permissions the agent does not hold: "
                              + ", ".join(sorted(missing_permissions)))
        return issues

    def activate(self, reference: str, version: int | None = None) -> AgentDefinition:
        agent = self.get(reference, version)
        if self.state(agent.agent_id)["status"] == "retired":
            raise AgentNotReady(["the agent is retired"])
        issues = self.readiness(agent)
        if issues:
            raise AgentNotReady(issues)
        self.store.set_agent_state(agent.agent_id, "active", agent.version)
        return agent

    def qualify(self, reference: str, profile: ModelProfile, *, trials: int = 2,
                progress: Callable[[str], None] | None = None,
                cancel_event: Event | None = None) -> HarnessQualification:
        """Qualify the agent's model against the agent's own tool catalogue."""
        agent = self.get(reference)
        if profile.digest != agent.model_digest:
            raise AgentConfigError("The supplied profile is for a different model digest")

        def factory(workspace: Path, cancel: Event | None = None) -> Any:
            lab_agent = replace(agent, permissions=replace(agent.permissions, overwrite="ask",
                                                           command="ask"))
            return self.build_tools(lab_agent, workspace, approval=lambda _d: True,
                                    cancel_event=cancel)

        record = HarnessLab(self.client, keep_alive=self.keep_alive).qualify(
            agent.model_name, profile, trials=trials, tool_factory=factory,
            progress=progress, cancel_event=cancel_event,
        )
        self.store.save_qualification(record.to_dict())
        return record

    # -- execution ----------------------------------------------------------------
    def run(
        self, reference: str, task: str, workspace: Path, profile: ModelProfile, *,
        checks: list[dict[str, Any]] | None = None, allow_command_checks: bool = False,
        approval: Callable[[str], bool] | None = None,
        event: Callable[[AgentEvent], None] | None = None,
        cancel_event: Event | None = None,
        resume_checkpoint: dict[str, Any] | None = None,
        tools_wrapper: Callable[[Any], Any] | None = None,
        baseline: dict[str, str] | None = None,
    ) -> AgentRunOutcome:
        state = self.state(reference)
        if state["status"] != "active" or state["active_version"] is None:
            raise AgentNotReady([f"agent status is {state['status']}; activate it first"])
        agent = self.get(state["agent_id"], int(state["active_version"]))
        # Re-check at the moment of use: the model, Ollama or a skill may have
        # changed since activation. A stale activation never authorises a run.
        issues = self.readiness(agent)
        if issues:
            raise AgentNotReady(issues)
        if profile.digest != agent.model_digest or profile.model_name != agent.model_name:
            raise AgentNotReady(["the supplied profile does not match the agent's model digest"])
        qualification = self.resolve_qualification(agent)
        assert qualification is not None  # guaranteed by readiness()
        harness = agent.harness or qualification.selected_harness
        if self.admission is not None:
            requested_ctx = agent.context.get("num_ctx")
            decision = self.admission.require(  # raises ResourceDenied
                agent.model_name, int(requested_ctx) if requested_ctx else None)
            if event is not None:
                event(AgentEvent("resource_admission", "; ".join(decision.reasons),
                                 decision.to_dict()))
        skills = self.skills.resolve([dict(item) for item in agent.skills])
        instructions = "\n\n".join(part for part in (
            agent.instructions.strip(), render_skills(skills)) if part)
        tools: Any = self.build_tools(agent, workspace, approval=approval,
                                      cancel_event=cancel_event)
        if tools_wrapper is not None:
            tools = tools_wrapper(tools)
        workspace_root = Path(tools.workspace)
        if baseline is None and resume_checkpoint is None:
            baseline = snapshot_workspace(workspace_root)
        options = ({"num_ctx": int(agent.context["num_ctx"])}
                   if agent.context.get("num_ctx") else None)
        runtime = AgentRuntime(
            self.client, max_steps=agent.budget.max_steps, keep_alive=self.keep_alive,
            repository=self.repository,
            memory_store=(memory_store_for(self.repository, self.client)
                          if agent.memory.get("enabled") and self.repository is not None else None),
        )
        result = runtime.run(
            agent.model_name, profile, task, tools, event=event, cancel_event=cancel_event,
            resume_checkpoint=resume_checkpoint,
            policy=RunPolicy(
                harness_mode=harness, extra_instructions=instructions,
                agent_title=f"{agent.name} ({agent.role_title})",
                max_seconds=agent.budget.max_seconds, max_tokens=agent.budget.max_tokens,
                max_tool_calls=agent.budget.max_tool_calls,
                repair_attempts=agent.repair_attempts, options=options,
            ),
        )
        self.store.bind_run(result.run_id, agent.agent_id, agent.version,
                            agent.config_sha256, qualification.qualification_id)
        report: VerificationReport | None = None
        if checks or agent.verification.get("require_checks"):
            report = VerificationEngine(
                workspace_root, allow_commands=allow_command_checks,
            ).verify(checks or [], run_id=result.run_id, run_status=result.status,
                     final_message=result.final_message, baseline=baseline)
            self.store.save_verification(report.to_dict())
        return AgentRunOutcome(result, report, agent, qualification.qualification_id)
