import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from caiocore.agent.loop import AgentLoop
from caiocore.agent.tools.base import Tool
from caiocore.agent.tools.registry import ToolRegistry
from caiocore.providers.base import LLMResponse, ToolCallRequest


class Critical(Tool):
    name = "email_send"
    description = "test"
    parameters = {"type": "object", "properties": {}, "required": []}
    def __init__(self):
        self.calls = []
    @property
    def requires_approval(self):
        return True
    async def execute(self, **kwargs):
        self.calls.append(kwargs)
        return "sent"


def test_loop_ignores_legacy_approval_for_multiple_changed_payloads():
    async def exercise():
        agent = AgentLoop.__new__(AgentLoop)
        agent.provider = SimpleNamespace(disable_agent_tools=False)
        agent.tools = ToolRegistry()
        tool = Critical()
        agent.tools.register(tool)
        agent.config = SimpleNamespace(agents=SimpleNamespace(defaults=SimpleNamespace(autonomous=True)))
        agent.max_iterations = 2
        agent.model = "test"
        agent.max_tokens = 100
        agent.temperature = 0
        agent.smart_router = SimpleNamespace(enabled=False)
        agent.tracer = SimpleNamespace(log_thought=AsyncMock())
        agent.context = SimpleNamespace(
            add_assistant_message=lambda messages, *a, **kw: messages,
            add_tool_result=lambda messages, *a, **kw: messages)
        agent._call_provider_with_fallback = AsyncMock(side_effect=[
            LLMResponse(None, [ToolCallRequest("1", "email_send", {"body": "first"}),
                               ToolCallRequest("2", "email_send", {"body": "changed"})]),
            LLMResponse("done")])
        session = SimpleNamespace(key="test", metadata={"user_just_approved": True}, add_message=MagicMock())
        _, used = await agent._run_agent_loop([{"role": "user", "content": "sim"}], session=session)
        assert used == ["email_send", "email_send"]
        assert not tool.calls
        results = [c.kwargs["content"] for c in session.add_message.call_args_list if c.kwargs["role"] == "tool"]
        assert len(results) == 2 and all("BLOQUEADO" in result for result in results)
    asyncio.run(exercise())
