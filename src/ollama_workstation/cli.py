"""Command-line entry point for inventory, profiling and agent execution."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ollama_workstation import __version__
from ollama_workstation.agents.identity_package import export_identity_package
from ollama_workstation.agents.runtime import AgentRuntime
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.config import AppPaths, load_settings
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
            def approve(description: str) -> bool:
                return input(f"APPROVAL REQUIRED: {description}\nAllow? [y/N] ").strip().lower() == "y"
            tools = WorkspaceTools(args.workspace, allow_shell=settings.allow_shell_tools,
                                   allowed_commands=settings.allowed_commands, approval=approve)
            result = AgentRuntime(client, max_steps=args.max_steps, keep_alive=settings.keep_alive,
                                  repository=repository).run(
                args.model, profile, args.task, tools,
                event=lambda item: print(f"[{item.kind}] {item.message}", file=sys.stderr))
            print(result.final_message)
            return 0 if result.status == "completed" else 1
    except (OllamaError, OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 2
