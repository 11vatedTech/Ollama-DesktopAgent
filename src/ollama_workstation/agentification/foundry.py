"""Qt-free controller behind the desktop Agent Foundry page.

Everything the page shows or does goes through here, so it is testable without
a display and uses exactly the same services as the CLI: qualification
(:class:`HarnessLab`), versioned agents (:class:`AgentService`), RAM/VRAM
admission and hybrid memory.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from threading import Event
from typing import Any

from ollama_workstation.agentification.agents import (
    AgentService,
    Budget,
    Permissions,
)
from ollama_workstation.agentification.harness import (
    HarnessLab,
    HarnessQualification,
    default_tool_factory,
    shell_tool_factory,
)
from ollama_workstation.agentification.resources import AdmissionController
from ollama_workstation.agentification.store import AgentStore
from ollama_workstation.agents.memory import MemoryStore
from ollama_workstation.models.schemas import ModelProfile


@dataclass(slots=True)
class ModelRow:
    name: str
    digest: str
    size_bytes: int
    profiled: bool
    qualification_status: str  # qualified | provisional | unqualified | not qualified
    harness: str | None = None
    passed: int = 0
    attempts: int = 0
    qualified_at: str = ""
    suitability: dict[str, str] = field(default_factory=dict)
    qualification_id: str = ""


@dataclass(slots=True)
class AgentRow:
    agent_id: str
    name: str
    status: str
    active_version: int | None
    latest_version: int
    model_name: str
    role_title: str
    issues: list[str]


class FoundryController:
    def __init__(self, client: Any, repository: Any, settings: Any,
                 memory_store: MemoryStore | None = None) -> None:
        self.client = client
        self.repository = repository
        self.settings = settings
        self.store = AgentStore(repository.database)
        self.admission = AdmissionController(client)
        self.service = AgentService(client, repository, self.store,
                                    keep_alive=getattr(settings, "keep_alive", "5m"),
                                    admission=self.admission)
        self.service.skills.install_builtins()
        self.memory = memory_store or MemoryStore(repository)

    # -- models and qualification -------------------------------------------------------
    def installed_models(self) -> list[dict[str, Any]]:
        return [item for item in self.client.list_models()
                if "embed" not in str(item.get("name") or "").casefold()]

    def model_rows(self) -> list[ModelRow]:
        rows: list[ModelRow] = []
        for item in self.installed_models():
            name = str(item.get("name") or item.get("model") or "")
            digest = str(item.get("digest") or "")
            record = self.latest_qualification(name, digest)
            row = ModelRow(
                name=name, digest=digest, size_bytes=int(item.get("size") or 0),
                profiled=self.repository.get_profile(name, digest) is not None,
                qualification_status="not qualified" if record is None else record.status,
            )
            if record is not None:
                result = record.harnesses.get(str(record.selected_harness))
                row.harness = record.selected_harness
                row.passed = result.passed if result else 0
                row.attempts = result.attempts if result else 0
                row.qualified_at = record.created_at
                row.suitability = dict(record.workload_suitability)
                row.qualification_id = record.qualification_id
            rows.append(row)
        return rows

    def latest_qualification(self, model: str, digest: str) -> HarnessQualification | None:
        raw = self.store.latest_qualification(model, digest)
        return None if raw is None else HarnessQualification.from_dict(raw)

    def profile(self, model: str) -> ModelProfile:
        """Exact profile for the installed digest, created (without probes) if missing."""
        from ollama_workstation.profiling.profiler import ModelProfiler
        profiler = ModelProfiler(self.client)
        installed = next((item for item in profiler.inventory() if item.name == model), None)
        if installed is None:
            raise LookupError(f"Model {model!r} is not installed")
        profile = self.repository.get_profile(model, installed.digest)
        if profile is None:
            profile = profiler.profile(model, run_probes=False, repeats=1)
            self.repository.save_profile(profile)
        return profile  # type: ignore[no-any-return]

    def qualify(self, model: str, *, trials: int = 2, shell: bool = False,
                progress: Callable[[str], None] | None = None,
                cancel_event: Event | None = None) -> HarnessQualification:
        self.admission.require(model)  # never start a model that cannot fit
        profile = self.profile(model)
        record = HarnessLab(self.client, keep_alive=getattr(self.settings, "keep_alive", "5m")
                            ).qualify(
            model, profile, trials=trials, progress=progress, cancel_event=cancel_event,
            tool_factory=shell_tool_factory if shell else default_tool_factory,
        )
        self.store.save_qualification(record.to_dict())
        return record

    def admission_for(self, model: str) -> dict[str, Any]:
        return self.admission.evaluate(model).to_dict()

    # -- agents ---------------------------------------------------------------------------
    def agent_rows(self) -> list[AgentRow]:
        rows: list[AgentRow] = []
        for item in self.store.list_agents():
            agent = self.service.get(item["agent_id"])
            try:
                issues = self.service.readiness(agent) if item["status"] != "retired" else []
            except Exception as exc:  # noqa: BLE001 - surfaced, never hidden
                issues = [f"readiness check failed: {exc}"]
            rows.append(AgentRow(
                agent_id=item["agent_id"], name=item["name"], status=item["status"],
                active_version=item["active_version"], latest_version=item["latest_version"],
                model_name=agent.model_name, role_title=agent.role_title, issues=issues,
            ))
        return rows

    def create_agent(self, *, name: str, model: str, role: str | None = None,
                     instructions: str = "", write: bool = True, shell: bool = False,
                     memory: bool = True, allow_provisional: bool = True, git: bool = False,
                     skills: list[str] | None = None, max_steps: int = 12) -> Any:
        profile = self.profile(model)
        pinned = tuple({"skill_id": skill_id, "version": self.service.skills.get(skill_id).version}
                       for skill_id in (skills or []))
        return self.service.create(
            name=name, profile=profile, role=role, instructions=instructions, skills=pinned,
            permissions=Permissions(write=write, shell=shell, git=git,
                                    allowed_commands=("{python}",) if shell else ()),
            budget=Budget(max_steps=max_steps), memory={"enabled": memory},
            allow_provisional=allow_provisional,
        )

    def activate(self, reference: str) -> Any:
        return self.service.activate(reference)

    def disable(self, reference: str) -> None:
        self.service.disable(reference)

    def run_agent(self, reference: str, task: str, workspace: Path, *,
                  checks: list[dict[str, Any]] | None = None,
                  approval: Callable[[str], bool] | None = None,
                  event: Callable[[Any], None] | None = None,
                  cancel_event: Event | None = None) -> Any:
        agent = self.service.get(reference)
        return self.service.run(reference, task, workspace, self.profile(agent.model_name),
                                checks=checks, approval=approval, event=event,
                                cancel_event=cancel_event)

    def skills(self) -> list[dict[str, Any]]:
        return self.store.list_skills()

    # -- memory ---------------------------------------------------------------------------
    def memory_rows(self, scopes: list[tuple[str, str]], query: str = "") -> list[Any]:
        if query.strip():
            return self.memory.recall(query, scopes, limit=20)
        return [self.memory._annotate(item)  # noqa: SLF001 - same package family
                for item in self.memory.list(scopes, limit=200)]

    def memory_mode(self) -> str:
        if self.memory.embedder is None:
            return "keyword only (no embedding model configured)"
        return f"hybrid ({self.memory.embedding_model})"
