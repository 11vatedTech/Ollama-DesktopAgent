# Production architecture and release gates

**Product:** 11vated Intelligence — Ollama Agent Workstation  
**Release baseline:** 0.2.0.dev0 (pre-release increment on the 0.1.0 foundation)  
**Development increment:** Model Intelligence Engine v0.2  
**Status:** Active engineering branch; locally verified, not production-certified.

## Product north star

Turn any compatible local Ollama model into a purpose-fit, measurable, policy-bounded agent. The workstation owns the intelligence dossier, empirical evaluation, identity assignment, harness selection, execution policy, evidence trail and workforce routing. Ollama remains the inference provider.

The differentiator is not merely local chat, MCP support or a model list. Goose already demonstrates local model providers, MCP extensions, recipes and subagents. Our defensible product thesis is the combination of:

1. Per-model origin dossiers with traceable publisher disclosures.
2. Reproducible capability and operational profiling.
3. Model-specific professional identities based on observed evidence.
4. Adaptive model-to-agent harness selection.
5. Agent certification tied to the exact model digest, harness, tools and environment.
6. Evidence-driven workforce matching and verified autonomy reporting.

## System context

    PySide6 Desktop / CLI
            |
    Application services and domain events
            |
    +----------------------+---------------------------+
    | Model Intelligence   | Agent Runtime             |
    | inventory + dossier  | planner + state + harness |
    | profiling + identity | tool loop + recovery      |
    +----------+-----------+-------------+-------------+
               |                         |
          Ollama adapter             Policy gateway
               |                         |
          Ollama API          workspace / shell / MCP / browser
               |                         |
               +------------+------------+
                            |
                  Verification & traces
                            |
                     SQLite / artifacts
                            |
                   Workforce scheduler

The UI must remain a client of application services. Provider-specific payloads, role scoring and permission enforcement do not belong in widgets.

### Desktop intelligence interface

The current PySide6 shell is a nine-workspace client of the same application services used by the CLI. Shared tokens and components provide a coherent surface; the intelligence core, chat states, workforce graph and telemetry are driven by real local events. Responsive layouts collapse Chat context and reflow Command/Workforce, and reduced motion is available. See [Desktop Intelligence Interface](UI_INTERFACE.md) for the design contract, screenshot gallery and validation matrix.

This is implemented and locally tested, not a production-certified client. The current native matrix covers 1480 × 940, 1120 × 720, 1280 × 800, 1024 × 768 and 900 × 700, including the vertical Model Intelligence split. Full screen-reader/high-contrast audit, clean-VM visual QA, screenshot regression testing and end-to-end frame pacing remain open. A fresh hosted-CI run for this increment is also pending.

## Model Intelligence & Identity Engine

### Origin dossier

Collect and preserve, when explicitly available:

- Exact Ollama model tag, digest, size, quantization and modification time.
- Family, architecture, parameter count and context metadata.
- Ollama-declared capabilities.
- User-confirmed upstream publisher repository.
- Publisher model description and intended use.
- Publisher-declared base model, datasets, languages, license and tags.
- Disclosed training details and evaluation/benchmark sections.
- Publisher-disclosed risks and limitations.
- Canonical source URLs, retrieval timestamp and model-card SHA-256.

Evidence levels are documented, observed, certified, inferred and unknown. Model-card search results are suggestions. The user must select the upstream repository. Lexical similarity is a transparent hint only and never establishes identity.

The system must not claim to know an undisclosed training corpus. A missing dataset disclosure remains unknown; it is not evidence that no dataset was used.

### Empirical capability profile

Capability dimensions are independent and can be passed, partial, failed, not_assessed or unsupported. A model is not assigned zero for an untested dimension.

Current smoke dimensions include instruction following, JSON schema output, structured actions, native tool calls, code generation, code comprehension, reasoning and arithmetic. Additional role dimensions include research, long-context handling, creative writing, multilingual behavior, data extraction, planning, vision, audio and error recovery. These additional dimensions remain unassessed until dedicated suites exist.

The v0.2 increment supports 1–5 repeated smoke trials, records attempts/passes, aggregates partial scores and increases sample confidence conservatively. It does not make a smoke suite equivalent to role qualification.

### Identity assignment

Professional titles and roles are deterministic, explainable host-side classifications. The classifier uses measured capability scores, sample confidence and role coverage. Publisher statements inform the declared purpose but never increase empirical scores.

