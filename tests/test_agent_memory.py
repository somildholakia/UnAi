import pytest

from unai_engine.agent.orchestrator import AgentOrchestrator


class FakeProvider:
    model = "fake-local"

    def __init__(self):
        self.messages = None

    async def complete(self, messages):
        self.messages = messages
        return "answer"


@pytest.mark.asyncio
async def test_agent_attaches_bounded_brain_references_as_untrusted_context():
    provider = FakeProvider()
    searches = []

    def search(query: str, limit: int):
        searches.append((query, limit))
        return [{"path": "Projects/unAI.md", "title": "unAI", "excerpt": "Graph memory design."}]

    agent = AgentOrchestrator(provider, context_search=search)
    result = await agent.chat("How did we design memory?", [])

    assert result == "answer"
    assert searches == [("How did we design memory?", 4)]
    memory_message = provider.messages[1]["content"]
    assert "Projects/unAI.md" in memory_message
    assert "untrusted reference data" in memory_message


@pytest.mark.asyncio
async def test_agent_does_not_fail_when_brain_is_unavailable():
    provider = FakeProvider()

    def broken_search(_query: str, _limit: int):
        raise OSError("index is unavailable")

    agent = AgentOrchestrator(provider, context_search=broken_search)
    assert await agent.chat("Continue", []) == "answer"
    assert len(provider.messages) == 2
