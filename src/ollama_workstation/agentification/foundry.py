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
    interaction_tool_factory,
    shell_tool_factory,
    web_tool_factory,
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

    def qualify(self, model: str, *, trials: int = 2, shell: bool = False, web: bool = False,
                browser: bool = False, computer: bool = False,
                progress: Callable[[str], None] | None = None,
                cancel_event: Event | None = None) -> HarnessQualification:
        self.admission.require(model)  # never start a model that cannot fit
        profile = self.profile(model)
        record = HarnessLab(self.client, keep_alive=getattr(self.settings, "keep_alive", "5m")
                            ).qualify(
            model, profile, trials=trials, progress=progress, cancel_event=cancel_event,
            tool_factory=(interaction_tool_factory(browser=browser, computer=computer)
                          if browser or computer else web_tool_factory if web
                          else shell_tool_factory if shell else default_tool_factory),
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
                     skills: list[str] | None = None, max_steps: int = 12,
                     web: bool = False, web_domains: tuple[str, ...] = (),
                     browser: bool = False, computer_apps: tuple[str, ...] = ()) -> Any:
        profile = self.profile(model)
        chosen = list(skills or [])
        if web or browser or computer_apps:
            # Web pages and application text are attacker-controlled; the content-is-data skill is the measured
            # mitigation for instruction-following from content (R15).
            if "content-is-data" not in chosen:
                chosen.append("content-is-data")
            web_domains = tuple(web_domains) or tuple(getattr(self.settings, "web_allowed_domains", ()))
        pinned = tuple({"skill_id": skill_id, "version": self.service.skills.get(skill_id).version}
                       for skill_id in chosen)
        return self.service.create(
            name=name, profile=profile, role=role, instructions=instructions, skills=pinned,
            permissions=Permissions(write=write, shell=shell, git=git, web=web, browser=browser,
                                    computer_apps=tuple(computer_apps),
                                    web_domains=tuple(web_domains),
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

    def skill_rows(self) -> list[dict[str, Any]]:
        rows = []
        for raw in self.store.list_skills():
            evaluations = self.store.list_skill_evaluations(str(raw["skill_id"]))
            latest = evaluations[-1] if evaluations else None
            rows.append({
                "skill_id": raw["skill_id"], "version": raw["version"], "name": raw["name"],
                "status": raw.get("_status", ""), "purpose": raw.get("purpose", ""),
                "evaluation": "" if latest is None else (
                    f"{latest['outcome']} on {latest['model_name']} "
                    f"({latest['baseline_passed']}->{latest['skill_passed']}/{latest['attempts']}"
                    + (f", p={latest['p_value']}" if "p_value" in latest else "") + ")"),
            })
        return rows

    @staticmethod
    def skill_template() -> dict[str, Any]:
        """A complete, valid starting point for a new skill (one test case)."""
        return {
            "skill_id": "my-skill", "name": "My skill",
            "purpose": "when the agent should apply this procedure",
            "input_contract": "What the agent is given.",
            "output_contract": "What the agent must produce.",
            "procedure": ["First step.", "Second step."],
            "validation_criteria": ["How a reviewer can tell the result is right."],
            "required_tools": ["read_file", "write_file"], "permissions": ["read", "write"],
            "test_cases": [{
                "task_id": "my_skill_case", "category": "multi_step",
                "objective": "Read input.txt and write its first line into output.txt.",
                "files": {"input.txt": "value {code}\nsecond\n"},
                "variables": {"code": {"kind": "token", "length": 7}},
                "checks": [{"kind": "run_completed"},
                           {"kind": "file_contains", "path": "output.txt", "text": "value {code}"},
                           {"kind": "changes_within", "allowed": ["output.txt"]}],
            }],
            "provenance": {"author": "operator", "source": "Agent Foundry"},
        }

    def publish_skill(self, payload: dict[str, Any]) -> Any:
        """Validate and publish a new immutable skill version."""
        return self.service.skills.publish(payload)

    def evaluate_skill(self, skill_id: str, model: str, *, trials: int = 4,
                       progress: Callable[[str], None] | None = None,
                       cancel_event: Event | None = None) -> Any:
        """Paired with/without evaluation on the model's qualified harness."""
        from ollama_workstation.agentification.skills import SkillEvaluator

        self.admission.require(model)
        profile = self.profile(model)
        record = self.latest_qualification(model, profile.digest)
        if record is None or record.selected_harness is None:
            raise LookupError(f"Qualify {model} first: a skill is evaluated on its qualified harness")
        skill = self.service.skills.get(skill_id)
        lab = HarnessLab(self.client, keep_alive=getattr(self.settings, "keep_alive", "5m"))
        factory = shell_tool_factory if "run_command" in skill.required_tools else default_tool_factory
        return SkillEvaluator(lab, self.store).evaluate(
            model, profile, record.selected_harness, skill, trials=trials, progress=progress,
            cancel_event=cancel_event, tool_factory=factory)

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
