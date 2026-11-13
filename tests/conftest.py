"""Shared fixtures for end-to-end tests that run over a real HTTP socket."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from support.scripted_ollama import ScriptedOllama

from ollama_workstation.agentification.agents import AgentService
from ollama_workstation.agentification.store import AgentStore
from ollama_workstation.models.schemas import ModelProfile
from ollama_workstation.ollama.client import OllamaClient
from ollama_workstation.profiling.profiler import ModelProfiler
from ollama_workstation.storage.repository import Repository


@pytest.fixture
def scripted() -> Iterator[ScriptedOllama]:
    with ScriptedOllama() as server:
        yield server


@pytest.fixture
def client(scripted: ScriptedOllama) -> OllamaClient:
    return OllamaClient(scripted.base_url, 30.0)


@pytest.fixture
def repository(tmp_path: Path) -> Repository:
    return Repository(tmp_path / "state" / "workstation.sqlite3")


@pytest.fixture
def store(repository: Repository) -> AgentStore:
    return AgentStore(repository.database)


@pytest.fixture
def service(client: OllamaClient, repository: Repository, store: AgentStore) -> AgentService:
    service = AgentService(client, repository, store)
    service.skills.install_builtins()
    return service


def make_profile(client: OllamaClient, repository: Repository, model: str) -> ModelProfile:
    profile = ModelProfiler(client).profile(model, run_probes=False)
    repository.save_profile(profile)
    return profile
