"""Embedded graph memory — direct GraphEngine, no HTTP server needed.

Wraps graph_memory.engine.GraphEngine for in-process use by PandaAgent.
Persists to SQLite + .npz under ~/.panda/memory/.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import Any

# Add graph_memory to import path
_GRAPH_MEMORY_DIR = Path(r"${PANDA_HOME}/graph_memory")
if str(_GRAPH_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_GRAPH_MEMORY_DIR))


class EmbeddedMemory:
    """Embedded graph memory engine — no external service required.

    Uses GraphEngine directly (NetworkX + sentence-transformers + PageRank).
    Data persists to ~/.panda/memory/ as SQLite + .npz files.
    """

    _instance: "EmbeddedMemory | None" = None

    def __init__(self, data_dir: Path | None = None):
        if data_dir is None:
            data_dir = Path(os.path.expanduser("~/.panda/memory"))
        data_dir.mkdir(parents=True, exist_ok=True)

        # Monkey-patch graph_memory config to use our data dir
        # before importing GraphEngine
        import graph_memory.config as gm_config
        gm_config.DATA_DIR = data_dir
        gm_config.GRAPH_DB = data_dir / "graph.db"
        gm_config.GRAPH_FILE = data_dir / "graph.json"
        gm_config.EMBEDDINGS_FILE = data_dir / "embeddings.npz"

        from graph_memory.engine import GraphEngine
        self._engine = GraphEngine()

    @classmethod
    def get(cls) -> "EmbeddedMemory | None":
        """Get or create singleton instance. Returns None if init fails."""
        if cls._instance is None:
            try:
                cls._instance = cls()
            except Exception as e:
                print(f"[Memory] Init failed: {e}", file=sys.stderr)
                return None
        return cls._instance

    def write(self, content: str, title: str = "",
              node_type: str = "knowledge", source: str = "panda") -> dict:
        """Write a knowledge node + auto-link to related nodes."""
        try:
            node = self._engine.add_node(
                content=content, title=title,
                node_type=node_type, source=source,
            )
            if node:
                self._engine.auto_link(node["id"], max_links=5)
                self._engine.save()
            return node or {}
        except Exception as e:
            print(f"[Memory] Write failed: {e}", file=sys.stderr)
            return {}

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Retrieve related knowledge via embedding + PageRank."""
        try:
            return self._engine.retrieve(query, top_k=top_k)
        except Exception as e:
            print(f"[Memory] Retrieve failed: {e}", file=sys.stderr)
            return []

    def retrieve_context(self, query: str, top_k: int = 3) -> str:
        """Retrieve and format as context string for LLM injection.

        This is what gets appended to the system prompt so the agent
        can benefit from past experience without re-learning.
        """
        results = self.retrieve(query, top_k=top_k)
        if not results:
            return ""

        lines = ["## Past Experience (from memory)"]
        for r in results:
            score = r.get("score", 0)
            content = r.get("content", "")[:300]
            node_type = r.get("node_type", "")
            lines.append(f"- [{score:.2f}] ({node_type}) {content}")
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        try:
            return self._engine.stats()
        except Exception:
            return {"error": "stats failed"}

    def is_available(self) -> bool:
        return self._engine is not None


# Backward-compatible API matching old MemoryClient interface
class MemoryClient:
    """Drop-in replacement for the old HTTP-based MemoryClient.

    Uses EmbeddedMemory internally — no external service needed.
    """

    def __init__(self, url: str = "", **kwargs):
        self._mem = EmbeddedMemory.get()

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if self._mem is None:
            return []
        return self._mem.retrieve(query, top_k=top_k)

    def write(self, content: str, title: str = "",
              node_type: str = "knowledge", source: str = "panda") -> dict:
        if self._mem is None:
            return {"error": "memory not available"}
        return self._mem.write(content, title=title, node_type=node_type, source=source)

    def retrieve_context(self, query: str, top_k: int = 3) -> str:
        if self._mem is None:
            return ""
        return self._mem.retrieve_context(query, top_k=top_k)

    def is_available(self) -> bool:
        return self._mem is not None and self._mem.is_available()

    def stats(self) -> dict[str, Any]:
        if self._mem is None:
            return {"error": "memory not available"}
        return self._mem.stats()

    def list_all(self) -> list[dict[str, Any]]:
        """List all memory nodes with full content — for /memory tidy review."""
        if self._mem is None:
            return []
        engine = self._mem._engine
        nodes = []
        for nid, attrs in engine.graph.nodes(data=True):
            nodes.append({
                "id": nid,
                "content": attrs.get("content", ""),
                "title": attrs.get("title", ""),
                "source": attrs.get("source", ""),
                "node_type": attrs.get("node_type", ""),
            })
        return nodes

    def delete_by_id(self, nid: str) -> bool:
        """Delete a memory node by id — for /memory tidy cleanup."""
        if self._mem is None:
            return False
        result = self._mem._engine.delete_node(nid)
        if result:
            self._mem._engine.save()
        return result