Examples include Repository Cartographer, Precision Implementation Engineer, Native Tool Operations Specialist, Evidence Synthesis Analyst, Precision Data Extraction Specialist, Creative Narrative Architect, Multilingual Communication Specialist, Strategic Workflow Planner and Visual Intelligence Inspector.

The title is a proposed operational identity, not a claim about sentience, personality or intrinsic expertise. One-shot profiles remain provisional. Titles must include a rationale and visible limitations.

### Model routing

`models/model_intelligence.py` parses Ollama `/api/show` metadata: family/architecture, parameter count, quantization, context length, embedding width, attention/KV geometry, declared capabilities and inferred memory envelopes. `models/routing.py` ranks installed artifacts by required capability, context, estimated memory, observed quality, speed preference, exclusions and explicit fallback chain.

A route distinguishes observed capability from declared-only metadata. Declared metadata may support a provisional route, but a request with `require_verified=True` rejects it; the decision reason and `details["declared_only"]` make the distinction visible. Hardware estimates are inferred, not measured. A route is not an execution guarantee until the selected model digest is bound and the child run succeeds.

### Agent Identity Package

The current schema-1.0 exporter emits:
- Package type, schema version and creation timestamp.
- Identity title, primary/secondary specialization, recommended role keys, rationale and confidence.
- Ollama provider, model name, digest, family, parameter size and quantization.
- Agentability mode/status and the default permission label.
- Capability name, status, score, confidence, method, metrics, evidence and limitations.
- Publisher/origin evidence, known limitations and profile version.

The current exporter does **not** include capability attempt/pass counts, a concrete tool-grant manifest, an assigned workspace, a memory policy, a resource envelope, or a signed certification record. Those are planned package-version fields, not recoverable from the current export.

A package must not silently grant permissions. The current default permission label is descriptive metadata; it is not an imported authorization. Importing an agent identity never authorizes shell, browser, network, desktop or secret access.

## Universal Agentification Engine

### Harness selection

The engine evaluates protocols rather than assuming all models implement native tool calls:

1. Native tool-call harness, only when protocol tests pass.
2. Schema-constrained structured-action harness.
3. Guided text-action / ReAct harness with strict parser and bounded recovery.
4. Shell-oriented harness only inside an OS-enforced sandbox and with explicit grants.
5. Code-action harness only in a disposable isolated execution environment.

A production qualification record must bind a chosen harness to a specific model digest, Ollama version, prompt/schema version, tool manifest, policy version and environment. The v0.2 CLI and desktop run paths now compare the currently installed Ollama digest with the saved profile digest and refuse execution when either digest is missing or they differ. Ollama-version, prompt/schema, tool-manifest, policy-version and environment-based qualification invalidation remain planned because the current profile does not yet store a complete harness certification record. Re-profile after replacing or updating a model artifact.

### Execution lifecycle

Objective → plan → permission check → action proposal → host validation → execution → observation → recovery/continue → independent verification → result.

Required runtime properties:
- Durable state and resumable runs.
- Desktop cancellation now closes the active Ollama streaming response and checks cancellation between tool operations. Approved command execution accepts the same cancellation event and uses process-group/taskkill cleanup on timeout or stop. Windows descendant cleanup remains best-effort; these are not OS isolation boundaries.
- Per-run deadlines and bounded steps. Current runtime adds a maximum of 8 tool calls per step, 64 per run, rejects an oversized response batch above 32 calls, and counts serialized tool-call JSON in the conversation budget.
- Approved command pipes are drained concurrently into bounded 256 KB tail buffers; only the final 12 KB per stream is exposed to the agent, with truncation reported. This bounds captured output memory, not the process's own memory or CPU.
- Typed tools with JSON Schema validation.
- Idempotency keys for retryable operations.
- Context compaction with retained task invariants.
- Event traces with timestamps and artifact references.
- Independent verification before reporting success.
- Explicit human approval for consequential actions.

### Workforce composition

The current `WorkforceMatcher` ranks saved model profiles for a requested canonical identity role using observed capability scores, sample confidence and evidence coverage, and returns a provisional score and missing capability list. `WorkforceSelector` adds explicit rejection reasons, preferred-model hints and distinct-model preference when selecting assignments. Routing separately considers declared model metadata, context and estimated memory; that provisional metadata never becomes empirical evidence.

