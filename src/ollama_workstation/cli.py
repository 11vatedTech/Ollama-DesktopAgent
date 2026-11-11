"""Command-line entry point for inventory, profiling and agent execution."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ollama_workstation import __version__
from ollama_workstation.agents.binding import validate_installed_model_binding
from ollama_workstation.agents.identity_package import export_identity_package
from ollama_workstation.agents.mcp_gateway import CompositeToolRegistry, MCPToolGateway
from ollama_workstation.agents.memory import MemoryStore
from ollama_workstation.agents.recovery import load_recovery_candidates
from ollama_workstation.agents.runtime import AgentEvent, AgentResult, AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.benchmarking.runner import BenchmarkRunner
from ollama_workstation.config import AppPaths, load_settings
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.ollama.client import OllamaClient, OllamaError
from ollama_workstation.profiling.profiler import ModelProfiler
from ollama_workstation.profiling.research import HuggingFaceResearcher, attach_model_card_evidence
from ollama_workstation.storage.repository import Repository


def _services() -> tuple[Any, Any, Any, Any]:
    paths = AppPaths.default()
    paths.ensure()
    settings = load_settings(paths.config_file)
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
) -> AgentResult:
    """Run or resume one bounded agent with the same host-tool policy."""
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
        return AgentRuntime(
            client, max_steps=max_steps, keep_alive=settings.keep_alive,
            repository=repository, memory_store=MemoryStore(repository),
        ).run(
            model, profile, task, tools,
            event=event,
            resume_checkpoint=resume_checkpoint,
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
    result = _execute_agent(
        client, settings, repository,
        model=model, profile=profile, task=task, workspace=workspace,
        max_steps=args.max_steps, resume_checkpoint=checkpoint,
        event=lambda item: print(f"[{item.kind}] {item.message}", file=sys.stderr),
    )
    safe_message, redacted_count = WorkspaceTools.redact_sensitive_text(result.final_message)
    if redacted_count:
        print(
            f"[security] Redacted {redacted_count} credential-like value(s) from the final response.",
            file=sys.stderr,
        )
    print(safe_message)
    return 0 if result.status == "completed" else 1


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
    recover = commands.add_parser(
        "recover", help="List, resume or discard durable agent run checkpoints"
    )
    recover.add_argument("run_id", nargs="?", help="Checkpoint run ID to resume or discard")
    recover.add_argument("--list", action="store_true", dest="list_only",
                         help="List recovery candidates and their eligibility")
    recover.add_argument("--discard", action="store_true",
                         help="Delete the selected checkpoint after review")
    recover.add_argument("--max-steps", type=int, default=12)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    paths, settings, client, repository = _services()
    try:
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
            )
            safe_message, redacted_count = WorkspaceTools.redact_sensitive_text(result.final_message)
            if redacted_count:
                print(f"[security] Redacted {redacted_count} credential-like value(s) from the final response.", file=sys.stderr)
            print(safe_message)
            return 0 if result.status == "completed" else 1
        if args.command == "recover":
            return _recover(args, settings, client, repository)
    except (OllamaError, OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 2
