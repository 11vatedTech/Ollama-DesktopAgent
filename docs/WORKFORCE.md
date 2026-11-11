# Workforce orchestration

**Status:** Implemented and locally verified against live Ollama. Not
production-certified; see the limits below.

The workforce subsystem turns model-to-role ranking into a host-controlled,
durable DAG execution engine. A model never schedules another model directly.
The host validates the plan, binds exact installed model digests, enforces step
and wall-clock budgets, applies approval boundaries, checks artifacts, and
retains a hash-chained event journal.

## Execution contract

```
objective
   |
validate DAG / roles / budgets / verification paths
   |
approve plan (and consequential task boundaries)
   |
for each topological task:
   bind exact model digest -> run bounded child runtime
   -> collect events and final status
   -> host artifact checks + optional independent verdict
   -> persist task/result/event
   |
aggregate verification -> completed | failed | verification_failed
```

A child is only considered successful when its runtime returns `completed` and
the host-owned checks pass. A valid artifact is necessary for the built-in
templates, but is not sufficient by itself: a verifier's `FAIL` verdict fails
the workflow even when its verification file exists.

## Plans

The built-in plans are `analysis` (analyse + verify) and `delivery` (analyse +
deliver + verify). A custom plan can be supplied with `--plan FILE`:

```json
{
  "objective": "Create greeting.txt at the workspace root.",
  "require_approval": true,
  "max_total_steps": 38,
  "max_wall_seconds": 600,
  "tasks": [
    {
      "task_id": "analyse",
      "role": "repository_analysis",
      "objective": "Inspect the workspace and write workforce/analysis.md.",
      "max_steps": 12,
      "verification": [{"kind": "file_nonempty", "target": "workforce/analysis.md"}]
    },
    {
      "task_id": "deliver",
      "role": "software_engineering",
      "objective": "Create greeting.txt with exact content, then workforce/result.md.",
      "depends_on": ["analyse"],
      "max_steps": 14,
      "requires_approval": true,
      "verification": [
        {"kind": "file_contains", "target": "greeting.txt", "expected": "hello workforce"},
        {"kind": "file_nonempty", "target": "workforce/result.md"}
      ]
    },
    {
      "task_id": "verify",
      "role": "repository_analysis",
      "objective": "Inspect the deliverables and write workforce/verification.md.",
      "depends_on": ["deliver"],
      "max_steps": 12,
      "verification": [{"kind": "file_contains", "target": "workforce/verification.md", "expected": "VERDICT:"}],
      "verdict_required": true
    }
  ]
}
```

Supported checks are `file_exists`, `file_nonempty`, `file_contains` and
`json_keys`. Verification targets must be relative and remain inside the
workspace; `..`, absolute paths, Windows drive paths and `~` are rejected
during plan validation. Plans are limited to 16 tasks and 160 total steps.

## Host guarantees

- **Model plans are untrusted.** Plans are parsed as data, validated for unique
  IDs, known dependencies, cycles, non-empty roles/objectives, bounded steps,
  bounded wall time and safe artifact paths.
- **Exact model binding.** If installed-model references are supplied, the host
  checks the task's profile digest against the live Ollama digest before the
  child starts.
- **Least privilege.** A child receives `WorkspaceTools` scoped to the selected
  workspace. A read-only task receives a registry that contains only list,
  read and search tools; it cannot write or run commands.
- **Budgets.** Per-task step limits and timeout limits are clamped by the plan
  budget. A task that stops at its step limit is reported as `step_limit` and
  fails; it is never retried or presented as complete.
- **Approval.** The plan and tasks marked `requires_approval` need an explicit
  approval callback (`--yes` for CLI automation, or the desktop confirmation).
  The desktop Workforce page displays the plan summary before execution.
- **Supervision.** Each child runs in a supervised thread. Cancellation is
  propagated to the child runtime and its tools; a timed-out child is marked
  `timed_out` and cannot become a successful dependency.
- **Retry safety.** Retries occur only for transient child failure on a task
  marked `retry_safe`. Artifact/verdict failures and deterministic step-limit
  termination are not retried, because replaying them can duplicate side
  effects or conceal the cause.
