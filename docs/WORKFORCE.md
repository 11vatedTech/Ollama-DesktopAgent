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
templates, but is not sufficient by itself. A verifier's substantive `FAIL`
fails the workflow. A host override is permitted only when the typed
`failure_reasons` list is complete, every listed reason is an
`artifact_missing` or `artifact_empty` claim tied to an exact manifest path,
the host manifest independently disproves every such claim, the verifier cites
every manifest artifact, the manifest remains unchanged and every deterministic
acceptance check passes. Any substantive reason, unknown reason, missing
evidence or integrity error keeps the result fail-closed. Free-form summary
text alone can never authorize an override.

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
      "objective": "Inspect the immutable manifest, verify every acceptance criterion, and return the structured JSON verdict. Do not write any file.",
      "depends_on": ["deliver"],
      "max_steps": 12,
      "read_only": true,
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
  termination are not retried automatically, because replaying them can
  duplicate side effects or conceal the cause.
- **Dependency context.** A child receives prior results explicitly labelled as
  untrusted evidence. It cannot gain permissions from another child's prose.

## Independent verification

The verifier is a separate read-only task over the same workspace. Before it
starts, the host snapshots every acceptance target into an immutable
`ArtifactManifest` (path, size, SHA-256). The verifier receives the original
acceptance criteria and manifest, and may use only list/read/search tools. It
must return the typed `11vated-verifier/2` JSON contract with
`schema_version: 2`, a verdict, non-empty summary, artifact evidence and a
`failure_reasons` array. PASS requires an empty failure-reasons array and
evidence for every manifest artifact. FAIL must enumerate every blocking reason
as `artifact_missing`, `artifact_empty` or `substantive`; factual reason
types require an exact manifest path.

If the verifier returns prose instead of the contract, the host performs one
provider-enforced JSON-schema formatting call over the same immutable manifest.
The model cannot write the audit file. The host validates paths and digests,
rechecks that the artifacts did not change, and writes
`workforce/verification.json` with `recorded_by: host`, the model verdict,
typed failure reasons, manifest, host checks, errors and tamper check. A host
override requires complete evidence coverage and independent contradiction of
every typed factual failure reason. Any substantive or uncontradicted reason,
failed host check, changed artifact, missing evidence or malformed normalized
JSON fails closed. Summary prose cannot authorize an override.

The aggregate verification result contains every check with its task ID, all
verdicts, host overrides and a fail-closed status.

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

On 2026-11-11, with Ollama 0.40.16 and `qwen2.5-coder:7b`, both CLI and
desktop-controller analysis workflows completed with host verification:

- Run ID `23dc040f-1aa3-40d1-882a-b3a2c7f6b6e0` (CLI): analysis completed,
  `workforce/analysis.md` nonempty, verifier prose was normalized to a
  schema-enforced PASS, host audit recorded, `verification_passed=true`.
- Desktop `WorkforcePage` run `fb65bbb1...` failed correctly when the analysis
  model claimed success without writing the artifact; the host blocked the
  verifier. A clean rerun (`live-workforce-ui2`) completed with
  `verification_passed=true`; its audit records model verdict `FAIL`,
  `host_decision: PASS`, `host_override: true` for a contradicted “empty”
  claim, and the immutable manifest for `workforce/analysis.md`.
- The CLI acceptance run also covered the contradiction-safe path: a model
  prose FAIL that claimed the nonempty artifact was missing was recorded, but
  deterministic acceptance passed.

These are live local results, not hosted CI or production certification.

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
