# Production architecture and release gates

**Product:** 11vated Intelligence — Ollama Agent Workstation  
**Architecture baseline:** 0.1.0  
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

An exportable package binds:
- Provider, exact model tag and digest.
- Profile version and source evidence.
- Title, mission, primary and secondary roles.
- Capability scores, attempts, confidence and methods.
- Selected harness and its qualification state.
- Tool grants and workspace scope.
- Memory policy and resource envelope.
- Known limitations and certification evidence.

A package must not silently grant permissions. Importing an agent identity never authorizes shell, browser, network, desktop or secret access.

## Universal Agentification Engine

### Harness selection

The engine evaluates protocols rather than assuming all models implement native tool calls:

1. Native tool-call harness, only when protocol tests pass.
2. Schema-constrained structured-action harness.
3. Guided text-action / ReAct harness with strict parser and bounded recovery.
4. Shell-oriented harness only inside an OS-enforced sandbox and with explicit grants.
5. Code-action harness only in a disposable isolated execution environment.

The chosen harness is bound to a specific model digest, Ollama version, prompt/schema version, tool manifest, policy version and environment. A model update invalidates affected qualification records.

### Execution lifecycle

Objective → plan → permission check → action proposal → host validation → execution → observation → recovery/continue → independent verification → result.

Required runtime properties:
- Durable state and resumable runs.
- Cancellation and process-tree cleanup.
- Per-run deadlines and bounded steps.
- Typed tools with JSON Schema validation.
- Idempotency keys for retryable operations.
- Context compaction with retained task invariants.
- Event traces with timestamps and artifact references.
- Independent verification before reporting success.
- Explicit human approval for consequential actions.

### Workforce composition

The workforce matcher ranks available agents by verified role suitability, task requirements, context fit, tool grants, latency, memory budget, hardware constraints and historical reliability. It must expose why an agent was selected and why alternatives were rejected.

Delegation is not a security boundary. Each child agent receives only its explicitly granted tools and workspace scope. The orchestrator cannot elevate a child agent's privileges.

## Benchmark architecture

| Layer | Reference suite | What it validates |
|---|---|---|
| Language/model tasks | Inspect AI, LM Evaluation Harness | Controlled task accuracy and scoring |
| Function calling | BFCL / Gorilla | Tool choice, argument correctness and multi-turn use |
| Repository engineering | SWE-bench Verified | Issue resolution against real repositories |
| Terminal operations | Terminal-Bench, TUA-Bench | Multi-step command-line work |
| Computer use | OSWorld | Desktop application operation |
| Browser work | Browser Use benchmark | Browser task completion |
| Product-specific | 11Vated Agent Arena | Identity, recovery, verification, resource and autonomy behavior |

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

The current shell allow-list is not an OS sandbox. Keep shell disabled for untrusted tasks until sandbox enforcement is implemented and independently tested.

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
| G2 — Empirical profiling | Repeated tests, expanded task suites, repeatability, confidence and resource metrics | Partial; smoke probes only |
| G3 — Agentification | Protocol adapters, schema validation, bounded recovery and digest binding | Partial; initial native/structured/guided paths |
| G4 — Safe execution | OS sandbox, permission broker, cancellation, audit and cleanup | Not production-ready |
| G5 — Durable agents | Checkpointing, resumable runs, memory retrieval and event store | Not complete |
| G6 — Multi-agent workforce | Delegation, shared state, scheduling, budgets and least privilege | Not complete |
| G7 — Interoperability | MCP client/server, ACP and browser/computer adapters | Not complete |
| G8 — Certification | Benchmark adapters, controlled comparisons and regression gates | Not complete |
| G9 — Distribution | Signed installer, SBOM, dependency audit, upgrade/uninstall and recovery | Partial |

A release may not be called production-certified until all applicable gates pass on the supported Windows hardware/software matrix. Feature presence alone is not proof of production readiness.
