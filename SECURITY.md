# Security policy and threat model

## Security objective

An agent may propose an action, but only host code can authorize and execute it. Model output, publisher model cards, retrieved memory, local documents, browser content and tool results are untrusted data.

The application is local-first, not automatically offline or sandboxed. A configured remote Ollama endpoint receives prompts and whatever project context the agent is permitted to read. Public model-card research is a separate, opt-in network feature.

## Assets and threats

Assets include source code, workspaces, local credentials, private keys, Git repositories, model files, local services, benchmark evidence and application state. Threats include prompt injection in documents/memory, hostile model output, compromised model packages, symlink/path escapes, malicious tool arguments, unsafe approved executables, malicious MCP servers, accidental destructive actions, stale model profiles and data disclosure to remote endpoints.

## Implemented controls in the 0.2 development branch

- Ollama defaults to loopback (127.0.0.1). A user-configured remote endpoint is not assumed private.
- Public Hugging Face model-card research is disabled by default and requires explicit upstream-repository selection.
- Agent execution verifies the installed model name and exact Ollama digest against the saved profile before starting.
- Workspace paths are resolved and checked for containment before file operations.
- Conventional credential/private-key locations are denied by default: .env files, credential stores, .ssh, .aws, .azure, .kube, .gnupg, secret directories and common private-key/certificate suffixes. .env.example, .env.sample and .env.template are allowed as templates. This is a heuristic deny-list, not a complete secret scanner.
- File reads are limited to 64 KB and writes to 1 MB per call. Writes use an atomic temporary-file replacement. Existing-file overwrites require host approval.
- Tool requests are validated by host code for registered tool name, required parameters, types, enums and unexpected properties.
- Tool outputs are capped before being returned to the model. Common credential-value patterns are heuristically redacted from text files, search snippets and command output. The same redaction is applied to saved memory notes, persisted run objectives/event payloads and final CLI/desktop responses. Placeholder values such as replace-me remain visible.
- Native tool calling, JSON-schema actions and guided ACTION/FINAL text actions use the same host tool catalogue and validation path.
- Desktop agent runs can request cancellation. The desktop passes a cancellation event into the Ollama client; the client streams the response and closes the HTTP stream when cancellation is requested. The server-side generation may not stop instantaneously.
- Optional command execution is disabled by default. When explicitly enabled, command names are resolved to configured executable paths, path-form model requests are rejected, shell=False is used, a host approval callback is required, the environment is reduced and a 90-second deadline is enforced.
- Command cancellation/timeout attempts to terminate the process tree: POSIX uses a dedicated process group; Windows uses taskkill /T /F while the direct parent remains available. This is best-effort process cleanup, not an OS sandbox.
- Local memory writes are explicit, scope-bound (user/project/agent), credential-redacted and validated. Recalled notes are marked as untrusted data in the system prompt and cannot grant permissions.
- Run execution is bounded by a configured step limit. Ordered events are journaled to SQLite during execution; crash-interrupted runs remain visible with a running status. Safe-boundary checkpoints record the exact model digest, workspace, task, harness and tool-manifest binding. Checkpoint payloads are AES-256-GCM encrypted with a separate 32-byte key file; the envelope hash binds the recovery identity. These controls are tamper-evident, not tamper-proof against an attacker who can rewrite both the database/key and application code, and the SQLite file itself is not encrypted at rest. An interrupted tool batch is marked `needs_review` and is never replayed automatically.
- Every repository-backed agent run acquires an atomic per-run lease through the runtime and keeps it alive with a heartbeat. CLI/desktop resume refuses a live lease; recovery displays it as `LEASED`. Lease loss aborts before the next provider request, checkpoint or tool operation. An expired lease is reclaimable. This prevents concurrent execution of the same run within the configured database, not unauthorized access to the database.
- Workforce plans are host-validated data: relative verification paths, DAG dependencies, step/time budgets and approval boundaries are checked before any child starts. Each child receives only its task tool registry and workspace. Workforce and child events are hash-chained and passed through credential-pattern redaction. Child agents share their assigned workspace; the read-only registry is a tool-policy boundary, not an OS sandbox.
- Structured JSONL logs contain bounded, redacted operational events and stay in the per-user application data directory. Log redaction is heuristic and is not a secret-vault guarantee.

## Known limitations — not production-certified

