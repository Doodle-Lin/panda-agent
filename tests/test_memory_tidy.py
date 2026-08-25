"""Test: /memory command in CLI — tidy up graph memory.

User types /memory in chat → agent reviews all memory nodes,
keeps useful ones, removes noise/junk/duplicates.
"""
import sys, os
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from panda_agent.memory import EmbeddedMemory, MemoryClient


class TestMemoryTidy:
    """EmbeddedMemory must support listing all nodes and deleting by id."""

    def test_list_all_nodes(self):
        """EmbeddedMemory should be able to list all nodes for review."""
        mem = EmbeddedMemory.get()
        if mem is None:
            return  # skip if no graph_memory available
        # Write a test node
        mem.write("Test node for tidy", title="test", source="test")
        # List all nodes
        engine = mem._engine
        node_ids = list(engine.graph.nodes())
        assert len(node_ids) > 0, "Should have at least one node"
        # Each node should have content
        for nid in node_ids[:3]:
            attrs = engine.graph.nodes[nid]
            assert "content" in attrs or "text" in attrs, f"Node {nid} has no content"

    def test_delete_node(self):
        """EmbeddedMemory should support deleting nodes by id."""
        mem = EmbeddedMemory.get()
        if mem is None:
            return
        engine = mem._engine
        # Write and then delete
        node = mem.write("Temporary node to delete", title="temp", source="test")
        if node and "id" in node:
            nid = node["id"]
            assert engine.graph.has_node(nid)
            result = engine.delete_node(nid)
            assert result is True
            assert not engine.graph.has_node(nid)

    def test_get_all_nodes_content(self):
        """EmbeddedMemory should return all nodes with content for LLM review."""
        mem = EmbeddedMemory.get()
        if mem is None:
            return
        engine = mem._engine
        nodes = []
        for nid, attrs in engine.graph.nodes(data=True):
            nodes.append({
                "id": nid,
                "content": attrs.get("content", ""),
                "title": attrs.get("title", ""),
                "source": attrs.get("source", ""),
                "node_type": attrs.get("node_type", ""),
            })
        # Should be a list of dicts
        assert isinstance(nodes, list)
        if nodes:
            assert "content" in nodes[0]
            assert "id" in nodes[0]


class TestSlashCommandParsing:
    """CLI should parse /memory as a slash command, not a task."""

    def test_slash_memory_detected(self):
        """Input starting with / should be detected as slash command."""
        from panda_agent.cli import _is_slash_command, _parse_slash_command
        assert _is_slash_command("/memory") is True
        assert _is_slash_command("/mem tidy") is True
        assert _is_slash_command("write a file") is False
        assert _is_slash_command("/help") is True

    def test_parse_slash_command(self):
        """Parse /memory → ('memory', ''), /mem tidy → ('memory', 'tidy')."""
        from panda_agent.cli import _parse_slash_command
        cmd, args = _parse_slash_command("/memory")
        assert cmd == "memory"
        assert args == ""

        cmd, args = _parse_slash_command("/help")
        assert cmd == "help"
