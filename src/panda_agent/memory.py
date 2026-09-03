"""Persistent graph memory with a dependency-free embedded SQLite backend."""

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
_AUTO_LINK_THRESHOLD = 0.12


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _default_storage_path() -> Path:
    panda_home = Path(os.getenv("PANDA_HOME", str(Path.home() / ".panda")))
    return panda_home / "memory" / "memory.sqlite3"


def _tokens(text: str) -> Counter[str]:
    return Counter(_TOKEN_RE.findall(text.casefold()))


def _similarity(left: str, right: str) -> float:
    """Return deterministic lexical cosine similarity."""
    left_counts = _tokens(left)
    right_counts = _tokens(right)
    if not left_counts or not right_counts:
        return 0.0
    dot = sum(left_counts[token] * right_counts.get(token, 0) for token in left_counts)
    left_norm = math.sqrt(sum(value * value for value in left_counts.values()))
    right_norm = math.sqrt(sum(value * value for value in right_counts.values()))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


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
    """Local graph store backed only by Python's standard library."""

    def __init__(self, path: Path):
        self.path = path.expanduser().resolve()

    def _connect(self):
        """Open a connection as a context manager that commits and closes.

        Returning the raw ``sqlite3.Connection`` and using it as a context
        manager (``with self._connect() as connection:``) commits on a clean
        exit but does **not** close the connection. On Windows the leftover
        open connection holds a file lock on ``memory.sqlite3``, which blocks
        ``TemporaryDirectory`` cleanup in tests (``WinError 32``) and prevents
        the DB file from being moved/deleted by the caller.

        This wrapper runs the connection's own context manager first (so
        commits on success / rolls back on exception), then closes it in a
        ``finally`` so callers keep the ``with self._connect() as connection:``
        spelling without leaking locks or losing transactions.
        """
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
                # Delegate to the connection's own context manager so a clean
                # exit commits and an exception rolls back.
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

    def write_if_novel(self, node: MemoryNode, threshold: float = 0.92) -> dict[str, Any]:
        """Persist a node or reinforce a highly similar node of the same type."""
        content = node.content.strip()
        if not content:
            raise ValueError("memory content must not be empty")
        if not 0.0 <= node.confidence <= 1.0:
            raise ValueError("memory confidence must be between 0 and 1")

        normalized = " ".join(content.casefold().split())
        now = _utc_now()
        with self._connect() as connection:
            existing = connection.execute(
                "SELECT * FROM nodes WHERE node_type = ?", (node.node_type,)
            ).fetchall()
            best_row: sqlite3.Row | None = None
            best_score = 0.0
            for row in existing:
                score = (
                    1.0
                    if row["normalized_content"] == normalized
                    else _similarity(content, row["content"])
                )
                if score > best_score:
                    best_row, best_score = row, score

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
            links = 0
            for row in existing:
                score = _similarity(content, self._search_text(row))
                if score < _AUTO_LINK_THRESHOLD:
                    continue
                source_id, target_id = sorted((node_id, int(row["id"])))
                connection.execute(
                    """
                    INSERT INTO edges (source_id, target_id, weight, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(source_id, target_id)
                    DO UPDATE SET weight = MAX(edges.weight, excluded.weight)
                    """,
                    (source_id, target_id, score, now),
                )
                links += 1
            row = connection.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
            result = self._row_to_result(row, 1.0)
            result.update({"created": True, "reinforced": False, "links": links})
            return result

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Retrieve by lexical score plus bounded one-hop graph propagation."""
        if not query.strip() or top_k <= 0:
            return []
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM nodes").fetchall()
            by_id = {int(row["id"]): row for row in rows}
            direct_scores = {
                node_id: _similarity(query, self._search_text(row))
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

            ranked = [(node_id, score) for node_id, score in scores.items() if score > 0]
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
            return cursor.rowcount == 1

    def delete_by_id(self, node_id: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
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
    """Memory facade using embedded SQLite by default and HTTP optionally."""

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
