"""Graph memory client — retrieves and writes knowledge via API.

Connects to the graph-memory FastAPI server (default: http://127.0.0.1:9121).
Uses embedding + Personalized PageRank for associative recall.
"""

from __future__ import annotations

import json
from typing import Any

import requests


class MemoryClient:
    """Client for the graph-memory API.

    If the server is not running, all operations gracefully degrade
    to no-ops (return empty results) instead of crashing.
    """

    def __init__(self, url: str = "http://127.0.0.1:9121"):
        self.url = url.rstrip("/")
        self._timeout = 5

    def retrieve(self, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        """Retrieve related knowledge from the graph.

        Uses embedding similarity + PageRank graph diffusion.
        Returns list of {content, score, title, node_type, ...}.
        """
        try:
            resp = requests.post(
                f"{self.url}/api/retrieve",
                json={"query": query, "top_k": top_k},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
        except Exception:
            return []

    def write(self, content: str, title: str = "", node_type: str = "knowledge") -> dict:
        """Write a knowledge node to the graph with auto-linking."""
        try:
            resp = requests.post(
                f"{self.url}/api/write",
                json={
                    "content": content,
                    "title": title,
                    "node_type": node_type,
                    "auto_link": True,
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

    def search(self, query: str) -> list[dict[str, Any]]:
        """Quick GET search (alias for retrieve)."""
        try:
            resp = requests.get(
                f"{self.url}/api/search",
                params={"q": query},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("results", [])
        except Exception:
            return []

    def stats(self) -> dict[str, Any]:
        """Get graph statistics."""
        try:
            resp = requests.get(f"{self.url}/api/stats", timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()
        except Exception:
            return {"error": "graph memory not running"}

    def is_available(self) -> bool:
        """Check if the graph memory server is running."""
        try:
            resp = requests.get(f"{self.url}/api/stats", timeout=2)
            return resp.status_code == 200
        except Exception:
            return False

    def retrieve_context(self, query: str, top_k: int = 3) -> str:
        """Retrieve and format as context string for LLM injection.

        Returns empty string if no memory or server unavailable.
        """
        results = self.retrieve(query, top_k=top_k)
        if not results:
            return ""
        lines = ["## Relevant Memory"]
        for r in results:
            score = r.get("score", 0)
            content = r.get("content", "")[:300]
            lines.append(f"- [{score:.2f}] {content}")
        return "\n".join(lines)
