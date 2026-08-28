"""Test: tools.py generates OpenAI-compatible function calling schema.

Phase 1: get_tool_schemas() returns the tools list for API request.
"""
from panda_agent.tools import get_tool_schemas, TOOLS


class TestToolSchemas:
    """get_tool_schemas() must return OpenAI-compatible tool definitions."""

    def test_returns_list(self):
        schemas = get_tool_schemas()
        assert isinstance(schemas, list)
        assert len(schemas) > 0

    def test_schema_format(self):
        """Each schema must have type=function, function={name, description, parameters}."""
        schemas = get_tool_schemas()
        for s in schemas:
            assert s["type"] == "function"
            fn = s["function"]
            assert "name" in fn
            assert "description" in fn
            assert "parameters" in fn
            params = fn["parameters"]
            assert params["type"] == "object"
            assert "properties" in params

    def test_write_file_schema(self):
        """write_file schema must have path and content properties."""
        schemas = get_tool_schemas()
        wf = [s for s in schemas if s["function"]["name"] == "write_file"][0]
        props = wf["function"]["parameters"]["properties"]
        assert "path" in props
        assert "content" in props
        assert props["path"]["type"] == "string"
        assert props["content"]["type"] == "string"
        required = wf["function"]["parameters"].get("required", [])
        assert "path" in required
        assert "content" in required

    def test_all_tools_have_schemas(self):
        """Every registered tool must have a schema."""
        schemas = get_tool_schemas()
        schema_names = {s["function"]["name"] for s in schemas}
        tool_names = set(TOOLS.keys())
        assert tool_names == schema_names, f"Missing: {tool_names - schema_names}"

    def test_run_command_has_timeout(self):
        """run_command schema must have command (required) and timeout (optional)."""
        schemas = get_tool_schemas()
        rc = [s for s in schemas if s["function"]["name"] == "run_command"][0]
        props = rc["function"]["parameters"]["properties"]
        assert "command" in props
        assert "timeout" in props
        assert props["timeout"]["type"] == "integer"
        required = rc["function"]["parameters"].get("required", [])
        assert "command" in required
        # timeout is optional
        assert "timeout" not in required
