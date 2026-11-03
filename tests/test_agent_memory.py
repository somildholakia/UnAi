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


class SpecialistFakeProvider:
    def __init__(self):
        self.requests = []

    async def complete(self, messages, *, model_override=None):
        self.requests.append((messages, model_override))
        return f"answer from {model_override}"


@pytest.mark.asyncio
async def test_specialist_chat_uses_only_the_configured_role_model():
    provider = SpecialistFakeProvider()
    agent = AgentOrchestrator(
        provider,
        specialist_models={
            "next_action": "unai-next-action-adapter",
            "planner": "unai-planner-adapter",
            "verifier": "unai-verifier-adapter",
        },
    )

    answer, model = await agent.specialist_chat(
        "planner",
        "Plan the repository inspection.",
        [{"role": "user", "content": "Earlier context"}],
    )

    assert answer == "answer from unai-planner-adapter"
    assert model == "unai-planner-adapter"
    messages, override = provider.requests[0]
    assert override == "unai-planner-adapter"
    assert "planning specialist" in messages[0]["content"]
    assert "\\n\\n" not in messages[0]["content"]
    assert messages[-1] == {"role": "user", "content": "Plan the repository inspection."}


@pytest.mark.asyncio
async def test_unconfigured_specialist_fails_closed_without_base_model_fallback():
    from unai_engine.agent.orchestrator import SpecialistUnavailable

    provider = SpecialistFakeProvider()
    agent = AgentOrchestrator(provider)

    with pytest.raises(SpecialistUnavailable, match="not configured"):
        await agent.specialist_chat("verifier", "Check this evidence.", [])

    assert provider.requests == []


@pytest.mark.asyncio
async def test_specialist_chat_rejects_unknown_roles():
    provider = SpecialistFakeProvider()
    agent = AgentOrchestrator(provider, specialist_models={"next_action": "adapter"})

    with pytest.raises(ValueError, match="Unsupported specialist role"):
        await agent.specialist_chat("general", "Do something.", [])

    assert provider.requests == []