The host-owned `WorkforceOrchestrator` validates a JSON plan/DAG, checks unique IDs, dependencies and cycles, clamps task and wall-clock budgets, binds installed model digests when available, and executes children sequentially in a shared controlled workspace. Each child receives a tool registry derived from the task's read-only flag and the workspace policy. Tasks marked `requires_approval` and the plan itself need explicit approval. Step-limit termination, missing artifacts, tamper and substantive verifier `FAIL` are failures. The verifier is read-only and receives an immutable acceptance manifest; its typed JSON evidence is host-validated, and one provider-enforced schema pass can normalize prose. A factual “missing/empty” model claim contradicted by the manifest is recorded as an override, never silently accepted. See [Workforce orchestration](WORKFORCE.md).

Delegation is not a security boundary. Each child receives only its tool registry and workspace scope; the orchestrator cannot grant a child a tool that the host policy denies. Children currently share one workspace, so a write-capable child can modify files visible to siblings. The read-only wrapper is a tool-policy boundary, not an OS sandbox.

The workforce event journal is append-only at the application layer and hash-chained across sequence, kind, message, canonical JSON data, timestamp and preceding hash. A separate verifier task returns a typed verdict over a host-owned immutable manifest; artifact checks are host-evaluated and the audit file is written by the host. The journal is tamper-evident, not tamper-proof against an attacker who can rewrite both storage and code.

## Benchmark architecture

| Layer | Reference suite | What it validates |
|---|---|---|
| Language/model tasks | Inspect AI, LM Evaluation Harness | Controlled task accuracy and scoring |
| Function calling | BFCL V4 plus an independent 11vated tool suite | Tool choice, argument correctness, multi-turn and agentic use |
| Repository engineering | SWE-bench Pro V2 (pinned protocol) plus private/held-out repository tasks | Issue resolution and regression behavior |
| Terminal operations | TUA-Bench | 120 execution-based terminal workflow tasks; task-set revision must be pinned |
| Computer use | OSWorld 2.0 (exact version pinned) | Desktop application operation |
| Browser work | Browser Use BU Bench V2.1.1 (exact dataset/runner pinned) | 200 web tasks; legacy 60-task results are not comparable |
| Product-specific | 11Vated Agent Arena | Identity, recovery, verification, resource and autonomy behavior |

Benchmark selection is version-aware. The research register documents known validity issues: do not rely on SWE-bench Verified as a headline coding measure; do not mix SWE-bench Pro v1 with V2; and treat BFCL V4 as one diagnostic signal because a September 2026 review found substantial issues in its audited sample. Every external benchmark must undergo protocol, dataset and task-quality review before it is used as a certification gate.
### Comparison protocol

For a controlled comparison, freeze exact model digest, quantization, prompt, context budget, task set, tool manifest, environment, timeout and scorer. Compare raw chat, native tools, structured actions, guided ReAct, eligible reference harnesses and 11vated Agentification using the same inputs.

Use repeated trials and publish medians, p95, variance and confidence intervals. Infrastructure exclusions must be predeclared. The model under test cannot be its own sole judge.

Primary metric:

Verified Autonomy Rate = independently verified completed tasks / tasks attempted.

Also report human interventions, unauthorized-action attempts, tool validity, recovery success, latency, tokens, memory peak, run variance and evidence coverage.

## Security baseline

The model and all retrieved content are untrusted. Only host code can authorize actions.

Production execution requires:
- OS-level isolation (Windows AppContainer/Job Objects or disposable VM/container with restricted ACLs).
- Separate read, write, delete, execute, network, GUI and credential grants.
- Deny-by-default policy and approval for destructive or external actions.
- No secrets in prompts, logs or model-visible tool output.
- Sanitized observations and prompt-injection-aware tool contracts.
- CPU, RAM, disk, process-count and wall-clock quotas.
- Cancellation and process-tree termination.
- Immutable audit events and artifact hashes.
- MCP server provenance, transport authentication and per-server grants.
- SBOM, dependency/license scanning, vulnerability review and signed releases.

