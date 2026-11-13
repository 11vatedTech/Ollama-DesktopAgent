"""Command-line entry point for inventory, profiling and agent execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import socket
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ollama_workstation import __version__
from ollama_workstation.agentification import commands as agentification_commands
from ollama_workstation.agentification.gate import ExecutionRefused, clear_repository_run
from ollama_workstation.agentification.resources import ResourceDenied
from ollama_workstation.agents.binding import validate_installed_model_binding
from ollama_workstation.agents.execution_lease import LeaseError
from ollama_workstation.agents.identity_package import export_identity_package
from ollama_workstation.agents.mcp_gateway import CompositeToolRegistry, MCPToolGateway
from ollama_workstation.agents.memory import memory_store_for
from ollama_workstation.agents.orchestration import (
    AgentAssignment,
    WorkforceOrchestrator,
    WorkforcePlan,
    WorkforcePlanner,
)
from ollama_workstation.agents.process_identity import current_process_identity
from ollama_workstation.agents.recovery import load_recovery_candidates
from ollama_workstation.agents.runtime import AgentEvent, AgentResult, AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.agents.workforce import WorkforceSelector
from ollama_workstation.benchmarking.runner import BenchmarkRunner
from ollama_workstation.config import AppPaths, load_settings
from ollama_workstation.models.routing import ModelRouter, RoutingRequest
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.observability import configure_logging, log_event
from ollama_workstation.ollama.client import OllamaClient, OllamaError
from ollama_workstation.profiling.profiler import ModelProfiler
from ollama_workstation.profiling.research import HuggingFaceResearcher, attach_model_card_evidence
from ollama_workstation.storage.repository import Repository


def _services() -> tuple[Any, Any, Any, Any]:
    paths = AppPaths.default()
    paths.ensure()
    configure_logging(paths.logs_dir)
    settings = load_settings(paths.config_file)
    base_url = os.environ.get("OLLAMA_WORKSTATION_OLLAMA_URL", "").strip()
    if base_url:
        settings.ollama_base_url = base_url
    client = OllamaClient(settings.ollama_base_url, settings.request_timeout_seconds)
    return paths, settings, client, Repository(paths.database_file)


def _execute_agent(
    client: Any,
    settings: Any,
    repository: Repository,
    *,
    model: str,
    profile: ModelProfile,
    task: str,
    workspace: Path,
    max_steps: int,
    resume_checkpoint: dict[str, Any] | None,
    event: Callable[[AgentEvent], None],
    lease_owner: str | None = None,
    allow_unqualified: bool = False,
) -> AgentResult:
    """Run or resume one bounded agent with the same host-tool policy.

    Every run passes the shared execution gate (qualification + RAM/VRAM
    admission) before the model is loaded.
    """
    def approve(description: str) -> bool:
        return input(f"APPROVAL REQUIRED: {description}\nAllow? [y/N] ").strip().lower() == "y"

    workspace_tools = WorkspaceTools(
        workspace, allow_shell=settings.allow_shell_tools,
        allowed_commands=settings.allowed_commands, approval=approve,
    )
    with MCPToolGateway(settings.mcp_servers) as mcp_gateway:
        tools = CompositeToolRegistry(workspace_tools, mcp_gateway)
        if mcp_gateway.routes:
            print(
                f"[mcp] Connected {len(mcp_gateway.routes)} explicitly approved tool(s).",
                file=sys.stderr,
            )
        clearance, policy = clear_repository_run(
            client, repository, model_name=model, model_digest=profile.digest,
            tool_definitions=tools.definitions(),
            allow_unqualified=allow_unqualified or settings.allow_unqualified_models,
            resume_checkpoint=resume_checkpoint,
        )
        print(f"[gate] {clearance.summary()}", file=sys.stderr)
        return AgentRuntime(
            client, max_steps=max_steps, keep_alive=settings.keep_alive,
            repository=repository, memory_store=memory_store_for(repository, client, settings),
        ).run(
            model, profile, task, tools,
            event=event,
            resume_checkpoint=resume_checkpoint,
            lease_owner=lease_owner,
            policy=policy,
        )


def _recover(args: argparse.Namespace, settings: Any, client: Any,
             repository: Repository) -> int:
    """List, resume or discard integrity-verified durable runs."""
    candidates = load_recovery_candidates(repository)
    if args.list_only or not args.run_id:
        print(json.dumps([item.to_dict() for item in candidates], indent=2, ensure_ascii=False))
        return 0
    run_id = str(args.run_id)
    if args.discard:
        if repository.delete_run_checkpoint(run_id):
            print(f"Discarded checkpoint for run {run_id}.")
            return 0
        print(f"No checkpoint exists for run {run_id}.", file=sys.stderr)
        return 2
    candidate = next((item for item in candidates if item.run_id == run_id), None)
    if candidate is None:
        print(f"No recoverable checkpoint exists for run {run_id}.", file=sys.stderr)
        return 2
    if not candidate.resumable:
        print(f"Run {run_id} is not resumable: {candidate.reason}", file=sys.stderr)
        return 2
    try:
        checkpoint = repository.get_run_checkpoint(run_id)
    except ValueError as exc:
        print(f"Cannot recover run {run_id}: {exc}", file=sys.stderr)
        return 2
    if checkpoint is None:
        print(f"Checkpoint for run {run_id} disappeared before recovery.", file=sys.stderr)
        return 2
    payload = checkpoint.get("payload") or {}
    task = str(payload.get("task") or "")
    model = str(checkpoint.get("model_name") or "")
    profile = repository.get_profile(model, str(checkpoint.get("model_digest") or ""))
    if profile is None:
        print(
            "The exact profiled model digest for this run is unavailable; refusing to resume.",
            file=sys.stderr,
        )
        return 2
    installed = next(
        (item for item in ModelProfiler(client).inventory() if item.name == model),
        None,
    )
    if installed is None:
        print(f"Model {model!r} is not installed in the configured Ollama instance.", file=sys.stderr)
        return 2
    validate_installed_model_binding(profile, installed)
    workspace = Path(str(checkpoint.get("workspace") or ""))
    if not workspace.is_dir():
        print(f"Checkpoint workspace does not exist: {workspace}", file=sys.stderr)
        return 2
    owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
    if not repository.acquire_run_lease(
        run_id, owner_id, ttl_seconds=300,
        **current_process_identity().as_lease_metadata(),
    ):
        print(
            f"Run {run_id} is leased by another workstation process; refusing concurrent resume.",
            file=sys.stderr,
        )
        return 2
    try:
        result = _execute_agent(
            client, settings, repository,
            model=model, profile=profile, task=task, workspace=workspace,
            max_steps=args.max_steps, resume_checkpoint=checkpoint,
            event=lambda item: print(f"[{item.kind}] {item.message}", file=sys.stderr),
            lease_owner=owner_id,
        )
    except LeaseError as exc:
        print(f"Cannot resume run {run_id}: {exc}", file=sys.stderr)
        return 2
    finally:
        repository.release_run_lease(run_id, owner_id)
    safe_message, redacted_count = WorkspaceTools.redact_sensitive_text(result.final_message)
    if redacted_count:
        print(
            f"[security] Redacted {redacted_count} credential-like value(s) from the final response.",
            file=sys.stderr,
        )
    print(safe_message)
    if result.status == "workspace_busy":
        print(
            "Run workspace is admitted to another live execution; no tool or provider call was made.",
            file=sys.stderr,
        )
        return 2
    return 0 if result.status == "completed" else 1


def _workforce_gate(client: Any, repository: Repository, settings: Any) -> Callable[..., Any]:
    """Execution gate for workforce child runs (qualification + admission)."""
    def gate(model: str, digest: str, definitions: list[dict[str, Any]]) -> Any:
        clearance, policy = clear_repository_run(
            client, repository, model_name=model, model_digest=digest,
            tool_definitions=definitions, allow_unqualified=settings.allow_unqualified_models,
        )
        print(f"[gate] {clearance.summary()}", file=sys.stderr)
        return policy
    return gate


def _ensure_profile(client: Any, repository: Repository, model: str, *,
                    run_probes: bool = False) -> tuple[ModelProfile, Any]:
    """Return an exact installed profile or create a clearly provisional one."""
    installed = next(
        (item for item in ModelProfiler(client).inventory() if item.name == model), None
    )
    if installed is None:
        raise ValueError(f"Model {model!r} is not installed in the configured Ollama instance")
    profile = repository.get_profile(model, installed.digest)
    if profile is not None:
        return profile, installed
    profile = ModelProfiler(client).profile(model, run_probes=run_probes, repeats=1)
    repository.save_profile(profile)
    return profile, installed


def _workforce(args: argparse.Namespace, settings: Any, client: Any,
               repository: Repository) -> int:
    """Plan, select, approve and execute a bounded multi-agent workflow."""
    if args.workforce_command == "status":
        if args.run_id:
            run = repository.get_workforce_run(args.run_id)
            if run is None:
                print(f"No workforce run {args.run_id!r}.", file=sys.stderr)
                return 2
            print(json.dumps(run, indent=2, ensure_ascii=False))
        else:
            print(json.dumps(repository.list_workforce_runs(100), indent=2, ensure_ascii=False))
        return 0

    if args.plan:
        plan = WorkforcePlan.from_dict(json.loads(Path(args.plan).read_text(encoding="utf-8")))
    elif args.mode == "analysis":
        plan = WorkforcePlanner.analysis_plan(args.task)
    else:
        plan = WorkforcePlanner.delivery_plan(args.task)
    installed = ModelProfiler(client).inventory()
    profiles = repository.list_profiles()
    assignments: dict[str, AgentAssignment] = {}

    if args.model:
        profile, _ = _ensure_profile(
            client, repository, args.model, run_probes=args.profile_probes
        )
        for task in plan.tasks:
            assignments[task.task_id] = AgentAssignment(
                task.task_id, profile.model_name, profile, task.read_only
            )
    elif args.assign:
        by_role: dict[str, tuple[ModelProfile, str]] = {}
        for specification in args.assign:
            role, separator, model = specification.partition("=")
            if not separator or not role.strip() or not model.strip():
                print("Each --assign value must be ROLE=MODEL.", file=sys.stderr)
                return 2
            profile, _ = _ensure_profile(
                client, repository, model.strip(), run_probes=args.profile_probes
            )
            by_role[role.strip()] = (profile, model.strip())
        for task in plan.tasks:
            selected = by_role.get(task.role)
            if selected is None:
                print(f"No model assigned for role {task.role!r}.", file=sys.stderr)
                return 2
            profile, model_name = selected
            assignments[task.task_id] = AgentAssignment(
                task.task_id, model_name, profile, task.read_only
            )
    else:
        selection = WorkforceSelector.select(
            [task.to_dict() for task in plan.tasks], profiles, installed
        )
        missing = [task.task_id for task in plan.tasks if task.task_id not in selection.assignments]
        if missing:
            print(
                "No evidence-backed workforce assignment is available. Profile candidate models "
                "first or pass --model MODEL / --assign ROLE=MODEL. Missing: " + ", ".join(missing),
                file=sys.stderr,
            )
            print(json.dumps(selection.to_dict(), indent=2), file=sys.stderr)
            return 2
        by_name = {profile.model_name: profile for profile in profiles}
        for task in plan.tasks:
            model_name = selection.assignments[task.task_id]
            profile = by_name.get(model_name)
            if profile is None:
                print(f"Selected model {model_name!r} has no saved profile.", file=sys.stderr)
                return 2
            assignments[task.task_id] = AgentAssignment(
                task.task_id, model_name, profile, task.read_only
            )
        print(json.dumps(selection.to_dict(), indent=2), file=sys.stderr)

    def approve(message: str) -> bool:
        print("APPROVAL REQUIRED:\n" + message, file=sys.stderr)
        if args.yes:
            print("[approval] granted by --yes", file=sys.stderr)
            return True
        try:
            return input("Allow? [y/N] ").strip().lower() == "y"
        except EOFError:
            return False

    owner_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex}"
    run_id = str(uuid.uuid4())
    if not repository.acquire_run_lease(
        run_id, owner_id, ttl_seconds=900,
        **current_process_identity().as_lease_metadata(),
    ):
        print("A workforce run with this identifier is already active.", file=sys.stderr)
        return 2
    try:
        result = WorkforceOrchestrator(
            client, repository, keep_alive=settings.keep_alive,
            execution_gate=_workforce_gate(client, repository, settings),
        ).run(
            plan, args.workspace, assignments, run_id=run_id,
            approval=approve, installed_models=installed, owner_id=owner_id,
            event=lambda item: print(f"[{item.kind}] {item.message}", file=sys.stderr),
        )
    finally:
        repository.release_run_lease(run_id, owner_id)
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(result.final_message)
        print(
            f"status={result.status} verification_passed={result.verification_passed} "
            f"run_id={result.run_id}",
            file=sys.stderr,
        )
    return 0 if result.status == "completed" else 1


def _route(args: argparse.Namespace, settings: Any, client: Any,
           repository: Repository) -> int:
    """Explain model routing using installed metadata and saved observations."""
    from ollama_workstation.models.model_intelligence import inspect_ollama_model
    from ollama_workstation.profiling.telemetry import SystemTelemetry

    models = ModelProfiler(client).inventory()
    intelligence = {}
    for model in models:
        try:
            intelligence[model.name] = inspect_ollama_model(
                model, client.show_model(model.name)
            )
        except OllamaError:
            continue
    decision = ModelRouter().rank(
        models,
        request=RoutingRequest(
            required_capabilities=tuple(args.capability or ()),
            context_tokens=max(0, int(args.context)),
            require_verified=bool(args.require_verified),
        ),
        profiles=repository.list_profiles(),
        intelligence=intelligence,
        host=SystemTelemetry().snapshot(),
    )
    print(json.dumps(decision.to_dict(), indent=2, ensure_ascii=False))
    return 0 if decision.selected is not None else 2


def _doctor(settings: Any, client: Any, repository: Repository) -> int:
    """Emit a local diagnostic snapshot without secrets or model payloads."""
    from ollama_workstation.profiling.telemetry import SystemTelemetry

    try:
        version = client.version()
        healthy = True
    except OllamaError as exc:
        version = f"unavailable: {type(exc).__name__}"
        healthy = False
    try:
        models = ModelProfiler(client).inventory()
        model_count = len(models)
        model_names = [item.name for item in models]
    except OllamaError:
        model_count = 0
        model_names = []
    try:
        import sqlite3

        with sqlite3.connect(repository.database) as db:
            integrity = str(db.execute("PRAGMA integrity_check").fetchone()[0])
    except Exception as exc:
        integrity = f"error: {type(exc).__name__}"
    payload = {
        "version": __version__,
        "ollama": {"base_url": settings.ollama_base_url, "healthy": healthy,
                   "version": version, "model_count": model_count,
                   "models": model_names},
        "database": {"path": str(repository.database), "integrity": integrity,
                     "profiles": len(repository.list_profiles()),
                     "runs": len(repository.list_runs(500)),
                     "workforce_runs": len(repository.list_workforce_runs(500))},
        "checkpoint_key": {"path": str(repository.checkpoint_key_path),
                            "present": repository.checkpoint_key_path.is_file()},
        "logs": {
            "path": str(AppPaths.default().logs_dir / "workstation.jsonl"),
            "present": (AppPaths.default().logs_dir / "workstation.jsonl").is_file(),
        },
        "host": SystemTelemetry().snapshot(),
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0 if healthy and integrity == "ok" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="11vated", description="11vatedTech Ollama Agent Workstation")
    parser.add_argument("--version", action="version", version=f"11vated {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("models", help="List installed Ollama models")
    profile = commands.add_parser("profile", help="Run a local capability assessment")
    profile.add_argument("model")
    profile.add_argument("--metadata-only", action="store_true")
    profile.add_argument("--repeats", type=int, default=1, choices=range(1, 6),
                         help="Repeat each local smoke probe 1-5 times (more reliable, slower)")
    profile_all = commands.add_parser(
        "profile-all",
        help="Create persistent profiles for every installed model",
    )
    profile_all.add_argument(
        "--probes", action="store_true",
        help="Run the full smoke probe suite for every model (slow and memory-intensive)",
    )
    profile_all.add_argument("--repeats", type=int, default=1, choices=range(1, 6))
    benchmark = commands.add_parser(
        "benchmark", help="Run a repeatable, independently scored local benchmark suite"
    )
    benchmark.add_argument("model")
    benchmark.add_argument("--suite", default="core", choices=["core", "11vated-core"])
    benchmark.add_argument("--repeats", type=int, default=3, choices=range(1, 6))
    benchmark.add_argument("--output", type=Path, help="Optional JSON report destination")
    research = commands.add_parser("research", help="Search public model-card candidates")
    research.add_argument("model")
    attach = commands.add_parser("attach-model-card", help="Attach a user-selected Hugging Face model card")
    attach.add_argument("model")
    attach.add_argument("repository_id")
    export = commands.add_parser("export-agent", help="Export an Agent Identity Package")
    export.add_argument("model")
    export.add_argument("--output", type=Path)
    run = commands.add_parser("run", help="Run a bounded agent in a workspace")
    run.add_argument("model")
    run.add_argument("workspace", type=Path)
    run.add_argument("task")
    run.add_argument("--max-steps", type=int, default=12)
    run.add_argument("--unqualified", action="store_true",
                     help="Operator override: run even though the model has no harness qualification")
    recover = commands.add_parser(
        "recover", help="List, resume or discard durable agent run checkpoints"
    )
    recover.add_argument("run_id", nargs="?", help="Checkpoint run ID to resume or discard")
    recover.add_argument("--list", action="store_true", dest="list_only",
                         help="List recovery candidates and their eligibility")
    recover.add_argument("--discard", action="store_true",
                         help="Delete the selected checkpoint after review")
    recover.add_argument("--max-steps", type=int, default=12)
    workforce = commands.add_parser(
        "workforce", help="Plan, approve and execute a bounded multi-agent workflow"
    )
    workforce_commands = workforce.add_subparsers(dest="workforce_command", required=True)
    workforce_run = workforce_commands.add_parser("run", help="Execute a validated workforce plan")
    workforce_run.add_argument("task")
    workforce_run.add_argument("workspace", type=Path)
    workforce_run.add_argument("--mode", choices=["analysis", "delivery"], default="analysis")
    workforce_run.add_argument("--model", help="Use one exact installed model for every task")
    workforce_run.add_argument(
        "--assign", action="append", default=[], metavar="ROLE=MODEL",
        help="Assign a model to a role; repeat for complementary agents",
    )
    workforce_run.add_argument("--plan", type=Path, help="Validated workforce plan JSON")
    workforce_run.add_argument("--yes", action="store_true",
                               help="Pre-approve this plan and its marked tasks")
    workforce_run.add_argument("--json", action="store_true", help="Print the full result JSON")
    workforce_run.add_argument(
        "--profile-probes", action="store_true",
        help="Run empirical smoke probes when creating a missing profile",
    )
    workforce_status = workforce_commands.add_parser(
        "status", help="List workforce runs or inspect one durable run"
    )
    workforce_status.add_argument("run_id", nargs="?")
    route = commands.add_parser("route", help="Explain hardware-aware model routing")
    route.add_argument("--capability", action="append", default=[],
                       help="Required capability, e.g. tool_calling or vision")
    route.add_argument("--context", type=int, default=0, help="Required context tokens")
    route.add_argument("--require-verified", action="store_true",
                       help="Require an observed capability score, not declared metadata")
    commands.add_parser("doctor", help="Emit local diagnostics and database integrity status")
    agentification_commands.register(commands)
    return parser


def _utf8_console() -> None:
    """Model output contains arbitrary Unicode. On Windows the console codec is often
    cp1252, so printing a single unmappable character would crash a finished command."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        encoding = str(getattr(stream, "encoding", "") or "").lower().replace("-", "")
        if reconfigure is not None and encoding != "utf8":
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    args = build_parser().parse_args(argv)
    paths, settings, client, repository = _services()
    log_event("cli_command", "CLI command started.", command=args.command)
    try:
        if args.command in agentification_commands.COMMANDS:
            return agentification_commands.dispatch(args, paths, settings, client, repository)
        if args.command == "workforce":
            return _workforce(args, settings, client, repository)
        if args.command == "route":
            return _route(args, settings, client, repository)
        if args.command == "doctor":
            return _doctor(settings, client, repository)
        if args.command == "models":
            print(json.dumps([{"name": x.name, "size_bytes": x.size_bytes,
                               "digest": x.digest, "details": x.details}
                              for x in ModelProfiler(client).inventory()], indent=2))
            return 0
        if args.command == "benchmark":
            model_ref = next(
                (item for item in ModelProfiler(client).inventory() if item.name == args.model),
                None,
            )
            if model_ref is None:
                print(f"Model {args.model!r} is not installed in the configured Ollama instance.", file=sys.stderr)
                return 2
            report = BenchmarkRunner(client, keep_alive=settings.keep_alive).run(
                args.model,
                model_digest=model_ref.digest,
                suite_id=args.suite,
                repeats=args.repeats,
                progress=lambda message: print(message, file=sys.stderr),
            )
            safe_model_id = hashlib.sha256(args.model.encode("utf-8")).hexdigest()[:12]
            destination = args.output or (
                paths.profiles_dir / "benchmarks" / f"{safe_model_id}-{report.run_id[:8]}.json"
            )
            destination.parent.mkdir(parents=True, exist_ok=True)
            payload = json.dumps(report.to_dict(), indent=2, ensure_ascii=False)
            destination.write_text(payload, encoding="utf-8")
            profile = repository.get_profile(args.model, model_ref.digest)
            if profile is not None:
                summary = report.profile_summary()
                operational = profile.operational if isinstance(profile.operational, dict) else {}
                history = operational.get("benchmark_history")
                history = list(history) if isinstance(history, list) else []
                operational["benchmark_history"] = (history + [summary])[-12:]
                operational["latest_benchmark"] = summary
                profile.operational = operational
                repository.save_profile(profile)
            print(json.dumps({
                "report": str(destination),
                "run_id": report.run_id,
                "model": report.model_name,
                "digest": report.model_digest,
                "ollama_version": report.ollama_version,
                "inference_options": report.inference_options,
                "suite": f"{report.suite_id}@{report.suite_version}",
                "score": report.score,
                "passed": report.total_passed,
                "attempts": report.total_attempts,
                "manifest_sha256": report.manifest_sha256,
            }, indent=2, ensure_ascii=False))
            return 0
        if args.command == "profile":
            profile = ModelProfiler(client, settings.keep_alive).profile(
                args.model, run_probes=not args.metadata_only,
                progress=lambda message: print(message, file=sys.stderr), repeats=args.repeats)
            repository.save_profile(profile)
            safe = args.model.replace("/", "_").replace(":", "_")
            (paths.profiles_dir / f"{safe}.json").write_text(
                json.dumps(profile.to_dict(), indent=2), encoding="utf-8")
            print(json.dumps(profile.to_dict(), indent=2, ensure_ascii=False))
            return 0
        if args.command == "profile-all":
            profiler = ModelProfiler(client, settings.keep_alive)
            profiles = profiler.profile_inventory(
                run_probes=args.probes,
                progress=lambda message: print(message, file=sys.stderr),
                repeats=args.repeats,
            )
            for profile in profiles:
                repository.save_profile(profile)
                safe = profile.model_name.replace("/", "_").replace(":", "_")
                (paths.profiles_dir / f"{safe}.json").write_text(
                    json.dumps(profile.to_dict(), indent=2), encoding="utf-8"
                )
            print(json.dumps({
                "profiles": len(profiles),
                "run_probes": bool(args.probes),
                "models": [
                    {
                        "model": profile.model_name,
                        "digest": profile.digest,
                        "title": profile.professional_title,
                        "assessed_capabilities": sum(
                            1 for item in profile.capabilities
                            if item.status != "not_assessed"
                        ),
                    }
                    for profile in profiles
                ],
            }, indent=2, ensure_ascii=False))
            return 0
        if args.command == "research":
            if not settings.enable_remote_model_research:
                print("Remote model research is disabled. Enable it in Settings first.", file=sys.stderr)
                return 2
            candidates = HuggingFaceResearcher().search_candidates(args.model)
            print(json.dumps([{"repository_id": x.repository_id, "pipeline_tag": x.pipeline_tag,
                               "downloads": x.downloads, "likes": x.likes, "tags": x.tags,
                               "match_score": x.match_score, "match_reasons": x.match_reasons,
                               "model_card_url": x.model_card_url} for x in candidates], indent=2))
            return 0
        if args.command == "attach-model-card":
            if not settings.enable_remote_model_research:
                print("Remote model research is disabled. Enable it in Settings first.", file=sys.stderr)
                return 2
            profile = next((x for x in repository.list_profiles() if x.model_name == args.model), None)
            if profile is None:
                print("Profile the model first.", file=sys.stderr)
                return 2
            card = HuggingFaceResearcher().fetch_model_card(args.repository_id)
            attach_model_card_evidence(profile, card)
            repository.save_profile(profile)
            safe = args.model.replace("/", "_").replace(":", "_")
            (paths.profiles_dir / f"{safe}.model-card.md").write_text(
                str(card.get("model_card_markdown") or ""), encoding="utf-8")
            print(f"Attached user-selected model card {args.repository_id} to {args.model}.")
            return 0
        if args.command == "export-agent":
            profile = next((x for x in repository.list_profiles() if x.model_name == args.model), None)
            if profile is None:
                print("Profile not found. Run 11vated profile MODEL first.", file=sys.stderr)
                return 2
            safe = args.model.replace("/", "_").replace(":", "_")
            destination = args.output or (paths.profiles_dir / f"{safe}.agent.json")
            export_identity_package(profile, destination)
            print(f"Exported {profile.professional_title} to {destination}")
            return 0
        if args.command == "run":
            profile = next((x for x in repository.list_profiles() if x.model_name == args.model), None)
            if profile is None:
                print("Profile not found. Run 11vated profile MODEL first.", file=sys.stderr)
                return 2
            installed = next(
                (item for item in ModelProfiler(client).inventory() if item.name == args.model),
                None,
            )
            if installed is None:
                print(f"Model {args.model!r} is not installed in the configured Ollama instance.", file=sys.stderr)
                return 2
            validate_installed_model_binding(profile, installed)
            result = _execute_agent(
                client, settings, repository,
                model=args.model, profile=profile, task=args.task, workspace=args.workspace,
                max_steps=args.max_steps, resume_checkpoint=None,
                event=lambda item: print(f"[{item.kind}] {item.message}", file=sys.stderr),
                allow_unqualified=args.unqualified,
            )
            safe_message, redacted_count = WorkspaceTools.redact_sensitive_text(result.final_message)
            if redacted_count:
                print(f"[security] Redacted {redacted_count} credential-like value(s) from the final response.", file=sys.stderr)
            print(safe_message)
            return 0 if result.status == "completed" else 1
        if args.command == "recover":
            return _recover(args, settings, client, repository)
    except ExecutionRefused as exc:
        lines = ["Refused: the model is not cleared to run as an agent:",
                 *(f"  - {issue}" for issue in exc.issues)]
        print("\n".join(lines), file=sys.stderr)
        return 3
    except ResourceDenied as exc:
        print(f"Refused by resource admission: {exc}", file=sys.stderr)
        return 3
    except (OllamaError, OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
