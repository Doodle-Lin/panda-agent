"""Persistent graph memory with embedded SQLite + semantic embedding retrieval.

Default backend: embedded SQLite for nodes/edges + numpy npz for embeddings.
Uses sentence_transformers (BAAI/bge-base-zh-v1.5) for semantic similarity
when available, with a lexical cosine fallback when the model is not installed.

The embedding approach is borrowed from the graph-memory project
(https://github.com/Doodle-Lin/graph-memory) which tuned the dedup
threshold (0.85), auto-link threshold (0.3), and matrix-cache pattern.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests


_EMBEDDED_SCHEMES = ("embedded://", "sqlite://")
_TOKEN_RE = re.compile(r"[a-z0-9_]+|[\u3400-\u9fff]", re.IGNORECASE)

# Tuned values from graph-memory benchmark
_DEDUP_THRESHOLD = 0.85      # embedding similarity above this = duplicate
_AUTO_LINK_THRESHOLD = 0.30  # embedding similarity above this = auto-link edge
_MIN_SIM_THRESHOLD = 0.15    # below this, don't return in retrieve results

# Model for semantic embedding (CJK + Latin, 768-dim, local download)
_EMBEDDING_MODEL = os.environ.get(
    "PANDA_EMBEDDING_MODEL", "BAAI/bge-base-zh-v1.5"
)

# Detect availability of numpy and sentence_transformers at import time.
# If not installed, fall back to lexical cosine — the interface stays the same.
try:
    import numpy as np
    _HAS_NUMPY = True
except ImportError:
    _HAS_NUMPY = False

# sentence_transformers is lazy-loaded (import triggers model download),
# but we detect whether the package is installed.
try:
    import sentence_transformers  # noqa: F401
    _HAS_ST = True
except ImportError:
    _HAS_ST = False

_USE_EMBEDDING = _HAS_NUMPY and _HAS_ST


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_storage_path() -> Path:
    panda_home = Path(os.getenv("PANDA_HOME", str(Path.home() / ".panda")))
    return panda_home / "memory" / "memory.sqlite3"


# ---------------------------------------------------------------------------
# Lexical fallback (used when sentence_transformers is not installed)
# ---------------------------------------------------------------------------

def _tokens(text: str) -> Counter[str]:
    return Counter(_TOKEN_RE.findall(text.casefold()))


def _lexical_similarity(left: str, right: str) -> float:
    """Lexical cosine similarity (fallback when embeddings unavailable)."""
    left_counts = _tokens(left)
    right_counts = _tokens(right)
    if not left_counts or not right_counts:
        return 0.0
    dot = sum(left_counts[token] * right_counts.get(token, 0) for token in left_counts)
    left_norm = math.sqrt(sum(value * value for value in left_counts.values()))
    right_norm = math.sqrt(sum(value * value for value in right_counts.values()))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


# The public similarity function dispatches to embedding or lexical.
def _similarity(left: str, right: str) -> float:
    """Return similarity score. Dispatches to embedding or lexical."""
    # This is only called in fallback mode; embedding mode uses matrix math.
    return _lexical_similarity(left, right)


@dataclass(frozen=True)
class MemoryNode:
    """A structured, persistent unit of knowledge."""

    content: str
    title: str = ""
    node_type: str = "knowledge"
    source: str = "panda"
    tags: tuple[str, ...] = field(default_factory=tuple)
    confidence: float = 1.0
    source_round: int | None = None


class EmbeddedMemoryStore:
    """Local graph store backed by SQLite + optional embedding retrieval.

    When sentence_transformers is installed:
    - Write: 3-layer dedup (MD5 → embedding > 0.85 → new) + auto-link > 0.3
    - Retrieve: embedding matrix multiply + one-hop graph propagation
    - Embeddings cached in npz file alongside the SQLite DB

    When sentence_transformers is NOT installed:
    - Falls back to lexical cosine similarity (the original implementation)
    - All public methods behave identically, just with lower quality
    """

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()
        # Embedding state (only used when _USE_EMBEDDING is True)
        self._embedder = None
        self._embeddings: dict[str, np.ndarray] | None = None  # node_id -> vec
        self._emb_matrix: np.ndarray | None = None
        self._emb_ids: list[str] | None = None
        self._emb_dirty = True
        self._embeddings_file = self.path.parent / "embeddings.npz"

    # -- Embedding infrastructure (lazy-loaded) -------------------------

    @property
    def embedder(self):
        """Lazy-load the sentence transformer model."""
        if self._embedder is None:
            from sentence_transformers import SentenceTransformer
            self._embedder = SentenceTransformer(_EMBEDDING_MODEL)
        return self._embedder

    def _embed(self, text: str) -> np.ndarray:
        return self.embedder.encode(text, normalize_embeddings=True)

    def _embed_batch(self, texts: list[str]) -> np.ndarray:
        return self.embedder.encode(texts, normalize_embeddings=True)

    def _get_emb_matrix(self) -> tuple[np.ndarray, list[str]]:
        """Return (matrix, ids) with caching."""
        if self._emb_dirty or self._emb_matrix is None or self._emb_ids is None:
            self._emb_ids = list(self._embeddings.keys()) if self._embeddings else []
            if self._emb_ids:
                self._emb_matrix = np.array([self._embeddings[k] for k in self._emb_ids])
            else:
                self._emb_matrix = np.empty((0, self.embedder.get_sentence_embedding_dimension()))
            self._emb_dirty = False
        return self._emb_matrix, self._emb_ids

    def _invalidate_emb_cache(self):
        self._emb_dirty = True

    def _load_embeddings(self):
        """Load embedding cache from npz file, rebuild if needed."""
        if not _USE_EMBEDDING or self._embeddings is not None:
            return

        self._embeddings = {}
        if self._embeddings_file.exists():
            try:
                arch = np.load(self._embeddings_file, allow_pickle=True)
                ids = arch["ids"].tolist()
                vecs = arch["vectors"]
                expected_dim = self.embedder.get_sentence_embedding_dimension()
                if vecs.shape[1] != expected_dim:
                    print(f"[Memory] Embedding dim mismatch (old={vecs.shape[1]}, "
                          f"new={expected_dim}), rebuilding...", file=sys.stderr)
                    self._embeddings = {}
                else:
                    self._embeddings = {i: v for i, v in zip(ids, vecs)}
            except Exception as e:
                print(f"[Memory] Embedding load failed: {e}, will rebuild on write",
                      file=sys.stderr)
                self._embeddings = {}

        # If we have nodes but no embeddings, rebuild
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        if count > 0 and not self._embeddings:
            self._rebuild_embeddings()

    def _rebuild_embeddings(self):
        """Rebuild embeddings for all existing nodes (batch)."""
        if not _USE_EMBEDDING:
            return
        with self._connect() as conn:
            rows = conn.execute("SELECT id, content, title FROM nodes ORDER BY id").fetchall()
        if not rows:
            return
        contents = []
        node_ids = []
        for row in rows:
            text = f"{row['title']} {row['content']}" if row["title"] else row["content"]
            contents.append(text)
            node_ids.append(str(row["id"]))
        vecs = self._embed_batch(contents)
        self._embeddings = {nid: vec for nid, vec in zip(node_ids, vecs)}
        self._invalidate_emb_cache()
        self._save_embeddings()

    def _save_embeddings(self):
        """Persist embeddings to npz file."""
        if not _USE_EMBEDDING or not self._embeddings:
            return
        try:
            ids = list(self._embeddings.keys())
            vecs = np.array([self._embeddings[i] for i in ids])
            np.savez(self._embeddings_file, ids=np.array(ids), vectors=vecs)
        except Exception as e:
            print(f"[Memory] Embedding save failed: {e}", file=sys.stderr)

    # -- Connection management (unchanged from original) ---------------

    def _connect(self):
        """Open a connection as a context manager that commits and closes."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        self._create_schema(connection)

        import contextlib

        @contextlib.contextmanager
        def _cm():
            try:
                with connection:
                    yield connection
            finally:
                connection.close()

        return _cm()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                content TEXT NOT NULL,
                normalized_content TEXT NOT NULL,
                title TEXT NOT NULL DEFAULT '',
                node_type TEXT NOT NULL DEFAULT 'knowledge',
                source TEXT NOT NULL DEFAULT 'panda',
                tags_json TEXT NOT NULL DEFAULT '[]',
                confidence REAL NOT NULL DEFAULT 1.0,
                source_round INTEGER,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                access_count INTEGER NOT NULL DEFAULT 0,
                last_accessed TEXT,
                UNIQUE(normalized_content, node_type)
            );
            CREATE TABLE IF NOT EXISTS edges (
                source_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                target_id INTEGER NOT NULL REFERENCES nodes(id) ON DELETE CASCADE,
                weight REAL NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (source_id, target_id),
                CHECK (source_id < target_id)
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(node_type);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
            """
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(nodes)").fetchall()
        }
        if "source" not in columns:
            connection.execute(
                "ALTER TABLE nodes ADD COLUMN source TEXT NOT NULL DEFAULT 'panda'"
            )

    @staticmethod
    def _row_to_result(row: sqlite3.Row, score: float = 0.0) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "content": row["content"],
            "title": row["title"],
            "node_type": row["node_type"],
            "source": row["source"],
            "tags": json.loads(row["tags_json"]),
            "confidence": row["confidence"],
            "source_round": row["source_round"],
            "score": round(score, 4),
        }

    @staticmethod
    def _search_text(row: sqlite3.Row) -> str:
        return "\n".join(
            (row["title"], row["content"], " ".join(json.loads(row["tags_json"])))
        )

    # -- Public API: write ----------------------------------------------

    def write_if_novel(self, node: MemoryNode, threshold: float = 0.85) -> dict[str, Any]:
        """Persist a node or reinforce a highly similar node.

        Three-layer dedup (when embeddings available):
        1. MD5 / normalized content exact match → reinforce
        2. Embedding similarity > threshold → reinforce
        3. No match → create new + auto-link

        Falls back to lexical similarity when embeddings unavailable.
        """
        content = node.content.strip()
        if not content:
            raise ValueError("memory content must not be empty")
        if not 0.0 <= node.confidence <= 1.0:
            raise ValueError("memory confidence must be between 0 and 1")

        normalized = " ".join(content.casefold().split())
        now = _utc_now()

        # Ensure embeddings are loaded
        if _USE_EMBEDDING and self._embeddings is None:
            self._load_embeddings()

        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM nodes WHERE node_type = ?", (node.node_type,)
            ).fetchall()

            # Layer 1: exact match (by normalized content)
            best_row: sqlite3.Row | None = None
            best_score = 0.0
            for row in existing:
                if row["normalized_content"] == normalized:
                    best_row, best_score = row, 1.0
                    break

            # Layer 2: semantic similarity
            if best_row is None and _USE_EMBEDDING and self._embeddings:
                new_emb = self._embed(content)
                existing_ids = [str(row["id"]) for row in existing]
                existing_embs = []
                existing_rows = []
                for row, nid in zip(existing, existing_ids):
                    if nid in self._embeddings:
                        existing_embs.append(self._embeddings[nid])
                        existing_rows.append(row)
                if existing_embs:
                    mat = np.array(existing_embs)
                    sims = mat @ new_emb
                    best_idx = int(np.argmax(sims))
                    best_sim = float(sims[best_idx])
                    if best_sim >= threshold:
                        best_row = existing_rows[best_idx]
                        best_score = best_sim

            # Layer 2 fallback: lexical similarity
            if best_row is None and not _USE_EMBEDDING:
                for row in existing:
                    score = _lexical_similarity(content, row["content"])
                    if score > best_score:
                        best_row, best_score = row, score

            # Found a match → reinforce
            if best_row is not None and best_score >= threshold:
                connection.execute(
                    """
                    UPDATE nodes
                    SET confidence = MAX(confidence, ?), updated_at = ?,
                        access_count = access_count + 1, last_accessed = ?
                    WHERE id = ?
                    """,
                    (node.confidence, now, now, best_row["id"]),
                )
                result = self._row_to_result(best_row, best_score)
                result.update({"created": False, "reinforced": True})
                return result

            # Layer 3: create new node
            cursor = connection.execute(
                """
                INSERT INTO nodes (
                    content, normalized_content, title, node_type, source,
                    tags_json, confidence, source_round, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    content,
                    normalized,
                    node.title.strip(),
                    node.node_type.strip() or "knowledge",
                    node.source.strip() or "panda",
                    json.dumps(list(node.tags), ensure_ascii=False),
                    node.confidence,
                    node.source_round,
                    now,
                    now,
                ),
            )
            node_id = int(cursor.lastrowid)

            # Compute embedding for new node
            if _USE_EMBEDDING:
                if best_row is None and 'new_emb' not in dir():
                    # best_row was None, but new_emb might not be computed yet
                    # (only computed in layer 2 when there are existing nodes)
                    pass
                # Compute embedding if not already done
                if 'new_emb' not in dir():
                    new_emb = self._embed(content)
                self._embeddings[str(node_id)] = new_emb
                self._invalidate_emb_cache()
                self._save_embeddings()

            # Auto-link to similar existing nodes
            links = 0
            for row in existing:
                if _USE_EMBEDDING and self._embeddings:
                    nid = str(row["id"])
                    if nid not in self._embeddings:
                        continue
                    sim = float(self._embeddings[nid] @ self._embeddings[str(node_id)])
                else:
                    sim = _lexical_similarity(content, self._search_text(row))

                if sim < _AUTO_LINK_THRESHOLD:
                    continue
                source_id, target_id = sorted((node_id, int(row["id"])))
                connection.execute(
                    """
                    INSERT INTO edges (source_id, target_id, weight, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_id, target_id)
                    DO UPDATE SET weight = MAX(edges.weight, excluded.weight)
                    """,
                    (source_id, target_id, sim, now),
                )
                links += 1

            row = connection.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            result = self._row_to_result(row, 1.0)
            result.update({"created": True, "reinforced": False, "links": links})
            return result

    # -- Public API: retrieve -------------------------------------------

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Retrieve by similarity + bounded one-hop graph propagation.

        When embeddings available: matrix multiply for O(1) per-node similarity.
        When not: falls back to lexical cosine on each node.
        """
        if not query.strip() or top_k <= 0:
            return []

        # Ensure embeddings are loaded
        if _USE_EMBEDDING and self._embeddings is None:
            self._load_embeddings()

        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM nodes").fetchall()
            by_id = {int(row["id"]): row for row in rows}

            if _USE_EMBEDDING and self._embeddings:
                # Embedding path: matrix multiply
                query_emb = self._embed(query)
                direct_scores: dict[int, float] = {}
                for node_id, row in by_id.items():
                    nid = str(node_id)
                    if nid in self._embeddings:
                        direct_scores[node_id] = float(self._embeddings[nid] @ query_emb)
                    else:
                        direct_scores[node_id] = 0.0
            else:
                # Lexical fallback
                direct_scores = {
                    node_id: _lexical_similarity(query, self._search_text(row))
                    for node_id, row in by_id.items()
                }

            scores = dict(direct_scores)
            seeds = sorted(
                direct_scores.items(), key=lambda item: item[1], reverse=True
            )[: max(top_k * 3, 10)]

            for node_id, seed_score in seeds:
                if seed_score <= 0:
                    break
                edges = connection.execute(
                    """
                    SELECT source_id, target_id, weight FROM edges
                    WHERE source_id = ? OR target_id = ?
                    """,
                    (node_id, node_id),
                ).fetchall()
                for edge in edges:
                    neighbour = (
                        edge["target_id"]
                        if edge["source_id"] == node_id
                        else edge["source_id"]
                    )
                    propagated = seed_score * float(edge["weight"]) * 0.25
                    scores[neighbour] = max(scores.get(neighbour, 0.0), propagated)

            ranked = [(node_id, score) for node_id, score in scores.items()
                      if score > _MIN_SIM_THRESHOLD]
            ranked.sort(key=lambda item: item[1], reverse=True)
            selected = ranked[:top_k]
            if selected:
                now = _utc_now()
                connection.executemany(
                    """
                    UPDATE nodes SET access_count = access_count + 1, last_accessed = ?
                    WHERE id = ?
                    """,
                    [(now, node_id) for node_id, _ in selected],
                )
            return [
                self._row_to_result(by_id[node_id], score) for node_id, score in selected
            ]

    # -- Public API: list/update/delete/stats --------------------------

    def list_all(self) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM nodes ORDER BY id").fetchall()
            return [self._row_to_result(row) for row in rows]

    def update_by_id(self, node_id: str, *, content: str) -> bool:
        content = content.strip()
        if not content:
            return False
        normalized = " ".join(content.casefold().split())
        with self._connect() as connection:
            cursor = connection.execute(
                """
                UPDATE nodes SET content = ?, normalized_content = ?, updated_at = ?
                WHERE id = ?
                """,
                (content, normalized, _utc_now(), node_id),
            )
            # Update embedding if available
            if _USE_EMBEDDING and self._embeddings is not None and cursor.rowcount == 1:
                try:
                    self._embeddings[str(node_id)] = self._embed(content)
                    self._invalidate_emb_cache()
                    self._save_embeddings()
                except Exception:
                    pass
            return cursor.rowcount == 1

    def delete_by_id(self, node_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            # Remove embedding
            if _USE_EMBEDDING and self._embeddings is not None and cursor.rowcount == 1:
                self._embeddings.pop(str(node_id), None)
                self._invalidate_emb_cache()
                self._save_embeddings()
            return cursor.rowcount == 1

    def stats(self) -> dict[str, Any]:
        with self._connect() as connection:
            nodes = int(connection.execute("SELECT COUNT(*) FROM nodes").fetchone()[0])
            edges = int(connection.execute("SELECT COUNT(*) FROM edges").fetchone()[0])
            by_type = {
                row["node_type"]: row["count"]
                for row in connection.execute(
                    "SELECT node_type, COUNT(*) AS count FROM nodes GROUP BY node_type"
                ).fetchall()
            }
        return {
            "backend": "embedded",
            "retrieval": "embedding" if _USE_EMBEDDING else "lexical",
            "path": str(self.path),
            "nodes": nodes,
            "edges": edges,
            "node_count": nodes,
            "edge_count": edges,
            "by_type": by_type,
        }


class EmbeddedMemory:
    """Compatibility facade over the local SQLite graph store."""

    _instance: EmbeddedMemory | None = None

    def __init__(
        self,
        data_dir: Path | None = None,
        *,
        storage_path: Path | None = None,
    ):
        path = storage_path or ((data_dir / "memory.sqlite3") if data_dir else _default_storage_path())
        self._store = EmbeddedMemoryStore(path)
        self.path = self._store.path
        self._store.stats()

    @classmethod
    def get(cls) -> EmbeddedMemory | None:
        """Return the process singleton, recreating it when PANDA_HOME changes."""
        desired = _default_storage_path().expanduser().resolve()
        if cls._instance is None or cls._instance.path != desired:
            try:
                cls._instance = cls(storage_path=desired)
            except Exception as error:
                print(f"[Memory] Init failed: {error}", file=sys.stderr)
                return None
        return cls._instance

    def write(
        self,
        content: str,
        title: str = "",
        node_type: str = "knowledge",
        source: str = "panda",
        *,
        tags: list[str] | tuple[str, ...] = (),
        confidence: float = 1.0,
        source_round: int | None = None,
    ) -> dict[str, Any]:
        try:
            return self._store.write_if_novel(
                MemoryNode(
                    content=content,
                    title=title,
                    node_type=node_type,
                    source=source,
                    tags=tuple(tags),
                    confidence=confidence,
                    source_round=source_round,
                )
            )
        except Exception as error:
            print(f"[Memory] Write failed: {error}", file=sys.stderr)
            return {"error": str(error)}

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        try:
            return self._store.retrieve(query, top_k=top_k)
        except Exception as error:
            print(f"[Memory] Retrieve failed: {error}", file=sys.stderr)
            return []

    def retrieve_context(self, query: str, top_k: int = 3) -> str:
        results = self.retrieve(query, top_k=top_k)
        if not results:
            return ""
        lines = ["## Past Experience (from memory)"]
        for result in results:
            score = float(result.get("score", 0.0))
            node_type = result.get("node_type", "knowledge")
            content = str(result.get("content", ""))[:500]
            lines.append(f"- [{score:.2f}] ({node_type}) {content}")
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        return self._store.stats()

    def list_all(self) -> list[dict[str, Any]]:
        return self._store.list_all()

    def update_by_id(self, node_id: str, *, content: str) -> bool:
        return self._store.update_by_id(node_id, content=content)

    def delete_by_id(self, node_id: str) -> bool:
        return self._store.delete_by_id(node_id)

    def is_available(self) -> bool:
        return "error" not in self.stats()


class MemoryClient:
    """Memory facade using embedded SQLite, with optional HTTP backend.

    The default and only out-of-the-box backend is embedded SQLite — no
    external service is required. An HTTP backend can still be configured
    by setting ``graph_url`` to a non-embedded URL, but it is never the
    default and never silently fallen back to. If the user explicitly
    configures HTTP and it is unreachable, the client returns empty
    results rather than pretending to have a memory.
    """

    def __init__(self, url: str = "embedded://", storage_path: str | Path | None = None):
        configured_url = url or "embedded://"
        self._embedded = configured_url.startswith(_EMBEDDED_SCHEMES)
        self.url = "embedded://" if self._embedded else configured_url.rstrip("/")
        self._timeout = 5
        if self._embedded and storage_path:
            self._mem = EmbeddedMemory(storage_path=Path(storage_path))
        elif self._embedded:
            self._mem = EmbeddedMemory.get()
        else:
            self._mem = None

    @classmethod
    def from_config(cls, memory_config: Any) -> MemoryClient:
        """Build a client from MemoryConfig without importing config here."""
        return cls(
            url=getattr(memory_config, "graph_url", "embedded://"),
            storage_path=getattr(memory_config, "storage_path", "") or None,
        )

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        if self._embedded:
            return self._mem.retrieve(query, top_k=top_k) if self._mem else []
        try:
            response = requests.post(
                f"{self.url}/api/retrieve",
                json={"query": query, "top_k": top_k},
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.json().get("results", [])
        except Exception:
            return []

    def write(
        self,
        content: str,
        title: str = "",
        node_type: str = "knowledge",
        source: str = "panda",
        *,
        tags: list[str] | tuple[str, ...] = (),
        confidence: float = 1.0,
        source_round: int | None = None,
    ) -> dict[str, Any]:
        if self._embedded:
            if not self._mem:
                return {"error": "memory not available"}
            return self._mem.write(
                content,
                title=title,
                node_type=node_type,
                source=source,
                tags=tags,
                confidence=confidence,
                source_round=source_round,
            )
        try:
            response = requests.post(
                f"{self.url}/api/write",
                json={
                    "content": content,
                    "title": title,
                    "node_type": node_type,
                    "source": source,
                    "tags": list(tags),
                    "confidence": confidence,
                    "source_round": source_round,
                    "auto_link": True,
                },
                timeout=self._timeout,
            )
            response.raise_for_status()
            return response.json()
        except Exception as error:
            return {"error": str(error)}

    def retrieve_context(self, query: str, top_k: int = 3) -> str:
        if self._embedded:
            return self._mem.retrieve_context(query, top_k=top_k) if self._mem else ""
        results = self.retrieve(query, top_k=top_k)
        if not results:
            return ""
        lines = ["## Past Experience (from memory)"]
        for result in results:
            lines.append(
                f"- [{float(result.get('score', 0)):.2f}] "
                f"({result.get('node_type', 'knowledge')}) "
                f"{str(result.get('content', ''))[:500]}"
            )
        return "\n".join(lines)

    def stats(self) -> dict[str, Any]:
        if self._embedded:
            return self._mem.stats() if self._mem else {"error": "memory not available"}
        try:
            response = requests.get(f"{self.url}/api/stats", timeout=self._timeout)
            response.raise_for_status()
            return response.json()
        except Exception:
            return {"error": "graph memory not running"}

    def is_available(self) -> bool:
        if self._embedded:
            return self._mem is not None and self._mem.is_available()
        try:
            response = requests.get(f"{self.url}/api/stats", timeout=2)
            return response.status_code == 200
        except Exception:
            return False

    def list_all(self) -> list[dict[str, Any]]:
        return self._mem.list_all() if self._embedded and self._mem else []

    def update_by_id(self, node_id: str, *, content: str) -> bool:
        return bool(
            self._embedded
            and self._mem
            and self._mem.update_by_id(node_id, content=content)
        )

    def delete_by_id(self, node_id: str) -> bool:
        return bool(self._embedded and self._mem and self._mem.delete_by_id(node_id))
