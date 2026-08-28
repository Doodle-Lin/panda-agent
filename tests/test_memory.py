"""End-to-end tests for the embedded persistent graph memory."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from panda_agent.memory import EmbeddedMemory, MemoryClient


@pytest.fixture
def memory(tmp_path):
    return EmbeddedMemory(data_dir=tmp_path)


def test_memory_client_initializes_without_private_package(tmp_path):
    client = MemoryClient(storage_path=tmp_path / "memory.sqlite3")

    assert client.is_available() is True
    assert client.stats()["backend"] == "embedded"


def test_write_retrieve_and_persist_across_clients(tmp_path):
    path = tmp_path / "memory.sqlite3"
    first = MemoryClient(storage_path=path)
    created = first.write(
        "Python is a programming language for data science",
        title="python",
        source="test",
    )
    second = MemoryClient(storage_path=path)

    results = second.retrieve("Python programming", top_k=5)

    assert created["created"] is True
    assert created["source"] == "test"
    assert results[0]["id"] == created["id"]
    assert "Python" in results[0]["content"]


def test_unrelated_and_empty_queries_return_empty(memory):
    memory.write("Python is a programming language for data science")

    assert memory.retrieve("zzz qq www xx", top_k=5) == []
    assert memory.retrieve("") == []
    assert memory.retrieve_context("") == ""


def test_retrieve_context_has_type_and_score(memory):
    memory.write(
        "Test knowledge for context formatting check",
        title="format-test",
        node_type="reference",
    )

    context = memory.retrieve_context("Test knowledge", top_k=3)

    assert "## Past Experience" in context
    assert "(reference)" in context
    assert "[" in context


def test_related_nodes_are_linked_and_reported(memory):
    memory.write("vLLM uses PagedAttention to manage KV cache pages")
    memory.write("PagedAttention manages vLLM KV cache memory pages")

    stats = memory.stats()

    assert stats["node_count"] == 2
    assert stats["edge_count"] >= 1


def test_duplicate_node_is_reinforced(memory):
    first = memory.write("Search results should include line numbers")
    duplicate = memory.write("Search results should include line numbers")

    assert duplicate["id"] == first["id"]
    assert duplicate["created"] is False
    assert duplicate["reinforced"] is True
    assert memory.stats()["node_count"] == 1


def test_chinese_content_is_retrievable(memory):
    memory.write("大语言模型推理优化是AI基础设施的关键技术", title="LLM推理优化")

    results = memory.retrieve("大语言模型推理", top_k=5)

    assert results
    assert "大语言模型" in results[0]["content"]


def test_list_update_and_delete_public_api(tmp_path):
    client = MemoryClient(storage_path=tmp_path / "memory.sqlite3")
    node = client.write("Temporary verbose memory", title="temp")

    assert client.list_all()[0]["content"] == "Temporary verbose memory"
    assert client.update_by_id(node["id"], content="Concise memory") is True
    assert client.list_all()[0]["content"] == "Concise memory"
    assert client.delete_by_id(node["id"]) is True
    assert client.list_all() == []


def test_http_backend_remains_compatible():
    with patch("panda_agent.memory.requests.post") as request:
        request.return_value.json.return_value = {
            "results": [{"id": "remote-1", "content": "remote result", "score": 0.8}]
        }
        request.return_value.raise_for_status.return_value = None

        result = MemoryClient(url="http://memory.example").retrieve("remote")

    assert result[0]["id"] == "remote-1"
    assert request.call_args.args[0] == "http://memory.example/api/retrieve"


def test_singleton_follows_panda_home(tmp_path, monkeypatch):
    monkeypatch.setenv("PANDA_HOME", str(tmp_path))
    EmbeddedMemory._instance = None

    memory = EmbeddedMemory.get()

    assert memory is not None
    assert memory.path == (tmp_path / "memory" / "memory.sqlite3").resolve()