The current command allow-list is not an OS sandbox. Keep command execution disabled for untrusted tasks until sandbox enforcement is implemented and independently tested. Workspace tool arguments are checked against host-owned required fields, types, enums and additional-property rules. Common credential stores and private-key paths are blocked by default, with known template-file exceptions. Pattern-based secret redaction is applied to ordinary file/search/command output, stored memory, event data, persisted task descriptions and final responses; this heuristic is not a complete secret scanner or data-loss-prevention system. Desktop cancellation closes the Ollama HTTP stream; POSIX command process groups are terminated, while Windows taskkill cleanup is best-effort and not a Job Object/AppContainer boundary.

## Competitive research register

Primary source repositories:
- Goose: https://github.com/aaif-goose/goose
- Interpreter Workstation: https://github.com/openinterpreter/interpreter-workstation
- Open Interpreter: https://github.com/openinterpreter/openinterpreter
- OpenHands SDK: https://github.com/OpenHands/software-agent-sdk
- Codex CLI: https://github.com/openai/codex
- Grok Build: https://github.com/xai-org/grok-build
- OpenCode: https://github.com/anomalyco/opencode
- Cline: https://github.com/cline/cline
- mini-SWE-agent: https://github.com/SWE-agent/mini-swe-agent
- Agent Zero: https://github.com/agent0ai/agent-zero
- Hermes Agent: https://github.com/NousResearch/hermes-agent
- LangGraph: https://github.com/langchain-ai/langgraph
- smolagents: https://github.com/huggingface/smolagents
- Letta: https://github.com/letta-ai/letta
- Mem0: https://github.com/mem0ai/mem0
- Browser Use: https://github.com/browser-use/browser-use
- Inspect AI: https://github.com/UKGovernmentBEIS/inspect_ai
- LM Evaluation Harness: https://github.com/EleutherAI/lm-evaluation-harness
- Ollama: https://github.com/ollama/ollama
- MCP specification: https://github.com/modelcontextprotocol/modelcontextprotocol
- ACP: https://github.com/agentclientprotocol/agent-client-protocol

These are research references, not approved runtime dependencies. For every proposed reuse, record exact commit SHA, license text, notices, dependency tree, platform support, security posture and Ollama compatibility. Do not vendor code solely because a repository is popular.

## Release gates

| Gate | Required evidence | Current state |
|---|---|---|
| G0 — Repository foundation | CI, typed boundaries, install and launch | Implemented baseline; hosted CI is green for the previous pushed head (run 42977911248); a fresh matrix for the current increment is pending |
| G1 — Model dossier | Publisher fields, source links, explicit match confirmation, hash, unknown handling | v0.2 increment implemented; 15 live metadata profiles now preserve provenance and unknown values; needs fresh CI and broader live model-card validation |
| G2 — Empirical profiling | Repeated tests, host/GPU telemetry, expanded task suites, repeatability, confidence and resource metrics | Partial; repeated smoke probes and a six-case deterministic 11vated-core runner with host-side scoring, per-trial evidence and a hashed manifest are implemented. Specialist/held-out suites and role thresholds remain open |
| G3 — Agentification | Protocol adapters, strict host-side tool argument validation, bounded recovery and digest binding | Partial; native/structured/guided paths and workspace tool schema validation implemented |
| G4 — Safe execution | OS sandbox, permission broker, cancellation, audit and cleanup | Partial; active streamed Ollama requests are cancellable, commands use bounded pipe capture and best-effort process cleanup, tool budgets and secret filtering are host-enforced. OS sandbox, Windows Job Objects/AppContainer, CPU/RAM/process quotas and tamper-resistant audit remain open |
| G5 — Durable agents | Checkpointing, resumable runs, scoped memory retrieval and event store | Implemented for local runs: integrity-hashed safe-boundary checkpoints bind model digest, workspace, task, harness and tool manifest; checkpoint payloads are AES-256-GCM encrypted with a separate 32-byte local key; desktop and CLI recovery require explicit confirmation; interrupted tool batches become `needs_review` and are never replayed; corrupt, stale, superseded or mismatched checkpoints fail closed. Atomic leases block concurrent CLI/desktop resume and workforce execution for the same run ID; expired leases are reclaimable. Scoped SQLite memory, ordered journaling, Run History and hash-chained workforce events are implemented. Vector retrieval, automatic memory consolidation and encrypted whole-database storage remain open |
| G6 — Multi-agent workforce | Delegation, shared state, scheduling, budgets and least privilege | Implemented and locally verified, not production-certified: validated DAG plans, sequential delegation, shared workspace policy, per-task step/time budgets, cancellation, retry-safe failure handling, plan/task approval, immutable-manifest verification, schema-enforced typed verdicts, host audit and durable hash-chained task/child events. CLI/desktop success and fail-closed runs are demonstrated. Parallel/distributed scheduling, resource quotas, per-child workspace isolation and an OS sandbox remain open |
| G7 — Interoperability | MCP client/server, ACP and browser/computer adapters | Partial; optional SDK v2 MCP tool client supports stdio and Streamable HTTP with exact tool grants, host JSON Schema validation, bounded/cancellable calls, output redaction, fail-closed reconnect and versioned transport diagnostics. Real SDK integration tests cover discovery, invocation, timeout, cancellation, crash, reconnection, schema drift, concurrent sessions and process/session cleanup locally on Windows. The Linux half of the CI matrix is configured but has not yet run on this branch. OAuth/authorization, protected remote servers, resources/prompts, MCP server, ACP and browser/computer-use adapters remain open |
| G8 — Certification | Version-pinned benchmark adapters, audited tasks, controlled comparisons and regression gates | Partial foundation; the six-case local core runner is versioned and independently scored. External benchmark adapters, audited specialist task banks, controlled multi-harness comparisons, role/agent certification and release regression thresholds remain incomplete |
| G9 — Distribution | Signed installer, SBOM, dependency audit, upgrade/uninstall and recovery | Partial |

