from ollama_workstation.agents.runtime import AgentRuntime


def test_decode_final_structured_action() -> None:
    calls, final = AgentRuntime._decode_structured_action('{"action":"final","message":"done"}')
    assert calls == []
    assert final == "done"

def test_decode_tool_structured_action() -> None:
    calls, final = AgentRuntime._decode_structured_action(
        '{"action":"tool","name":"list_files","arguments":{"path":"."}}')
    assert final is None
    assert calls[0]["function"]["name"] == "list_files"

def test_guided_action_parser_accepts_only_registered_tools() -> None:
    calls, final = AgentRuntime._decode_guided_action(
        'ACTION: {"name":"read_file","arguments":{"path":"README.md"}}', ["read_file", "list_files"])
    assert final is None
    assert calls[0]["function"]["name"] == "read_file"
    rejected, _ = AgentRuntime._decode_guided_action(
        'ACTION: {"name":"delete_everything","arguments":{}}', ["read_file"])
    assert rejected == []

def test_guided_action_parser_recognizes_final_marker() -> None:
    calls, final = AgentRuntime._decode_guided_action("FINAL: complete", ["read_file"])
    assert calls == []
    assert final == "complete"
