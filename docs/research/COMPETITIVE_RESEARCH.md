# Competitive research register

**Snapshot:** 2026-11-10. This is a research and architecture register, not a claim that every candidate has been audited at every source line. Re-check current default branches, releases, license texts, dependency notices and platform support before reuse.

## Product thesis

The product opportunity is not another Ollama chat GUI. It is an agent operating layer that makes local models operational through adaptive harnesses, host-enforced tools, persistent state, verification and evidence-backed model identities.

## Tier 1 — Closest desktop/workstation references

| Candidate | Source | Why study it | Reuse / risk notes |
|---|---|---|---|
| Goose (Block / Agentic AI Foundation) | https://github.com/aaif-goose/goose | Desktop, CLI/API, Ollama/provider adapters, MCP extensions, subagents, recipes, policy | Apache-2.0; inspect current provider and GDK APIs. Local-agent product closest to our goal. |
| Interpreter Workstation | https://github.com/openinterpreter/interpreter-workstation | Desktop shell, host adapters, local policy, browser bridge, durable goals | README separates desktop shell from Open Interpreter runtime/harness/provider discovery. Node/pnpm/Bun/Rust/submodules create build complexity. |
| Open Interpreter | https://github.com/openinterpreter/openinterpreter | Agent runtime and computer/workstation tools behind Interpreter Workstation | Inspect current harness and provider contracts; do not duplicate its provider layer in UI. |
| OpenHands Software Agent SDK | https://github.com/OpenHands/software-agent-sdk | Python/TS/REST SDK, agents, tools, conversations, workspaces, events, API server | MIT. Strong runtime candidate; coding focus, workspace sandbox and Agent Server operational footprint need validation. |

## Tier 2 — Execution harness and coding-agent references

| Candidate | Source | Main research target |
|---|---|---|
| OpenAI Codex CLI | https://github.com/openai/codex | Apache-2.0 Rust harness, tool loop, approvals, sandbox, threads, app server |
| Grok Build | https://github.com/xai-org/grok-build | Rust TUI/runtime/tools/workspace decomposition, ACP, checkpoints; its README has warned Windows source builds are best-effort/not tested |
| OpenCode | https://github.com/anomalyco/opencode | Provider adapters, plan/build modes, subagents, tool permission model |
| Cline | https://github.com/cline/cline | Shared SDK/CLI/desktop/IDE engine, MCP, teams and approval workflow |
| Aider | https://github.com/Aider-AI/aider | Repository map, context selection, patching and Git workflow |
| mini-SWE-agent | https://github.com/SWE-agent/mini-swe-agent | Minimal shell/action harness; demonstrates tool-calling-independent agent loop |
| Agent Zero | https://github.com/agent0ai/agent-zero | General-purpose workspace, tools, subagents, memory and computer environment |
| Hermes Agent | https://github.com/NousResearch/hermes-agent | Skills, tool use, persistent agent lifecycle and workflows |

## Tier 3 — Frameworks, memory and environment adapters

| Candidate | Source | Main research target |
|---|---|---|
| LangGraph | https://github.com/langchain-ai/langgraph | Stateful graphs, persistence, checkpointing and human interruption |
| smolagents | https://github.com/huggingface/smolagents | Code-agent and tool-agent execution abstractions |
| Letta | https://github.com/letta-ai/letta | Stateful memory and persistent agent identities |
| Letta Code | https://github.com/letta-ai/letta-code | Coding-agent experience and memory integration |
| Mem0 | https://github.com/mem0ai/mem0 | Memory extraction/retrieval; compare with local SQLite/FTS/embedding design |
| Browser Use | https://github.com/browser-use/browser-use | Browser state, actions, retries, screenshots and validation |
| Browser Harness | https://github.com/browser-use/browser-harness | Repeatable browser-agent evaluation |
| MCP | https://github.com/modelcontextprotocol/modelcontextprotocol | Tools/resources/prompts interoperability; pin supported spec/SDK |
| ACP | https://github.com/agentclientprotocol/agent-client-protocol | JSON-RPC agent-to-client session and update interoperability |

## Tier 4 — Model evaluation research

| Candidate | Source | Main research target |
|---|---|---|
| Inspect AI | https://github.com/UKGovernmentBEIS/inspect_ai | Composable datasets, solvers, tools, scorers, sandboxes and traces |
| Inspect Evals | https://github.com/UKGovernmentBEIS/inspect_evals | Reusable evaluation task suites |
| LM Evaluation Harness | https://github.com/EleutherAI/lm-evaluation-harness | Standardized academic model benchmarks |
| SWE-bench | https://github.com/SWE-bench/SWE-bench | Repository-level issue resolution |
| Terminal-Bench | https://github.com/laude-institute/terminal-bench | Multi-step terminal task execution |
| BFCL / Gorilla | https://github.com/ShishirPatil/gorilla | Function calling, argument generation, multi-round tool use |
| OSWorld | https://github.com/xlang-ai/OSWorld | Desktop/computer-use tasks |
| TUA-Bench | https://github.com/facebookresearch/TUA-Bench | Terminal-use task benchmark |

