from ollama_workstation.agents.memory import MemoryStore
from ollama_workstation.storage.repository import Repository


def test_recall_searches_older_matching_notes_before_candidate_limit(tmp_path) -> None:
    store = MemoryStore(Repository(tmp_path / "memory.sqlite3"))
    scope_id = str(tmp_path / "large-project")
    target = store.remember(
        "The legacy Vulkan allocator requires explicit lifetime tracking.",
        scope="project", scope_id=scope_id, category="decision",
        tags=["allocator", "legacy"],
    )
    for index in range(520):
        store.remember(
            f"Recent unrelated project note number {index} about documentation.",
            scope="project", scope_id=scope_id, category="fact",
        )

    matches = store.recall(
        "Vulkan allocator lifetime", [("project", scope_id)], limit=6
    )
    assert target.memory_id in {item.memory_id for item in matches}