- **Dependency context.** A child receives prior results explicitly labelled as
  untrusted evidence. It cannot gain permissions from another child's prose.

## Independent verification

The verifier is a separate task with its own bounded runtime, usually the same
workspace, and a host-required `VERDICT: PASS` or `VERDICT: FAIL` signal. The
host reads an existing verdict artifact first. If the verifier returns a
verdict in its final message but omits the requested audit file, the host
persists that verdict as `workforce/verification.md`; it never overwrites an
existing artifact. This preserves the audit record without accepting a
verdict that contradicts the artifact.

The aggregate verification result contains every check with its task ID, all
verdicts and a fail-closed status. A missing artifact, failed check, `FAIL`
verdict or failed/blocked task makes the workflow fail.

## Durable tracing

Every workforce run, task, result and event is stored in SQLite. Events are
append-only at the application layer and form a SHA-256 chain over run ID,
sequence, kind, message, canonical JSON data, timestamp and the preceding
event hash. `verify_workforce_event_chain()` detects altered rows. Run records
include the plan, owner ID, status, completion time and final result.

Executions append `task_started`, child runtime events, `task_finished`,
`task_blocked`, `task_failed` and `workforce_completed` events. Event messages
and metadata pass through the same credential-pattern redaction used for model
output. The chain is tamper-evident within the application database, not
tamper-proof against an attacker who can rewrite both the database and code.

## CLI

```powershell
# Host-selected evidence-backed assignment
11vated workforce run "Inspect this repository and document its architecture" .\workspace

# One exact model for every task
11vated workforce run "Verify the documented interfaces" .\workspace --mode analysis --model qwen2.5-coder:7b --yes

# Different models per role
11vated workforce run "Implement and verify the change" .\workspace --mode delivery `
  --assign repository_analysis=analyst:latest --assign software_engineering=coder:7b --yes

# Custom validated plan
11vated workforce run "Create the requested artifact" .\workspace --plan .\plan.json --model qwen2.5-coder:7b --yes

11vated workforce status
11vated workforce status RUN_ID
```

`--yes` is an explicit automation approval; it must only be used for tasks
whose consequences the operator has reviewed. Without it the CLI prompts.

## Desktop

The desktop has a **Workforce** page with objective, workspace, exact model,
analysis/delivery mode, review-and-run, stop, durable run list, task table and
event journal. The model must already have a saved profile whose digest
matches the installed model. The page acquires a durable run lease before
execution and releases it afterward. It uses the same `WorkforceOrchestrator`
service as the CLI; no scheduling or policy logic lives in the widget.

## Live local evidence

On 2026-11-11, with Ollama 0.40.15 and `qwen2.5-coder:7b`, a custom three-task
delivery workflow completed successfully:

- Run ID: `cb917844-fcc4-4080-b7bd-eee6082aa523`
- Analysis task: completed, `workforce/analysis.md` non-empty.
- Delivery task: completed, root `greeting.txt` contained `hello workforce`,
  `workforce/result.md` non-empty.
- Verifier: independent task returned `VERDICT: PASS`; verification artifact
  present and host checks passed.
- CLI exit code: 0; elapsed: 17 seconds.

This is live local evidence, not a hosted CI result or a production
certification.

## Limits

- Plans are built from static templates or a supplied JSON file. There is no
  model-generated planner that can execute without host validation.
- DAG tasks execute sequentially. There is no parallel scheduler, distributed
  worker pool, resource quota, GPU admission control or cross-machine lease.
- Child agents share a controlled workspace. The read-only wrapper is a
  tool-policy boundary, not an OS sandbox; a child that needs write access can
  still modify its scoped workspace within host approval rules.
- Verification checks file artifacts and a model verdict. The verifier is not a
  formal theorem prover and may be wrong; human review remains necessary for
  consequential deliverables.
- The workforce database and event journal are not encrypted at rest as a
  whole. Checkpoint payloads are AES-256-GCM encrypted separately; the event
  journal is integrity-chained and redacted but stored in SQLite.
- Inter-agent communication is limited to dependency result context plus the
  shared host workspace. There is no private message bus or capability token
  exchange.
- Desktop Workforce execution uses workspace tools only; MCP tools are
  available to single-agent runs but are not yet injected into the workforce
  tool factory.
