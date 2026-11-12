"""Child process for the recovery acceptance scenario.

Runs one agent task in a separate OS process so the parent can terminate it at
a controlled point, exactly as a crash or power loss would. Not a public entry
point: invoked as ``python -m ollama_workstation.agentification._recovery_child SPEC``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from ollama_workstation.agentification.agents import AgentService
from ollama_workstation.agentification.store import AgentStore
from ollama_workstation.ollama.client import OllamaClient
from ollama_workstation.storage.repository import Repository


def main(argv: list[str]) -> int:
    spec = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    database = Path(spec["database"])
    client = OllamaClient(spec["base_url"], float(spec.get("timeout", 300.0)))
    repository = Repository(database)
    service = AgentService(client, repository, AgentStore(database),
                           keep_alive=str(spec.get("keep_alive", "5m")))
    agent = service.get(spec["agent_id"])
    profile = repository.get_profile(agent.model_name, agent.model_digest)
    if profile is None:
        return 3
    outcome = service.run(
        agent.agent_id, spec["task"], Path(spec["workspace"]), profile,
        approval=lambda _description: True,
    )
    return 0 if outcome.result.status == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
