"""Test: SYSTEM_PROMPT updated for native function calling.

Native FC handles tool calling — prompt should NOT teach TOOL_CALL: text format.
Should still teach DONE:/FAILED: for task completion signaling.
"""
import sys, os
sys.path.insert(0, r'E:\workspace\evo-agent\src')
os.environ['PANDA_HOME'] = os.path.expanduser('~/.panda')

from panda_agent.brain import build_system_prompt


class TestPromptNativeFC:
    """System prompt should not contain TOOL_CALL text instructions."""

    def test_no_tool_call_instruction(self):
        """Prompt should NOT teach LLM to output TOOL_CALL: {json}."""
        prompt = build_system_prompt("test tools")
        assert "TOOL_CALL:" not in prompt, \
            "Prompt should not contain TOOL_CALL: — native FC handles this"

    def test_done_instruction_present(self):
        """Prompt must still teach DONE: for task completion."""
        prompt = build_system_prompt("test tools")
        assert "DONE:" in prompt, \
            "Prompt must contain DONE: for completion signaling"

    def test_failed_instruction_present(self):
        """Prompt must still teach FAILED: for giving up."""
        prompt = build_system_descriptor = build_system_prompt("test tools")
        assert "FAILED:" in prompt, \
            "Prompt must contain FAILED: for failure signaling"

    def test_os_detection_present(self):
        """Prompt must still teach OS detection."""
        prompt = build_system_prompt("test tools")
        assert "Windows" in prompt or "operating system" in prompt.lower()

    def test_write_file_rule_present(self):
        """Prompt must still require write_file for file creation."""
        prompt = build_system_prompt("test tools")
        assert "write_file" in prompt

    def test_react_workflow_simplified(self):
        """Prompt should not have 'Action: call a tool' text protocol instructions."""
        prompt = build_system_prompt("test tools")
        # ReAct workflow can mention tools, but not in the text-protocol sense
        assert "Action: call a tool" not in prompt, \
            "Prompt should not describe Action: text protocol"
