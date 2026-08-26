"""Request handlers — benchmark fixture.

Intentionally the longest file in the fixture so that "which file has the
most lines" has a determinate answer.
"""

from __future__ import annotations

from .config import DEFAULT_PORT, MAX_RETRIES, TIMEOUT

# TODO: add structured logging instead of print
_ROUTES: dict[str, str] = {}


def register(path: str, name: str) -> None:
    """Register a route handler by name."""
    if not path.startswith("/"):
        raise ValueError(f"path must start with '/': {path!r}")
    if path in _ROUTES:
        raise ValueError(f"route already registered: {path}")
    _ROUTES[path] = name


def resolve(path: str) -> str | None:
    """Return the handler name for a path, or None."""
    return _ROUTES.get(path)


def list_routes() -> list[tuple[str, str]]:
    """Return all routes sorted by path."""
    return sorted(_ROUTES.items())


def handle_index() -> dict[str, object]:
    return {"status": "ok", "port": DEFAULT_PORT}


def handle_health() -> dict[str, object]:
    return {"status": "healthy", "timeout": TIMEOUT, "retries": MAX_RETRIES}


def handle_echo(payload: str) -> dict[str, str]:
    if not payload:
        return {"error": "empty payload"}
    return {"echo": payload}


def clear_routes() -> None:
    """Reset registry — used between tests."""
    _ROUTES.clear()


def route_count() -> int:
    return len(_ROUTES)
