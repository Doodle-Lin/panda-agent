"""Tests for the /memory tidy command's public storage operations."""

from __future__ import annotations

from panda_agent.memory import MemoryClient


def test_list_all_nodes(tmp_path):
    memory = MemoryClient(storage_path=tmp_path / "memory.sqlite3")
    memory.write("Test node for tidy", title="test", source="test")

    nodes = memory.list_all()

    assert len(nodes) == 1
    assert nodes[0]["content"] == "Test node for tidy"
    assert nodes[0]["source"] == "test"


def test_update_and_delete_node(tmp_path):
    memory = MemoryClient(storage_path=tmp_path / "memory.sqlite3")
    node = memory.write("Temporary node", title="temp", source="test")

    assert memory.update_by_id(node["id"], content="Refined node") is True
    assert memory.list_all()[0]["content"] == "Refined node"
    assert memory.delete_by_id(node["id"]) is True
    assert memory.list_all() == []


def test_slash_memory_detected():
    from panda_agent.cli import _is_slash_command

    assert _is_slash_command("/memory") is True
    assert _is_slash_command("/mem tidy") is True
    assert _is_slash_command("write a file") is False
    assert _is_slash_command("/help") is True


def test_parse_slash_command():
    from panda_agent.cli import _parse_slash_command

    assert _parse_slash_command("/memory") == ("memory", "")
    assert _parse_slash_command("/help") == ("help", "")
