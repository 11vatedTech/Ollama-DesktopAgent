"""One execution gate for every path that runs a model as an agent.

``AgentService.run`` gates versioned agents. The older entry points (``11vated
run``, chat agent mode, history resume and workforce child runs) run a bare
model with an ad-hoc tool catalogue. They go through :func:`clear_for_execution`
so that no path can load a model as an agent without:

* the installed artifact matching the digest being run,
* a harness qualification for that digest whose qualified tool set covers the
  workspace tools being granted, under the current runtime/prompt/suite, and
* a RAM/VRAM admission decision that admits the load.

A run may proceed without a qualification only through an explicit, recorded
operator override (``allow_unqualified``). Provisional harnesses are permitted
but reported, because a provisional record is still measured evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from ollama_workstation.agentification.harness import (
    HarnessQualification,
    qualification_issues,
)

# Tools that the qualification suite exercises. Anything else in a catalogue
# (MCP tools, future integrations) is outside qualified evidence.
QUALIFIABLE_TOOLS = frozenset({"list_files", "read_file", "search_text", "write_file",
                               "run_command", "git_status", "git_diff", "git_log",
                               "web_search", "web_fetch"}) | frozenset(
    {"browser_open", "browser_snapshot", "browser_click", "browser_type", "browser_select",
     "browser_scroll", "browser_back", "browser_wait", "browser_downloads", "browser_upload",
     "computer_apps", "computer_launch", "computer_windows", "computer_snapshot",
     "computer_click", "computer_type", "computer_read", "computer_close"})


class ExecutionRefused(RuntimeError):
    """The gate refused to run the model. ``issues`` lists every blocking reason."""

    def __init__(self, issues: list[str]) -> None:
        super().__init__("Execution refused: " + "; ".join(issues))
        self.issues = list(issues)


@dataclass(frozen=True, slots=True)
class ExecutionClearance:
    model_name: str
    model_digest: str
    harness: str | None
    qualification_id: str | None
    qualification_status: str
    override: bool
    warnings: tuple[str, ...] = ()
    admission: dict[str, Any] | None = None
    unqualified_tools: tuple[str, ...] = field(default=())

    def to_dict(self) -> dict[str, Any]:
        return {
            "model_name": self.model_name, "model_digest": self.model_digest,
            "harness": self.harness, "qualification_id": self.qualification_id,
            "qualification_status": self.qualification_status, "override": self.override,
            "warnings": list(self.warnings), "admission": self.admission,
            "unqualified_tools": list(self.unqualified_tools),
        }

    def summary(self) -> str:
        if self.override:
            head = f"{self.model_name}: running UNQUALIFIED by operator override"
        else:
            head = (f"{self.model_name}: {self.qualification_status} on harness "
                    f"{self.harness} (qualification {str(self.qualification_id)[:8]})")
        return "; ".join((head, *self.warnings))


def _tool_names(definitions: list[dict[str, Any]]) -> set[str]:
    return {str(item.get("function", {}).get("name") or "") for item in definitions} - {""}


def _installed_digest(client: Any, model_name: str) -> str | None:
    for item in client.list_models():
        if str(item.get("name") or item.get("model") or "") == model_name:
            return str(item.get("digest") or "")
    return None


def select_qualification(
    records: list[HarnessQualification], *, model_digest: str, tool_names: set[str],
    runtime_version: str | None,
) -> tuple[HarnessQualification | None, list[str]]:
    """Newest record that still certifies this digest and covers the tools.

    Returns the record, or ``None`` with the reasons the newest candidate failed.
    """
    needed = tool_names & QUALIFIABLE_TOOLS
    first_issues: list[str] = []
    for record in records:
        issues = qualification_issues(
            record, model_digest=model_digest,
            tool_manifest=record.tool_manifest_sha256,  # coverage is checked below
            runtime_version=runtime_version, harness=record.selected_harness,
        )
        if record.selected_harness is None:
            issues.append("qualification found no harness this model can operate reliably")
        missing = needed - set(record.tool_names)
        if missing:
            issues.append("qualified tool set does not cover: " + ", ".join(sorted(missing)))
        if not issues:
            return record, []
        if not first_issues:
            first_issues = issues
    return None, first_issues


def clear_for_execution(
    client: Any, store: Any, *, model_name: str, model_digest: str,
    tool_definitions: list[dict[str, Any]], admission: Any | None = None,
    num_ctx: int | None = None, allow_unqualified: bool = False,
) -> ExecutionClearance:
    """Decide whether ``model_name`` may run as an agent with these tools.

    Raises :class:`ExecutionRefused` (qualification) or ``ResourceDenied``
    (admission). On success the returned clearance names the harness to use.
    """
    issues: list[str] = []
    warnings: list[str] = []
    try:
        installed = _installed_digest(client, model_name)
        runtime_version: str | None = str(client.version())
    except Exception as exc:  # noqa: BLE001 - any transport failure is a refusal
        raise ExecutionRefused([f"Ollama is unreachable, so the model cannot be verified: {exc}"]
                               ) from exc
    if installed is None:
        raise ExecutionRefused([f"model {model_name} is not installed"])
    if model_digest and installed != model_digest:
        raise ExecutionRefused([
            "the installed model digest differs from the profiled digest; "
            f"re-profile and qualify {model_name}"])
    digest = model_digest or installed

    names = _tool_names(tool_definitions)
    extras = tuple(sorted(names - QUALIFIABLE_TOOLS))
    if extras:
        warnings.append("tools outside qualified evidence: " + ", ".join(extras))

    records = [HarnessQualification.from_dict(raw)
               for raw in store.qualifications_for(model_name, digest)]
    record, reasons = select_qualification(
        records, model_digest=digest, tool_names=names, runtime_version=runtime_version)
    override = False
    if record is None:
        if not records:
            reasons = ["no harness qualification exists for this model digest; run: "
                       f"11vated qualify {model_name}"
                       + (" --shell" if "run_command" in names else "")
                       + (" --web" if "web_fetch" in names else "")
                       + (" --browser" if "browser_open" in names else "")
                       + (" --computer" if "computer_launch" in names else "")]
        if not allow_unqualified:
            issues.extend(reasons)
        else:
            override = True
            warnings.append("qualification bypassed: " + "; ".join(reasons))
    else:
        result = record.harnesses.get(str(record.selected_harness))
        if result is not None and result.status == "provisional":
            warnings.append(
                f"harness {record.selected_harness} is provisional "
                f"({result.passed}/{result.attempts} verified passes)")
        weak = sorted(category for category, verdict in record.workload_suitability.items()
                      if verdict == "unsuitable")
        if weak:
            warnings.append("measured unsuitable for: " + ", ".join(weak))
    if issues:
        raise ExecutionRefused(issues)

    decision_dict: dict[str, Any] | None = None
    if admission is not None:
        decision = admission.require(model_name, num_ctx)  # raises ResourceDenied
        decision_dict = decision.to_dict()
        if decision.outcome != "admit":
            warnings.append("; ".join(decision.reasons))
    return ExecutionClearance(
        model_name=model_name, model_digest=digest,
        harness=None if record is None else record.selected_harness,
        qualification_id=None if record is None else record.qualification_id,
        qualification_status="unqualified" if record is None else record.status,
        override=override, warnings=tuple(warnings), admission=decision_dict,
        unqualified_tools=extras,
    )


def clear_repository_run(
    client: Any, repository: Any, *, model_name: str, model_digest: str,
    tool_definitions: list[dict[str, Any]], allow_unqualified: bool = False,
    resume_checkpoint: dict[str, Any] | None = None, num_ctx: int | None = None,
    admission: Any | None = None, use_admission: bool = True,
) -> tuple[ExecutionClearance, Any]:
    """Gate a legacy run that owns a :class:`Repository`; return clearance and RunPolicy.

    A resumed run keeps the harness recorded in its checkpoint, because the
    runtime refuses to continue a transcript under a different action protocol.
    """
    from ollama_workstation.agentification.resources import AdmissionController
    from ollama_workstation.agentification.store import AgentStore
    from ollama_workstation.agents.runtime import RunPolicy

    database = getattr(repository, "database", None)
    if database is None:
        raise ExecutionRefused(["no workstation database is available to look up qualifications"])
    if use_admission and admission is None:
        admission = AdmissionController(client)
    clearance = clear_for_execution(
        client, AgentStore(database), model_name=model_name, model_digest=model_digest,
        tool_definitions=tool_definitions, admission=admission if use_admission else None,
        num_ctx=num_ctx, allow_unqualified=allow_unqualified,
    )
    harness = clearance.harness
    if resume_checkpoint is not None and resume_checkpoint.get("harness_mode"):
        harness = str(resume_checkpoint["harness_mode"])
    return clearance, RunPolicy(harness_mode=harness)
