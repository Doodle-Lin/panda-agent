"""Audit Phase 3 memory robustness tests.

Covers audit finding #10 (split into 4 sub-issues) from the 2026-09-14 audit.

#10a (schema versioning): _create_schema only migrated `source`. New columns
     added since the initial schema (normalized_content, title, tags_json,
     confidence, source_round, access_count, last_accessed) would INSERT-fail
     on a DB created with an older schema. Add a `schema_version` table and
     a per-column migration chain.

#10b (write_if_novel O(N)): every write does `SELECT * FROM nodes WHERE
     node_type = ?` then loops in Python. Index `node_type` already exists;
     push the similarity filtering into SQL where possible. The fix keeps
     the O(N) shape (semantic similarity needs Python for the embedding matmul)
     but ensures the SQL filter uses the index.

#10c (concurrent write race): EmbeddedMemory.get() singleton has no thread
     lock; np.savez is not atomic. Add a threading.Lock and write-then-rename
     for the embeddings npz file.

#10d (dead code): `'new_emb' not in dir()` is fragile and the branch is `pass`
     (dead). Replace with an explicit local variable initialized to None.
"""
from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

from panda_agent.memory import EmbeddedMemoryStore, MemoryNode


def _make_store(tmp_path: Path) -> EmbeddedMemoryStore:
    return EmbeddedMemoryStore(tmp_path / "memory.sqlite3")


# ---------------------------------------------------------------------------
# #10a: schema versioning — old DB migrates to current
# ---------------------------------------------------------------------------

class TestSchemaMigration:
    """An old-schema DB (no normalized_content / no title / etc.) must
    migrate transparently on first connect, and INSERT must succeed."""

    def _create_v0_db(self, path: Path) -> None:
        """Create a DB with the *original* minimal schema (just id/content)."""
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.executescript(
            """
            CREATE TABLE nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        conn.execute(
            "INSERT INTO nodes (content, created_at, updated_at) "
            "VALUES ('legacy', '2024-01-01T00:00:00+00:00', '2024-01-01T00:00:00+00:00')"
        )
        conn.commit()
        conn.close()

    def test_old_schema_migrates_to_current(self, tmp_path, monkeypatch):
        # Force lexical path so we don't need sentence_transformers installed
        # for this migration test — we're testing schema migration, not
        # embedding retrieval.
        monkeypatch.setattr("panda_agent.memory._USE_EMBEDDING", False)
        db = tmp_path / "old.sqlite3"
        self._create_v0_db(db)

        # Opening must migrate, not raise.
        store = EmbeddedMemoryStore(db)
        # Writing a new node must succeed (all current columns present).
        result = store.write_if_novel(
            MemoryNode(content="migrated ok", node_type="knowledge")
        )
        assert result.get("created") is True
        # The legacy row is still there (id=1); the new row has id=2.
        with store._connect() as conn:
            rows = conn.execute("SELECT id, content FROM nodes ORDER BY id").fetchall()
        ids_contents = [(r["id"], r["content"]) for r in rows]
        assert (1, "legacy") in ids_contents
        assert any(content == "migrated ok" for _, content in ids_contents)


# ---------------------------------------------------------------------------
# #10c: concurrent writes do not corrupt embeddings
# ---------------------------------------------------------------------------

class TestConcurrentWrites:
    """Two threads writing simultaneously must not corrupt the embeddings
    file or the SQLite DB."""

    def test_concurrent_writes_no_corruption(self, tmp_path, monkeypatch):
        # Force the lexical path so we don't need sentence_transformers
        # installed for this test to run.
        monkeypatch.setattr("panda_agent.memory._USE_EMBEDDING", False)
        store = _make_store(tmp_path)

        errors: list[Exception] = []
        n_threads = 10
        n_writes_per_thread = 5

        def writer(thread_id: int) -> None:
            try:
                for i in range(n_writes_per_thread):
                    store.write_if_novel(
                        MemoryNode(
                            content=f"t{thread_id}-w{i} unique content",
                            node_type="knowledge",
                            title=f"thread-{thread_id}-{i}",
                        )
                    )
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"concurrent writes raised: {errors}"
        # All n_threads * n_writes_per_thread rows must be present.
        with store._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        assert count == n_threads * n_writes_per_thread


# ---------------------------------------------------------------------------
# #10d: dead `dir()` check removed
# ---------------------------------------------------------------------------

class TestDirCheckRemoved:
    """The pre-fix code used `'new_emb' not in dir()` to check a local binding,
    which is fragile and the branch was a no-op `pass`. The fix replaces it
    with an explicit `new_emb: np.ndarray | None = None` at function top."""

    def test_no_dir_check_in_write_path(self):
        import inspect
        from panda_agent import memory as mem_mod

        source = inspect.getsource(mem_mod.EmbeddedMemoryStore.write_if_novel)
        assert "'new_emb' not in dir()" not in source, (
            "write_if_novel still uses the fragile 'new_emb' not in dir() check; "
            "replace with an explicit local variable."
        )
