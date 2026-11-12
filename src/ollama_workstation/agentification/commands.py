"""Command-line surface for qualification, agents, skills, verification and acceptance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from ollama_workstation.agentification.acceptance import (
    NOT_IMPLEMENTED,
    SCENARIOS,
    AcceptanceRunner,
)
from ollama_workstation.agentification.agents import (
    AgentConfigError,
    AgentNotReady,
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
from ollama_workstation.agentification.skills import SkillError, SkillEvaluator
from ollama_workstation.agentification.store import AgentStore
from ollama_workstation.agentification.verifier import CheckSpecError, VerificationEngine
from ollama_workstation.agents.tools import WorkspaceTools
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.profiling.profiler import ModelProfiler

COMMANDS = frozenset({"qualify", "agent", "skill", "verify", "accept"})


def register(commands: Any) -> None:
    qualify = commands.add_parser(
        "qualify", help="Empirically qualify a model's agent harnesses with verified tool tasks")
    qualify.add_argument("model")
    qualify.add_argument("--trials", type=int, default=2, help="Trials per task (1-10)")
    qualify.add_argument("--shell", action="store_true",
                         help="Qualify against the tool set that includes run_command")
    qualify.add_argument("--json", action="store_true")

    agent = commands.add_parser("agent", help="Create, inspect, gate and run agents")
    actions = agent.add_subparsers(dest="agent_command", required=True)
    create = actions.add_parser("create", help="Create a draft agent from a profiled model")
    create.add_argument("name")
    create.add_argument("--model", required=True)
    create.add_argument("--role")
    create.add_argument("--allow-unearned-role", action="store_true")
    create.add_argument("--instructions", default="")
    create.add_argument("--skill", action="append", default=[], help="Skill id (repeatable)")
    create.add_argument("--shell", action="store_true")
    create.add_argument("--allow-command", action="append", default=[],
                        help="Executable the agent may request; use {python} for this interpreter")
    create.add_argument("--read-only", action="store_true")
    create.add_argument("--memory", action="store_true", help="Enable scoped memory recall")
    create.add_argument("--max-steps", type=int, default=12)
    create.add_argument("--max-seconds", type=float, default=900.0)
    create.add_argument("--max-tokens", type=int)
    create.add_argument("--allow-provisional", action="store_true")
    actions.add_parser("list", help="List agents and their status")
    for name, text in (("show", "Show an agent definition and its readiness"),
                       ("activate", "Run the readiness gate and activate the latest version"),
                       ("disable", "Stop an agent from running"),
                       ("retire", "Permanently retire an agent")):
        actions.add_parser(name, help=text).add_argument("agent")
    duplicate = actions.add_parser("duplicate", help="Copy an agent under a new name")
    duplicate.add_argument("agent")
    duplicate.add_argument("new_name")
    run = actions.add_parser("run", help="Run an active agent and verify its work")
    run.add_argument("agent")
    run.add_argument("workspace", type=Path)
    run.add_argument("task")
    run.add_argument("--checks", type=Path, help="JSON file with host verification checks")
    run.add_argument("--allow-command-checks", action="store_true")
    run.add_argument("--yes", action="store_true", help="Approve agent permission requests")
    run.add_argument("--json", action="store_true")

    skill = commands.add_parser("skill", help="List, publish, evaluate and retire skills")
    skill_actions = skill.add_subparsers(dest="skill_command", required=True)
    skill_actions.add_parser("list", help="List installed skills")
    skill_actions.add_parser("show", help="Show a skill").add_argument("skill")
    skill_actions.add_parser("publish", help="Publish a skill version from JSON").add_argument(
        "file", type=Path)
    skill_actions.add_parser("retire", help="Retire the latest skill version").add_argument("skill")
    evaluate = skill_actions.add_parser(
        "evaluate", help="Controlled with/without comparison of a skill on one model")
    evaluate.add_argument("skill")
    evaluate.add_argument("--model", required=True)
    evaluate.add_argument("--trials", type=int, default=2)
    evaluate.add_argument("--allow-code-execution", action="store_true")

    verify = commands.add_parser("verify", help="Run host verification checks on a workspace")
    verify.add_argument("workspace", type=Path)
    verify.add_argument("checks", type=Path)
    verify.add_argument("--allow-commands", action="store_true")

    accept = commands.add_parser("accept", help="Run real-world acceptance scenarios")
    accept.add_argument("--models", nargs="+", required=True,
                        help="Models to use; the first runs scenarios A, C and E")
    accept.add_argument("--scenarios", default=",".join(SCENARIOS),
                        help=f"Comma-separated subset of {', '.join(SCENARIOS)}")
    accept.add_argument("--trials", type=int, default=2)
    accept.add_argument("--allow-code-execution", action="store_true",
                        help="Permit scenario A to run model-written code without a sandbox")
    accept.add_argument("--output", type=Path)


def _say(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _profile(client: Any, repository: Any, model: str) -> ModelProfile:
    installed = next((item for item in ModelProfiler(client).inventory() if item.name == model), None)
    if installed is None:
        raise LookupError(f"Model {model!r} is not installed in the configured Ollama instance.")
    profile = repository.get_profile(model, installed.digest)
    if profile is None:
        raise LookupError(f"No profile for the installed digest. Run: 11vated profile {model}")
    return profile  # type: ignore[no-any-return]


def _print_qualification(record: HarnessQualification) -> None:
    print(f"{record.model_name}  digest {record.model_digest[:16]}  ->  {record.status.upper()}"
          f"  harness: {record.selected_harness or 'none'}")
    for mode, item in record.harnesses.items():
        timing = f"  median {item.median_seconds:.1f}s" if item.median_seconds is not None else ""
        print(f"  {mode:<19} {item.status:<12} {item.passed}/{item.attempts} verified"
              f"  95% CI {item.interval_low:.2f}-{item.interval_high:.2f}{timing}"
              + (f"  [{item.note}]" if item.note else ""))
    if record.workload_suitability:
        print("  workloads: " + ", ".join(f"{k}={v}" for k, v in record.workload_suitability.items()))
    print(f"  bound to: tools {record.tool_manifest_sha256[:12]}, prompt {record.prompt_version}, "
          f"Ollama {record.runtime_version}, suite {record.suite_id}@{record.suite_version}")
    print(f"  qualification id: {record.qualification_id}")


def dispatch(args: argparse.Namespace, paths: Any, settings: Any, client: Any,
             repository: Any) -> int:
    store = AgentStore(paths.database_file)
    service = AgentService(client, repository, store, keep_alive=settings.keep_alive)
    service.skills.install_builtins()
    try:
        if args.command == "qualify":
            profile = _profile(client, repository, args.model)
            record = HarnessLab(client, keep_alive=settings.keep_alive).qualify(
                args.model, profile, trials=args.trials, progress=_say,
                tool_factory=shell_tool_factory if args.shell else default_tool_factory,
            )
            store.save_qualification(record.to_dict())
            if args.json:
                print(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
            else:
                _print_qualification(record)
            return 0 if record.status == "qualified" else 1
        if args.command == "agent":
            return _agent(args, service, store, client, repository)
        if args.command == "skill":
            return _skill(args, service, store, client, repository, settings)
        if args.command == "verify":
            checks = json.loads(args.checks.read_text(encoding="utf-8"))
            report = VerificationEngine(args.workspace, allow_commands=args.allow_commands).verify(
                checks)
            print(json.dumps(report.to_dict(), indent=2, ensure_ascii=False))
            return 0 if report.passed else 1
        if args.command == "accept":
            return _accept(args, paths, settings, client, repository, store)
    except (LookupError, AgentConfigError, SkillError, CheckSpecError) as exc:
        print(str(exc).strip("'\""), file=sys.stderr)
        return 2
    except AgentNotReady as exc:
        print("Agent is not ready:", file=sys.stderr)
        for issue in exc.issues:
            print(f"  - {issue}", file=sys.stderr)
        return 2
    return 2


def _agent(args: argparse.Namespace, service: AgentService, store: AgentStore, client: Any,
           repository: Any) -> int:
    action = args.agent_command
    if action == "create":
        profile = _profile(client, repository, args.model)
        commands = tuple(args.allow_command) or (("{python}",) if args.shell else ())
        pinned = tuple({"skill_id": item, "version": service.skills.get(item).version}
                       for item in args.skill)
        agent = service.create(
            name=args.name, profile=profile, role=args.role,
            allow_unearned_role=args.allow_unearned_role, instructions=args.instructions,
            skills=pinned,
            permissions=Permissions(write=not args.read_only, shell=args.shell,
                                    allowed_commands=commands),
            budget=Budget(max_steps=args.max_steps, max_seconds=args.max_seconds,
                          max_tokens=args.max_tokens),
            memory={"enabled": bool(args.memory)}, allow_provisional=args.allow_provisional,
        )
        print(f"Created draft agent {agent.name} ({agent.agent_id}) v{agent.version} as "
              f"{agent.role_title}.")
        issues = service.readiness(agent)
        if issues:
            print("Not ready to activate:")
            for issue in issues:
                print(f"  - {issue}")
        else:
            print(f"Ready. Activate with: 11vated agent activate {agent.name}")
        return 0
    if action == "list":
        for row in store.list_agents():
            print(f"{row['name']:<32} {row['status']:<9} active v{row['active_version'] or '-'}"
                  f"  latest v{row['latest_version']}  {row['agent_id']}")
        return 0
    if action == "show":
        agent = service.get(args.agent)
        print(json.dumps({
            "state": service.state(args.agent), "definition": agent.to_dict(),
            "config_sha256": agent.config_sha256, "versions": store.agent_versions(agent.agent_id),
            "readiness_issues": service.readiness(agent),
        }, indent=2, ensure_ascii=False))
        return 0
    if action == "activate":
        agent = service.activate(args.agent)
        print(f"Activated {agent.name} v{agent.version}.")
        return 0
    if action in {"disable", "retire"}:
        getattr(service, action)(args.agent)
        print(f"Agent {args.agent} is now {action}d.")
        return 0
    if action == "duplicate":
        copy = service.duplicate(args.agent, args.new_name)
        print(f"Created draft agent {copy.name} ({copy.agent_id}).")
        return 0
    # run
    agent = service.get(args.agent)
    profile = _profile(client, repository, agent.model_name)
    checks = json.loads(args.checks.read_text(encoding="utf-8")) if args.checks else None

    def approve(description: str) -> bool:
        _say("APPROVAL REQUIRED: " + description)
        if args.yes:
            _say("[approval] granted by --yes")
            return True
        try:
            return input("Allow? [y/N] ").strip().lower() == "y"
        except EOFError:
            return False

    outcome = service.run(
        args.agent, args.task, args.workspace, profile, checks=checks,
        allow_command_checks=args.allow_command_checks, approval=approve,
        event=lambda item: _say(f"[{item.kind}] {item.message}"),
    )
    if args.json:
        print(json.dumps(outcome.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(WorkspaceTools.redact_sensitive_text(outcome.result.final_message)[0])
        verdict = "not requested" if outcome.report is None else outcome.report.status.value
        _say(f"status={outcome.result.status} verification={verdict} run_id={outcome.result.run_id}")
    if outcome.result.status != "completed":
        return 1
    return 0 if outcome.report is None or outcome.report.passed else 1


def _skill(args: argparse.Namespace, service: AgentService, store: AgentStore, client: Any,
           repository: Any, settings: Any) -> int:
    action = args.skill_command
    if action == "list":
        for skill, status in service.skills.all():
            print(f"{skill.skill_id:<26} v{skill.version}  {status:<8} {skill.name}")
        return 0
    if action == "show":
        skill = service.skills.get(args.skill)
        print(json.dumps({"definition": skill.to_dict(),
                          "status": service.skills.status(args.skill),
                          "evaluations": [
                              {k: v for k, v in item.items() if k != "attempts_detail"}
                              for item in store.list_skill_evaluations(args.skill)]},
                         indent=2, ensure_ascii=False))
        return 0
    if action == "publish":
        skill = service.skills.publish(json.loads(args.file.read_text(encoding="utf-8")))
        print(f"Published {skill.skill_id} v{skill.version}.")
        return 0
    if action == "retire":
        service.skills.retire(args.skill)
        print(f"Retired the latest version of {args.skill}.")
        return 0
    # evaluate
    skill = service.skills.get(args.skill)
    profile = _profile(client, repository, args.model)
    needs_shell = "run_command" in skill.required_tools
    if needs_shell and not args.allow_code_execution:
        print("This skill's tests run model-written code without an OS sandbox. "
              "Pass --allow-code-execution to opt in.", file=sys.stderr)
        return 2
    factory = shell_tool_factory if needs_shell else default_tool_factory
    raw = store.latest_qualification(args.model, profile.digest)
    if raw is None or not raw.get("selected_harness"):
        print(f"Qualify the model first: 11vated qualify {args.model}", file=sys.stderr)
        return 2
    lab = HarnessLab(client, keep_alive=settings.keep_alive,
                     allow_command_checks=args.allow_code_execution)
    evaluation = SkillEvaluator(lab, store).evaluate(
        args.model, profile, str(raw["selected_harness"]), skill, trials=args.trials,
        tool_factory=factory, progress=_say)
    print(f"{skill.skill_id} v{skill.version} on {args.model} [{evaluation.harness}]: "
          f"baseline {evaluation.baseline_passed}/{evaluation.attempts}, with skill "
          f"{evaluation.skill_passed}/{evaluation.attempts} -> {evaluation.outcome.upper()} "
          f"({'accepted' if evaluation.accepted else 'rejected'})")
    return 0 if evaluation.accepted else 1


def _accept(args: argparse.Namespace, paths: Any, settings: Any, client: Any, repository: Any,
            store: AgentStore) -> int:
    requested = tuple(item.strip().upper() for item in args.scenarios.split(",") if item.strip())
    unknown = [item for item in requested if item not in SCENARIOS and item not in NOT_IMPLEMENTED]
    if unknown:
        print("Unknown scenario(s): " + ", ".join(unknown), file=sys.stderr)
        return 2
    work_root = paths.workspaces_dir / "acceptance"
    work_root.mkdir(parents=True, exist_ok=True)
    runner = AcceptanceRunner(
        client, repository, store, work_root, base_url=settings.ollama_base_url,
        keep_alive=settings.keep_alive, trials=args.trials,
        allow_code_execution=args.allow_code_execution, progress=_say,
    )
    results = runner.run(requested, list(args.models))
    generated_at = results[-1].finished_at if results else ""
    report = {
        "generated_at": generated_at,
        "ollama_version": _safe_version(client), "models": list(args.models),
        "results": [item.to_dict() for item in results],
    }
    destination = args.output or (
        paths.profiles_dir / "acceptance" / f"acceptance-{generated_at[:19].replace(':', '')}.json")
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(destination)
    print()
    for item in results:
        print(f"Scenario {item.scenario}  {item.status.upper():<13} {item.title}"
              + (f" [{item.model}]" if item.model else ""))
        print(f"    {item.summary}")
        for issue in item.evidence.get("issues", []):
            print(f"      - {issue}")
    print(f"\nEvidence written to {destination}")
    return 0 if results and all(item.status == "passed" for item in results) else 1


def _safe_version(client: Any) -> str:
    try:
        return str(client.version())
    except Exception:
        return "unavailable"
