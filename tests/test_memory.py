"""Tests for embedded graph memory — EmbeddedMemory and MemoryClient.

Uses FakeEmbedder (bag-of-words hashing) to avoid loading the real
sentence-transformers model (~80MB). Uses tmp_path for data isolation.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pytest

# Ensure panda_agent (src/) is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# Ensure graph_memory is importable (memory.py also does this at import time,
# but we need it available before importing panda_agent.memory)
_GRAPH_MEMORY_DIR = Path(r"${PANDA_HOME}/graph_memory")
if str(_GRAPH_MEMORY_DIR) not in sys.path:
    sys.path.insert(0, str(_GRAPH_MEMORY_DIR))

from panda_agent.memory import EmbeddedMemory, MemoryClient


# ---------------------------------------------------------------------------
# Fake embedder — deterministic, no external model
# ---------------------------------------------------------------------------

class FakeEmbedder:
    """Deterministic fake embedder: bag-of-words hashing into fixed dims.

    Same approach as graph-memory/tests/test_engine.py.
    - Same text → same vector (MD5 dedup still works)
    - Shared tokens → high cosine similarity (auto_link threshold testable)
    - No shared tokens → near-orthogonal (unrelated query returns empty)
    Millisecond-fast, fully offline.
    """

    def __init__(self, dim: int = 64):
        self.dim = dim

    def _tokens(self, text: str) -> list[str]:
        parts = re.findall(r"[a-zA-Z0-9]+|[一-鿿]", text)
        return [p.lower() for p in parts if p]

    def _vec(self, text: str) -> np.ndarray:
        v = np.zeros(self.dim, dtype=np.float32)
        for tok in self._tokens(text):
            h = hash(tok) % self.dim
            v[h] += 1.0
        n = np.linalg.norm(v)
        return v / n if n > 0 else v

    def encode(self, texts, normalize_embeddings=True):
        if isinstance(texts, str):
            return self._vec(texts)
        return np.array([self._vec(t) for t in texts])

    def get_sentence_embedding_dimension(self):
        return self.dim


# ---------------------------------------------------------------------------
# Path patching helper
# ---------------------------------------------------------------------------

def _patch_paths(monkeypatch, tmp_path: Path):
    """Patch graph_memory config AND engine module paths to tmp_path.

    engine.py does `from .config import GRAPH_DB, ...` at module level,
    so patching config alone is not enough — must also patch the bindings
    in the engine module itself.
    """
    from graph_memory import config as gm_config
    from graph_memory import engine as gm_engine

    db = tmp_path / "graph.db"
    gf = tmp_path / "graph.json"
    emb = tmp_path / "embeddings.npz"

    monkeypatch.setattr(gm_config, "DATA_DIR", tmp_path)
    monkeypatch.setattr(gm_config, "GRAPH_DB", db)
    monkeypatch.setattr(gm_config, "GRAPH_FILE", gf)
    monkeypatch.setattr(gm_config, "EMBEDDINGS_FILE", emb)

    monkeypatch.setattr(gm_engine, "GRAPH_DB", db)
    monkeypatch.setattr(gm_engine, "GRAPH_FILE", gf)
    monkeypatch.setattr(gm_engine, "EMBEDDINGS_FILE", emb)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mem(tmp_path, monkeypatch):
    """Fresh EmbeddedMemory with fake embedder, data isolated to tmp_path."""
    _patch_paths(monkeypatch, tmp_path)

    old_instance = EmbeddedMemory._instance
    EmbeddedMemory._instance = None

    m = EmbeddedMemory(data_dir=tmp_path)
    m._engine._embedder = FakeEmbedder(dim=64)

    try:
        yield m
    finally:
        try:
            m._engine._db.close()
        except Exception:
            pass
        EmbeddedMemory._instance = old_instance


@pytest.fixture
def client(mem):
    """MemoryClient backed by the isolated EmbeddedMemory singleton."""
    EmbeddedMemory._instance = mem
    return MemoryClient()


# ---------------------------------------------------------------------------
# 1. test_memory_client_init
# ---------------------------------------------------------------------------

class TestMemoryClientInit:
    def test_memory_client_init(self, client):
        """MemoryClient() can initialize, is_available() returns bool."""
        assert client is not None
        result = client.is_available()
        assert isinstance(result, bool)
        assert result is True  # should be available with our fixture


# ---------------------------------------------------------------------------
# 2. test_write_and_retrieve
# ---------------------------------------------------------------------------

class TestWriteAndRetrieve:
    def test_write_and_retrieve(self, mem):
        """Write knowledge, then retrieve related query returns non-empty."""
        mem.write("Python is a programming language for data science",
                   title="python")
        results = mem.retrieve("Python programming", top_k=5)
        assert isinstance(results, list)
        assert len(results) > 0
        assert "Python" in results[0]["content"]


# ---------------------------------------------------------------------------
# 3. test_retrieve_empty
# ---------------------------------------------------------------------------

class TestRetrieveEmpty:
    def test_retrieve_empty(self, mem):
        """Retrieve with an unrelated query returns empty list."""
        mem.write("Python is a programming language for data science")
        results = mem.retrieve("quantum entanglement xyzzy unrelated",
                                top_k=5)
        assert isinstance(results, list)
        assert len(results) == 0


# ---------------------------------------------------------------------------
# 4. test_retrieve_context_format
# ---------------------------------------------------------------------------

class TestRetrieveContextFormat:
    def test_retrieve_context_format(self, mem):
        """retrieve_context returns properly formatted string."""
        mem.write("Test knowledge for context formatting check",
                   title="format-test")
        ctx = mem.retrieve_context("Test knowledge", top_k=3)
        assert isinstance(ctx, str)
        assert len(ctx) > 0
        assert "## Past Experience" in ctx
        # Should contain score notation and node type
        assert "(" in ctx
        assert ")" in ctx


# ---------------------------------------------------------------------------
# 5. test_stats
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats(self, mem):
        """stats() returns dict containing node_count."""
        mem.write("Stats test node content")
        stats = mem.stats()
        assert isinstance(stats, dict)
        assert "node_count" in stats
        assert isinstance(stats["node_count"], int)
        assert stats["node_count"] >= 1


# ---------------------------------------------------------------------------
# 6. test_write_with_metadata
# ---------------------------------------------------------------------------

class TestWriteWithMetadata:
    def test_write_with_metadata(self, mem):
        """Write with node_type='reference', source='panda_test'."""
        node = mem.write(
            "Reference documentation for testing metadata",
            title="ref-doc",
            node_type="reference",
            source="panda_test",
        )
        assert isinstance(node, dict)
        assert "id" in node
        assert node["node_type"] == "reference"
        assert node["source"] == "panda_test"


# ---------------------------------------------------------------------------
# 7. test_multiple_writes_and_linking
# ---------------------------------------------------------------------------

class TestMultipleWritesAndLinking:
    def test_multiple_writes_and_linking(self, mem):
        """Two related writes should auto_link to create an edge."""
        mem.write("vLLM 用 PagedAttention 分页管理 KV cache 提升并发")
        mem.write("vLLM 的 PagedAttention 把 KV cache 分页 管理显存碎片")

        stats = mem.stats()
        assert stats["node_count"] == 2
        # auto_link in write() should have created at least one edge
        # between the two semantically related nodes
        assert stats["edge_count"] >= 1


# ---------------------------------------------------------------------------
# 8. test_error_handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_error_handling(self, mem):
        """Passing empty strings does not crash."""
        # write with empty content
        result = mem.write("")
        assert isinstance(result, dict)

        # retrieve with empty query
        results = mem.retrieve("")
        assert isinstance(results, list)

        # retrieve_context with empty query
        ctx = mem.retrieve_context("")
        assert isinstance(ctx, str)


# ---------------------------------------------------------------------------
# 9. test_write_chinese_content
# ---------------------------------------------------------------------------

class TestWriteChineseContent:
    def test_write_chinese_content(self, mem):
        """Chinese content can be written and retrieved with Chinese query."""
        mem.write("大语言模型推理优化是AI基础设施的关键技术",
                   title="LLM推理优化")
        results = mem.retrieve("大语言模型推理", top_k=5)
        assert len(results) > 0
        assert "大语言模型" in results[0]["content"]


# ---------------------------------------------------------------------------
# 10. test_retrieve_context_empty_when_no_data
# ---------------------------------------------------------------------------

class TestRetrieveContextEmptyWhenNoData:
    def test_retrieve_context_empty_when_no_data(self, client):
        """retrieve_context returns empty string when no data exists."""
        ctx = client.retrieve_context("anything at all", top_k=3)
        assert isinstance(ctx, str)
        assert ctx == ""