- The command allow-list is not a sandbox. An approved Python executable can run arbitrary code; Git and other approved binaries can also have powerful side effects. Keep command execution disabled for untrusted tasks.
- POSIX process groups and Windows taskkill improve cleanup but do not provide a complete isolation or resource-control boundary. Windows Job Objects/AppContainer or a disposable VM/container are still required.
- Filesystem containment uses application-level path resolution. Handle-based operations are still required to fully address filesystem race/symlink TOCTOU risks.
- Credential path protection and value redaction are heuristic deny-lists/pattern filters; they cannot identify every secret, encoded key, custom token format, renamed credential file or application-specific vault. Never treat them as a complete data-loss-prevention system.
- The allow_sensitive_files constructor option exists for trusted host integrations, but the desktop does not expose it. Do not enable it for model-controlled tasks.
- The stop action can close the active HTTP stream, but it cannot guarantee that the Ollama server immediately releases GPU memory or stops all work. Provider cancellation semantics must be verified for each supported Ollama release.
- The runtime does not yet implement OS sandbox enforcement, a network egress firewall, MCP OAuth/protected-resource authorization, browser/computer-use controls, a credential broker or signed certification. Durable checkpoint/resume is implemented for local runs with encrypted payloads and atomic per-run leases; the lease prevents concurrent resume through the application, but the SQLite database and key file are not an access-control boundary against a local user who can read the user's application-data directory. The optional MCP client has real SDK v2 stdio/Streamable HTTP lifecycle tests, exact tool grants, bounded calls and fail-closed manifest verification. A timed-out or transport-interrupted tool call is reported as unknown outcome and is never automatically replayed; reconnection only enables subsequent calls. A cross-platform supervisor adds a second kill-on-close Job Object on Windows or a dedicated process group on POSIX in ordinary Python runs; frozen packaged builds use the SDK cleanup unless a supervisor executable is bundled. The client still does not sandbox child servers or authenticate protected remote servers. Do not enable untrusted MCP packages or protected remote servers.
- Workforce children share one workspace and can observe sibling artifacts. A write-capable child can modify files within that workspace subject to the tool approval policy. Workflows execute sequentially; there is no parallel scheduler, per-child OS isolation, memory/CPU quota or distributed lease. The verifier is a model and can be wrong; human review remains required for consequential artifacts.
- The SQLite workforce/run event journals are hash-chained for tamper evidence but are not encrypted at rest. The checkpoint key is stored separately from the database, but both live under the user's application-data directory by default. Protect that directory and use the key-file environment override for a stronger secret-management arrangement.
- Prompt injection is not solved by system prompts. Every adapter, file path and external tool needs host-side policy.
- Remote Ollama endpoints receive prompts and local project context. Configure a remote endpoint only when its data-handling posture is acceptable.

## Production requirements before broader computer control

1. Isolate each run with an OS-enforced boundary: Windows AppContainer/Job Object restrictions or a disposable VM/container with strict ACLs.
2. Separate read, write, delete, execute, network, GUI and credential grants.
3. Add tool-specific risk classes and policy evaluation; require approval for destructive writes, installs, external transactions, Git pushes and application control.
4. Store secrets outside model context; provide short-lived, scoped credential handles only when necessary.
5. Add sanitized observations and an explicit untrusted-data boundary for files, memory and web content.
6. Record append-only audit events for run start, model digest, tool request, policy decision, approval, execution, result and verification.
7. Add in-flight cancellation, process-tree cleanup, CPU/RAM/disk/process-count quotas and cleanup after application crashes.
8. Durable checkpoints bind model/harness/policy/tool manifest and refuse interrupted tools. Workforce children inherit the host tool policy and records; add per-child OS isolation and resource quotas before broader computer control.
9. Add SBOM, dependency/license scanning, vulnerability review, signed installers and reproducible Windows packaging.
10. Threat-model MCP server provenance, remote transport authentication, server capabilities and child-agent permission inheritance. The current client uses explicit per-server tool-name grants and environment-variable references for stdio secrets. OAuth, custom HTTP authorization headers, resource/prompt subscriptions and an MCP server are not implemented. The real transport suite covers stdio/HTTP initialization, invocation, timeout, cancellation, crash detection, reconnection, schema-drift refusal and process/session cleanup, but those checks are not a substitute for server sandboxing or remote authorization.

## Reporting vulnerabilities

Do not publish exploitable vulnerabilities in public issues. Contact the maintainers through a private security advisory on the GitHub repository.
