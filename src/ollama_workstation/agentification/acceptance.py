"""Real-world acceptance scenarios, executed against a live Ollama instance.

Each scenario builds a disposable workspace, runs a real agent through the
production runtime and reports a status derived only from host-executed
verification. A scenario that cannot be exercised honestly (a missing model, a
permission the operator did not grant, an interruption that could not be
timed) reports ``blocked`` or ``inconclusive``; it never reports a pass.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import partial
from pathlib import Path
from typing import Any

from ollama_workstation.agentification.agents import (
    AgentDefinition,
    AgentNotReady,
    AgentService,
    Budget,
    Permissions,
)
from ollama_workstation.agentification.harness import (
    HarnessLab,
    HarnessQualification,
    default_tool_factory,
    qualification_issues,
    shell_tool_factory,
)
from ollama_workstation.agentification.skills import builtin_skills
from ollama_workstation.agentification.store import AgentStore
from ollama_workstation.agentification.tasks import (
    FaultInjectingTools,
    TaskSpec,
    ToolFault,
    instantiate,
)
from ollama_workstation.agentification.verifier import snapshot_workspace
from ollama_workstation.agents.recovery import load_recovery_candidates
from ollama_workstation.agents.runtime import tool_manifest_sha256
from ollama_workstation.models.schemas import ModelProfile, utc_now
from ollama_workstation.profiling.profiler import ModelProfiler

SCENARIOS = ("A", "C", "E", "F")
NOT_IMPLEMENTED = {
    "B": "Research agent: requires a browsing/research tool that is not implemented yet.",
    "D": "Multi-agent workforce: run `11vated workforce run`; not yet wired into this harness.",
}


@dataclass(slots=True)
class ScenarioResult:
    scenario: str
    title: str
    status: str  # passed | failed | blocked | inconclusive
    model: str = ""
    summary: str = ""
    evidence: dict[str, Any] = field(default_factory=dict)
    started_at: str = field(default_factory=utc_now)
    finished_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {slot: getattr(self, slot) for slot in self.__slots__}


def aggregation_task() -> TaskSpec:
    """Multi-file, multi-tool task shared by the tool and recovery scenarios."""
    amount = {"kind": "int", "min": 11, "max": 480}
    return TaskSpec(
        "regional_totals", "autonomous_tools",
        "The orders directory contains one text file per region. Each file has a line of the "
        "form 'total: N'. Read every file in orders, add the totals together and write the "
        "result into report/summary.txt as exactly: grand total: SUM",
        files={
            "orders/north.txt": "region: north\ntotal: {north}\n",
            "orders/south.txt": "region: south\ntotal: {south}\n",
            "orders/west.txt": "region: west\ntotal: {west}\n",
            "README.md": "Regional order exports. Batch {code}.\n",
        },
        variables={"code": {"kind": "token", "length": 6}, "north": amount, "south": amount,
                   "west": amount, "sum": {"kind": "sum", "of": ["north", "south", "west"]}},
        checks=(
            {"kind": "run_completed"},
            {"kind": "file_contains", "path": "report/summary.txt",
             "regex": r"grand total:\s*{sum}(?!\d)"},
            {"kind": "changes_within", "allowed": ["report/summary.txt"]},
        ),
        max_steps=14,
    )


class AcceptanceRunner:
    def __init__(self, client: Any, repository: Any, store: AgentStore, work_root: Path, *,
                 base_url: str, keep_alive: str = "5m", trials: int = 2,
                 allow_code_execution: bool = False,
                 progress: Callable[[str], None] | None = None) -> None:
        self.client = client
        self.repository = repository
        self.store = store
        self.work_root = work_root
        self.base_url = base_url
        self.keep_alive = keep_alive
        self.trials = trials
        self.allow_code_execution = allow_code_execution
        self.notify = progress or (lambda _message: None)
        self.service = AgentService(client, repository, store, keep_alive=keep_alive)
        self.service.skills.install_builtins()

    # -- shared preparation -------------------------------------------------------
    def profile(self, model: str) -> ModelProfile:
        profiler = ModelProfiler(self.client, keep_alive=self.keep_alive)
        installed = next((item for item in profiler.inventory() if item.name == model), None)
        if installed is None:
            raise LookupError(f"Model {model!r} is not installed in the configured Ollama instance")
        saved = self.repository.get_profile(model, installed.digest)
        if saved is not None and any(item.status != "not_assessed" for item in saved.capabilities):
            return saved  # type: ignore[no-any-return]
        self.notify(f"Profiling {model} (capability probes)")
        profile = profiler.profile(model, run_probes=True, repeats=1, progress=self.notify)
        self.repository.save_profile(profile)
        return profile

    def qualification(self, model: str, profile: ModelProfile, *, shell: bool = False,
                      force: bool = False) -> HarnessQualification:
        factory = shell_tool_factory if shell else default_tool_factory
        scratch = self.work_root / "manifest-probe"
        manifest = tool_manifest_sha256(factory(scratch, None).definitions())
        existing = None if force else self.store.latest_qualification(
            model, profile.digest, manifest)
        if existing is not None:
            record = HarnessQualification.from_dict(existing)
            if not qualification_issues(record, model_digest=profile.digest,
                                        tool_manifest=manifest,
                                        runtime_version=str(self.client.version())):
                return record
        self.notify(f"Qualifying harnesses for {model}" + (" (with shell tool)" if shell else ""))
        record = HarnessLab(self.client, keep_alive=self.keep_alive).qualify(
            model, profile, trials=self.trials, tool_factory=factory, progress=self.notify)
        self.store.save_qualification(record.to_dict())
        return record

    def ready_agent(self, model: str, profile: ModelProfile, *, purpose: str, shell: bool = False,
                    skills: tuple[str, ...] = (), max_steps: int = 14) -> AgentDefinition:
        """Create and activate an agent, or raise AgentNotReady with every reason."""
        self.qualification(model, profile, shell=shell)
        pinned = tuple({"skill_id": skill.skill_id, "version": skill.version}
                       for skill in builtin_skills() if skill.skill_id in skills)
        agent = self.service.create(
            name=f"acceptance-{purpose}-{uuid.uuid4().hex[:8]}", profile=profile,
            permissions=Permissions(shell=shell,
                                    allowed_commands=("{python}",) if shell else ()),
            skills=pinned,
            budget=Budget(max_steps=max_steps, max_seconds=1_800.0),
        )
        return self.service.activate(agent.agent_id)

    # -- scenarios ----------------------------------------------------------------
    def run(self, scenarios: tuple[str, ...], models: list[str]) -> list[ScenarioResult]:
        results: list[ScenarioResult] = []
        for key in scenarios:
            if key in NOT_IMPLEMENTED:
                results.append(ScenarioResult(key, "Not implemented", "blocked",
                                              summary=NOT_IMPLEMENTED[key], finished_at=utc_now()))
                continue
            if key == "F":
                results.append(self._guard(key, "Model qualification", "",
                                           lambda: self.scenario_f(models)))
                continue
            handler = {"A": self.scenario_a, "C": self.scenario_c, "E": self.scenario_e}[key]
            title = {"A": "Repository engineering agent", "C": "Autonomous tool agent",
                     "E": "Recovery after interruption"}[key]
            model = models[0]
            results.append(self._guard(key, title, model, partial(handler, model)))
        return results

    def _guard(self, key: str, title: str, model: str,
               action: Callable[[], ScenarioResult]) -> ScenarioResult:
        started = utc_now()
        try:
            result = action()
        except AgentNotReady as exc:
            result = ScenarioResult(key, title, "blocked", model,
                                    "The model did not pass the agent readiness gate.",
                                    {"issues": exc.issues})
        except LookupError as exc:
            result = ScenarioResult(key, title, "blocked", model, str(exc))
        except Exception as exc:
            result = ScenarioResult(key, title, "failed", model,
                                    f"{type(exc).__name__}: {exc}")
        result.started_at = started
        result.finished_at = utc_now()
        return result

    def _workspace(self, label: str) -> Path:
        workspace = self.work_root / f"{label}-{uuid.uuid4().hex[:8]}"
        workspace.mkdir(parents=True, exist_ok=False)
        return workspace

    def scenario_f(self, models: list[str]) -> ScenarioResult:
        rows: list[dict[str, Any]] = []
        for model in models:
            profile = self.profile(model)
            record = self.qualification(model, profile)
            rows.append({
                "model": model, "digest": profile.digest[:16], "status": record.status,
                "selected_harness": record.selected_harness,
                "professional_title": profile.professional_title,
                "recommended_roles": profile.recommended_roles[:3],
                "workload_suitability": record.workload_suitability,
                "harnesses": {mode: {"status": item.status,
                                     "passed": item.passed, "attempts": item.attempts,
                                     "interval": [item.interval_low, item.interval_high],
                                     "median_seconds": item.median_seconds}
                              for mode, item in record.harnesses.items()},
                "qualification_id": record.qualification_id,
            })
        differences: list[str] = []
        for label, key in (("qualification status", "status"),
                           ("selected harness", "selected_harness"),
                           ("professional title", "professional_title"),
                           ("workload suitability", "workload_suitability")):
            if len({json.dumps(row[key], sort_keys=True) for row in rows}) > 1:
                differences.append(label)
        if len({json.dumps({m: (h["passed"], h["attempts"]) for m, h in row["harnesses"].items()},
                           sort_keys=True) for row in rows}) > 1:
            differences.append("verified pass counts per harness")
        if len(rows) < 2:
            status, summary = "inconclusive", "At least two installed chat models are required."
        elif differences:
            status = "passed"
            summary = "Measured differences between models: " + ", ".join(differences) + "."
        else:
            status = "inconclusive"
            summary = "All models were measured but produced identical results."
        return ScenarioResult("F", "Model qualification", status, ", ".join(models), summary,
                              {"models": rows, "differences": differences})

    def scenario_c(self, model: str) -> ScenarioResult:
        profile = self.profile(model)
        agent = self.ready_agent(model, profile, purpose="tools")
        instance = instantiate(aggregation_task(), time.time_ns() % 1_000_000)
        workspace = self._workspace("scenario-c")
        instance.materialize(workspace)
        wrapper: dict[str, FaultInjectingTools] = {}

        def wrap(tools: Any) -> Any:
            wrapper["tools"] = FaultInjectingTools(tools, (ToolFault("write_file", 1),))
            return wrapper["tools"]

        outcome = self.service.run(
            agent.agent_id, instance.objective, workspace, profile,
            checks=[dict(item) for item in instance.checks],
            approval=lambda _description: True, tools_wrapper=wrap,
            event=lambda item: self.notify(f"[{item.kind}] {item.message}"),
        )
        kinds = [item.kind for item in outcome.result.trace]
        induced = len(wrapper["tools"].induced)
        tools_used = sorted({str(item.data.get("tool")) for item in outcome.result.trace
                             if item.kind == "tool_completed"})
        recovered = induced > 0 and "tool_denied" in kinds and outcome.verified
        if outcome.verified and recovered and len(tools_used) >= 2:
            status, summary = "passed", (
                f"Completed a {kinds.count('tool_completed')}-tool-call task using "
                f"{', '.join(tools_used)}, recovered from an induced write failure, and the "
                "result was independently verified.")
        elif outcome.verified:
            status, summary = "inconclusive", (
                "The result verified, but the induced failure was not exercised.")
        else:
            status, summary = "failed", "Independent verification did not pass."
        return ScenarioResult("C", "Autonomous tool agent", status, model, summary, {
            **outcome.to_dict(), "workspace": str(workspace), "induced_failures": induced,
            "tools_used": tools_used, "harness": outcome.result.trace[0].data.get("mode"),
        })

    def scenario_a(self, model: str) -> ScenarioResult:
        if not self.allow_code_execution:
            return ScenarioResult(
                "A", "Repository engineering agent", "blocked", model,
                "This scenario runs model-written code with the Python interpreter and no OS "
                "sandbox. Re-run with --allow-code-execution to opt in.")
        profile = self.profile(model)
        agent = self.ready_agent(model, profile, purpose="repo", shell=True,
                                 skills=("defect-fix-loop",), max_steps=16)
        spec = next(skill for skill in builtin_skills()
                    if skill.skill_id == "defect-fix-loop").test_cases[0]
        instance = instantiate(spec, 1)
        workspace = self._workspace("scenario-a")
        instance.materialize(workspace)
        outcome = self.service.run(
            agent.agent_id, instance.objective, workspace, profile,
            checks=[dict(item) for item in instance.checks], allow_command_checks=True,
            approval=lambda _description: True,
            event=lambda item: self.notify(f"[{item.kind}] {item.message}"),
        )
        ran_tests = any(item.kind == "tool_completed" and item.data.get("tool") == "run_command"
                        for item in outcome.result.trace)
        status = "passed" if outcome.verified else "failed"
        summary = ("Defect located and fixed; the host re-ran the test suite and confirmed the "
                   "test file was untouched." if outcome.verified
                   else "Host verification (test suite and diff rules) did not pass.")
        return ScenarioResult("A", "Repository engineering agent", status, model, summary, {
            **outcome.to_dict(), "workspace": str(workspace), "agent_ran_tests": ran_tests,
        })

    def scenario_e(self, model: str, *, poll_seconds: float = 0.01,
                   deadline_seconds: float = 900.0) -> ScenarioResult:
        title = "Recovery after interruption"
        profile = self.profile(model)
        agent = self.ready_agent(model, profile, purpose="recovery")
        instance = instantiate(aggregation_task(), time.time_ns() % 1_000_000)
        workspace = self._workspace("scenario-e")
        instance.materialize(workspace)
        baseline = snapshot_workspace(workspace)
        resolved = str(workspace.resolve())
        spec_file = self.work_root / f"recovery-{uuid.uuid4().hex[:8]}.json"
        spec_file.write_text(json.dumps({
            "database": str(self.repository.database), "base_url": self.base_url,
            "agent_id": agent.agent_id, "workspace": resolved, "task": instance.objective,
            "keep_alive": self.keep_alive,
        }), encoding="utf-8")
        child = subprocess.Popen(
            [sys.executable, "-m", "ollama_workstation.agentification._recovery_child",
             str(spec_file)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        self.notify(f"Started agent in child process {child.pid}; waiting for a safe boundary")
        run_id, killed_at_step = "", -1
        limit = time.monotonic() + deadline_seconds
        try:
            while time.monotonic() < limit and child.poll() is None:
                for row in self.repository.list_run_checkpoints(50):
                    if (row["workspace"] == resolved and row["state"] == "awaiting_model"
                            and int(row["tool_calls"]) >= 1):
                        run_id, killed_at_step = str(row["run_id"]), int(row["step"])
                        break
                if run_id:
                    child.kill()  # abrupt termination: no cleanup handlers run
                    break
                time.sleep(poll_seconds)
        finally:
            if child.poll() is None and not run_id:
                child.kill()
            child.wait(timeout=30)
        if not run_id:
            return ScenarioResult("E", title, "inconclusive", model,
                                  "The agent finished or failed before it could be interrupted "
                                  "after its first tool call; no recovery was exercised.",
                                  {"child_exit_code": child.returncode})
        events_before = self.repository.list_run_events(run_id)
        self.notify(f"Killed process {child.pid} at step {killed_at_step}; "
                    f"{len(events_before)} journal events survive. Recovering.")
        candidate = next((item for item in load_recovery_candidates(self.repository)
                          if item.run_id == run_id), None)
        evidence: dict[str, Any] = {
            "run_id": run_id, "killed_pid": child.pid, "killed_at_step": killed_at_step,
            "events_before_interruption": len(events_before), "workspace": resolved,
            "recovery_status": None if candidate is None else candidate.status.value,
        }
        if candidate is None or not candidate.resumable:
            return ScenarioResult(
                "E", title, "inconclusive", model,
                "The interruption landed where the host refuses automatic replay "
                f"({'missing' if candidate is None else candidate.reason}). That is the safe "
                "behaviour, but resumption was not demonstrated; re-run the scenario.", evidence)
        checkpoint = self.repository.get_run_checkpoint(run_id)
        outcome = self.service.run(
            agent.agent_id, instance.objective, workspace, profile,
            checks=[dict(item) for item in instance.checks],
            approval=lambda _description: True, resume_checkpoint=checkpoint, baseline=baseline,
            event=lambda item: self.notify(f"[{item.kind}] {item.message}"),
        )
        events_after = self.repository.list_run_events(run_id)
        kinds = [str(item["kind"]) for item in events_after]
        writes = sum(1 for item in events_after if item["kind"] == "tool_completed"
                     and item["data"].get("tool") == "write_file")
        history_kept = [item["kind"] for item in events_after[:len(events_before)]] == [
            item["kind"] for item in events_before]
        chain_ok = bool(self.repository.verify_run_event_chain(run_id, strict=True))
        evidence.update({
            **outcome.to_dict(), "events_after_recovery": len(events_after),
            "resumed_events": kinds.count("resumed"), "write_file_completions": writes,
            "history_preserved": history_kept, "journal_chain_verified": chain_ok,
            "same_run_id": outcome.result.run_id == run_id,
        })
        ok = (outcome.verified and outcome.result.run_id == run_id and kinds.count("resumed") == 1
              and writes == 1 and history_kept and chain_ok)
        return ScenarioResult(
            "E", title, "passed" if ok else "failed", model,
            ("The agent process was killed mid-task, the run resumed from its durable "
             "checkpoint in a new process, completed with exactly one write, kept its full "
             "tamper-evident history, and the result was independently verified.") if ok
            else "Recovery completed but one or more recovery guarantees were not met.",
            evidence)

