# Security policy and threat model

## Security objective

An agent may propose actions, but only the host application can authorize and execute them. Model output, model-card content, retrieved documents, browser pages and tool results are untrusted data.

## Threat model

Assets include user source files, local credentials, model files, Git repositories, local services, workspaces and application state. Threat actors include malicious prompt content, hostile websites/documents, compromised model packages, confused or unreliable models, malicious MCP servers and accidental destructive tool calls.

## 0.1.0 controls

- Ollama endpoint defaults to loopback (`127.0.0.1`). Users may configure another endpoint; remote endpoints are not assumed private.
- Remote model-card research is disabled by default.
- Workspace paths are resolved and checked for containment before file operations.
- File reads are limited to 64 KB and writes to 1 MB per tool call.
- Existing-file overwrite requires host approval.
- Shell tool is omitted from tool definitions unless explicitly enabled in settings.
- Shell execution uses argument arrays and `shell=False`; executable must be allow-listed; a host approval callback is required; timeout is 90 seconds.
- Agent loop has a bounded maximum step count (1–40, default 12).
- Tool names and argument objects are checked by host code before invocation.
- Tool output is truncated for command output.
- SQLite state is kept in the user's local app-data directory.

## Known limitations in 0.1.0

- The initial shell allow-list does not provide OS-level sandboxing. An allow-listed executable such as Python can run arbitrary code when the user explicitly approves it. Keep shell disabled for untrusted tasks.
- File path containment has application-level checks; production hardening should use platform-specific handle-based operations to mitigate filesystem race/symlink TOCTOU cases.
- No MCP server manager, browser automation, desktop control, secret broker or network egress firewall is exposed yet.
- PySide UI uses a deny-by-default approval callback; advanced interactive tool approvals need a dedicated main-thread policy service before enabling shell in the desktop UI.
- A remote Ollama endpoint can receive prompts and local project context. Treat it as a data processor and configure accordingly.
- Prompt injection is not solved by system prompts. Every tool and adapter must enforce host-side policy.

## Production requirements before broader computer control

1. Use a per-run temporary workspace/container or Windows AppContainer/Job Object strategy with restricted ACLs.
2. Separate read, write, delete, execute, network and GUI permissions.
3. Use typed tool schemas with argument validation and per-tool risk classification.
4. Add human approval for destructive writes, external network actions, installs, Git pushes, credentials and application control.
5. Store secrets outside model context; provide short-lived, scoped credential handles when necessary.
6. Add a sanitized tool-result channel and defend against tool-output prompt injection.
7. Record immutable audit events with user, agent, model digest, tool, approval, arguments digest, result and timestamp.
8. Add cancellation, process-tree termination, CPU/memory/time limits and cleanup on crash.
9. Add SBOM, dependency scanning, code signing and reproducible Windows packaging.
10. Threat-model MCP server provenance, remote transport authentication and server capability grants.

## Reporting vulnerabilities

Do not publish exploitable vulnerabilities in public issues. Contact the maintainers through a private security advisory on the GitHub repository.