## Local verification evidence

On the Windows development host (Python 3.12.13, Ollama 0.40.16, 15 installed models):

- Ruff and `compileall` pass.
- The full local pytest suite reports **185 passed / 4 skipped**. Opt-in live Ollama tests are skipped in the ordinary run. Python 3.11 and fresh hosted coverage for this increment remain to be rerun after push.
- Ten focused desktop UI tests pass under offscreen Qt: all nine workspace constructors, responsive Command/Chat/Model behavior, intelligence-core states and reduced-motion timing, Markdown escaping, conversation persistence and Settings values.
- Twenty-four native Windows gallery captures were inspected at 1480 × 940, 1120 × 720, 1280 × 800, 1024 × 768 and 900 × 700 logical windows: nine desktop workspaces, compact Command/Chat/Workforce, and the responsive matrix. The measured 410 × 410 core paint sample averaged 5.77 ms per frame (p95 8.01 ms); no end-to-end FPS claim is made.
- Live provider checks cover real version/inventory, `/api/show` metadata, non-empty chat completion and 768-dimensional embeddings. A real `qwen2.5-coder:7b` desktop chat returned `STREAM_OK`; an active stream was stopped and surfaced as `Generation stopped`.
- Inventory coverage: 15/15 models have persistent metadata profiles; one (`qwen2.5-coder:7b`) has empirical probe evidence. Its two-repeat profile used 16 calls at 44.22 tok/s mean; reasoning/structured output/actions passed, tool calling and vision were unsupported, code generation failed, and long-context/research/planning/error-recovery/audio remain unassessed. The two-repeat core benchmark scored 8/12 (66.67) with manifest `9e68732a7f0a648ab6cfaa6584d897be1abed8bbb32de59142a8eead52947f79`; the summary is now attached to the profile and available to routing.
- Live CLI analysis run `23dc040f-1aa3-40d1-882a-b3a2c7f6b6e0` completed with `verification_passed=true`; prose was normalized through the typed schema and the host wrote the audit.
- Live desktop `WorkforcePage` run `beee5cd1-342a-449b-994b-e47d3a53d9be` completed with `verification_passed=true`. Its audit preserves the model's `FAIL`, records the factual contradiction against the nonempty immutable manifest, and records host decision `PASS` with `host_override=true`.
- Live desktop failure run `fb65bbb1-064e-4be6-9937-a40c2d3acd30` correctly reported `verification_failed` when the analysis model claimed a write without creating the artifact. The host blocked the verifier rather than manufacturing success.
- Real MCP stdio and Streamable HTTP integration tests passed locally on Windows in the preceding run; they were not rerun for this documentation-only increment. Hosted CI run [42977911248](https://github.com/11vatedTech/Ollama-DesktopAgent/actions/runs/42977911248) passed all four jobs at the previous pushed head; this increment needs a fresh matrix result.

A release may not be called production-certified until all applicable gates pass on the supported Windows hardware/software matrix. Feature presence alone is not proof of production readiness.
