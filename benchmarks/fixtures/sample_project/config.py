"""Sample project configuration — benchmark fixture."""

DEFAULT_PORT = 8080
DEFAULT_HOST = "127.0.0.1"
MAX_RETRIES = 3

# TODO: make the timeout configurable per-endpoint
TIMEOUT = 30


def build_url(path: str = "/") -> str:
    return f"http://{DEFAULT_HOST}:{DEFAULT_PORT}{path}"
