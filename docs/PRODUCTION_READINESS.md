# Production architecture and release gates

**Product:** 11vated Intelligence — Ollama Agent Workstation  
**Release baseline:** 0.2.0.dev0 (pre-release increment on the 0.1.0 foundation)  
**Development increment:** Model Intelligence Engine v0.2  
**Status:** Active engineering branch; not production-certified.

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

The current WorkforceMatcher ranks saved model profiles for a requested canonical identity role using observed capability scores, sample confidence and evidence coverage. It returns a provisional score and missing capability list. It does not currently consider tool grants, context-window fit, latency, memory budgets, hardware constraints or historical reliability, and it does not yet provide a full selection/rejection explanation. Those are planned workforce-scheduler inputs.

Delegation is not a security boundary. Each child agent must receive only its explicitly granted tools and workspace scope. The orchestrator must not be able to elevate a child agent's privileges.

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
| G0 — Repository foundation | CI, typed boundaries, install and launch | Implemented baseline; CI must be green |
| G1 — Model dossier | Publisher fields, source links, explicit match confirmation, hash, unknown handling | v0.2 increment implemented; needs CI and live model-card validation |
| G2 — Empirical profiling | Repeated tests, host/GPU telemetry, expanded task suites, repeatability, confidence and resource metrics | Partial; repeated smoke probes and best-effort telemetry implemented, specialist suites remain |
| G3 — Agentification | Protocol adapters, strict host-side tool argument validation, bounded recovery and digest binding | Partial; native/structured/guided paths and workspace tool schema validation implemented |
| G4 — Safe execution | OS sandbox, permission broker, cancellation, audit and cleanup | Partial; active streamed Ollama requests are cancellable, commands use bounded pipe capture and best-effort process cleanup, tool budgets and secret filtering are host-enforced. OS sandbox, Windows Job Objects/AppContainer, CPU/RAM/process quotas and tamper-resistant audit remain open |
| G5 — Durable agents | Checkpointing, resumable runs, scoped memory retrieval and event store | Partial; scoped SQLite memory, credential-redacted memory writes, ordered per-event journaling and a desktop Run History view are implemented. Interrupted runs remain visible with a running status and their last events. Checkpoint/resume, restart recovery, vector retrieval and automatic memory consolidation remain incomplete |
| G6 — Multi-agent workforce | Delegation, shared state, scheduling, budgets and least privilege | Not complete |
| G7 — Interoperability | MCP client/server, ACP and browser/computer adapters | Not complete |
| G8 — Certification | Version-pinned benchmark adapters, audited tasks, controlled comparisons and regression gates | Not complete; research register establishes selection policy, no full benchmark adapter or role/agent certification yet |
| G9 — Distribution | Signed installer, SBOM, dependency audit, upgrade/uninstall and recovery | Partial |

A release may not be called production-certified until all applicable gates pass on the supported Windows hardware/software matrix. Feature presence alone is not proof of production readiness.