## Proprietary products and source-access limits

| Product | Source access | Research approach |
|---|---|---|
| Claude / Claude Code | Proprietary; the public Claude Agent SDK is not the complete Claude Code source | Study public docs and SDK contracts; do not claim source-level access |
| OpenAI Codex | Codex CLI is public; hosted Codex product/model services are not equivalent to the CLI repo | Study CLI source and public docs; isolate provider assumptions |
| Grok | Grok Build source is public; it is not the entire Grok assistant stack | Study Grok Build CLI/runtime and public documentation |
| Freebuff | User's project; source repository was not supplied as part of this project task | Product capability comparison only; source-level teardown requires the relevant source tree |
| OpenHands | Public SDK and application repositories | Inspect SDK/runtime, workspace backends, control surface and security |

## Source-level observations

### Goose

Official source/docs describe a local general-purpose agent with desktop, CLI and API surfaces, multiple model providers including Ollama, MCP extensions, subagents and recipes. Therefore "local models + MCP" is not a unique differentiator. Our defensible work must center on model-to-agent conversion, per-model harness selection, identity, certification and controlled agent teams.

### Interpreter Workstation

Its current README describes the separation between Open Interpreter (runtime, harnesses, provider discovery, app-server) and Workstation (desktop shell, host adapters, local policy and workstation tools). This validates the provider/runtime/UI separation in our architecture.

### OpenHands SDK

The SDK documents Python, TypeScript and REST APIs, with agents, tools, conversations, workspaces, events and both local and ephemeral workspace backends. It is a strong adapter/reference candidate, not an automatic dependency decision.

### Codex CLI and Grok Build

Both are useful source-level references for local agent execution, tool loops, terminal interaction, approvals and workspace management. Grok Build's published README has warned that source builds on Windows are best-effort/not tested. Verify the exact current revision and platform status before reuse.

### mini-SWE-agent

Its v2 project description emphasizes a simple shell-action interface that does not depend on native model tool calls. This is key evidence for the agentification thesis: tool calling is one interaction protocol, not the definition of an agent. Any shell harness must run under real OS sandboxing before untrusted tasks.

### Inspect AI

Inspect AI provides composable evaluation datasets, agents/solvers, tools, scorers, sandboxing and traces. It is a valuable research-grade evaluation substrate. Its sandbox and generated-code execution configuration must be reviewed before use.

## Qualitative fit scorecard

Scale 1 (weak) to 5 (strong) for our Windows-first, Ollama-first goal. This is an engineering judgment, not measured benchmark performance.

| Candidate | Desktop UX | Runtime modularity | Local/model openness | Windows relevance | Principal risk |
|---|---:|---:|---:|---:|---|
| Goose | 5 | 4 | 5 | 4 | Rust integration cost; evolving provider APIs |
| Interpreter Workstation | 5 | 4 | 4 | 3 | Node/Rust/Bun build stack and submodules |
| OpenHands SDK | 3 | 5 | 4 | 3 | Coding focus and workspace infrastructure complexity |
| Codex CLI | 4 | 4 | 2 | 4 | Provider/model assumptions |
| Grok Build | 4 | 4 | 2 | 2 | Windows source-build caveat and provenance review |
| OpenCode | 4 | 4 | 4 | 4 | Fast-moving TypeScript APIs |
| Cline | 5 | 4 | 4 | 4 | Monorepo/host complexity |
| mini-SWE-agent | 2 | 4 | 5 | 4 | Narrow scope; shell isolation required |
| LangGraph | 1 | 5 | 4 | 4 | Orchestration complexity |
| Inspect AI | 2 | 5 | 5 | 4 | Evaluation substrate, not a desktop runtime |

## Reuse and license gate

No candidate code is vendored in 0.1.0. Before importing a component, record exact commit SHA and license text; scan dependency licenses and notices; assess SBOM, security advisories, telemetry, network use, Windows compatibility, Ollama path, extension contracts and policy boundaries. Reuse architectural patterns without copying implementation where licensing/provenance is unclear.

## Research sources

- Ollama API: https://github.com/ollama/ollama/blob/main/docs/api.md
- Goose docs/source: https://block.github.io/goose/ and https://github.com/aaif-goose/goose
- Interpreter Workstation: https://github.com/openinterpreter/interpreter-workstation
- OpenHands SDK: https://github.com/OpenHands/software-agent-sdk
- Codex CLI: https://github.com/openai/codex
- Grok Build: https://github.com/xai-org/grok-build
- Cline: https://github.com/cline/cline
- mini-SWE-agent: https://github.com/SWE-agent/mini-swe-agent
- Inspect AI: https://inspect.aisi.org.uk/
- Hugging Face model cards: https://huggingface.co/docs/hub/model-cards
- BFCL: https://gorilla.cs.berkeley.edu/leaderboard.html
- SWE-bench: https://www.swebench.com/
