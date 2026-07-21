from __future__ import annotations

import httpx
import pytest
from lotte_agent.memory import MemoryEntry, MemoryEntryKind
from lotte_agent.models.base import AsyncModelBase
from lotte_agent.models.model_types import ModelOutput
from lotte_agent.tools import ToolSpec

from mnemome import Mnemome
from mnemome.adapters import InMemoryStores
from mnemome.integrations.lotte_agent import MnemomeLongTermMemory
from mnemome.service.app import create_app
from mnemome.service.demo import _needs_fresh_search
from mnemome.service.settings import ApiPrincipal, Settings


def test_news_query_requires_fresh_search() -> None:
    assert _needs_fresh_search("엔비디아 뉴스") is True
    assert _needs_fresh_search("오늘 엔비디아 관련 소식 찾아줘") is True
    assert _needs_fresh_search("내가 선호하는 답변 방식은?") is False


@pytest.mark.asyncio
async def test_lotte_memory_protocol_round_trip() -> None:
    mnemome = Mnemome.in_memory()
    await mnemome.initialize()
    memory = MnemomeLongTermMemory(mnemome.application, "tenant-a")
    await memory.store(
        MemoryEntry(
            id="pref-1",
            kind=MemoryEntryKind.PREFERENCE,
            content="답변은 한국어로 간결하게",
            metadata={"source_type": "profile", "source_id": "user-1"},
            tags=["language", "style"],
        )
    )

    recalled = await memory.search("한국어 간결하게", top_k=3)
    assert [entry.id for entry in recalled] == ["pref-1"]
    assert recalled[0].kind == MemoryEntryKind.PREFERENCE
    assert recalled[0].tags == ["language", "style"]
    assert await memory.delete("pref-1") is True
    assert await memory.retrieve("pref-1") is None


@pytest.mark.asyncio
async def test_demo_page_runs_lotte_agent_with_mnemome_memory(monkeypatch) -> None:
    seen_messages: list[str] = []

    class FakeLiveOpenAIModel(AsyncModelBase):
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, messages, *args, **kwargs):
            del args, kwargs
            seen_messages.append(str(messages))
            self.calls += 1
            if self.calls == 1:
                text = '("[final_answer] 저장된 장기 기억으로 한국어 답변",)'
            else:
                text = "저장된 선호에 따라 한국어로 간결하게 답변합니다."
            return ModelOutput(model="gpt-live-test", text=text, finish_reason="stop")

        def generate_stream(self, messages, *args, **kwargs):
            async def iterator():
                yield await self.generate(messages, *args, **kwargs)

            return iterator()

    import lotte_agent.models
    import lotte_agent.tools

    async def fake_tool(**kwargs):
        return kwargs

    class FakeMcpClient:
        def __init__(self, url: str) -> None:
            assert url == "https://assistant.fin-ally.net/mcp"

        async def __aenter__(self):
            return [
                ToolSpec(name="search_retrieve", fn=fake_tool, description="Search"),
                ToolSpec(name="local_knowledge", fn=fake_tool, description="Unsafe write tool"),
            ]

        async def __aexit__(self, exc_type, exc, tb) -> None:
            return None

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-live-test")
    monkeypatch.setenv("MNEMOME_MCP_URL", "https://assistant.fin-ally.net/mcp")
    monkeypatch.setattr(lotte_agent.tools, "McpToolSpecClient", FakeMcpClient)
    monkeypatch.setattr(
        lotte_agent.models,
        "AsyncOpenAIClient",
        lambda **kwargs: FakeLiveOpenAIModel(),
    )
    settings = Settings(
        environment="test",
        database_path=":memory:",
        api_keys={"unused": ApiPrincipal("tenant", "principal", frozenset({"admin"}))},
        log_level="WARNING",
    )
    app = create_app(settings, stores=InMemoryStores())
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://demo.test"
        ) as client:
            page = await client.get("/")
            assert page.status_code == 200
            assert "Mnemome · Agent Memory Lab" in page.text
            assert "기억을 넣고" not in page.text
            assert "20260721-light" in page.text
            assert "LOTTE AGENT TRACE" in page.text
            assert "메모리 적용 지점" in page.text
            assert "기록 비우기" in page.text

            status = await client.get("/demo/api/status")
            assert status.status_code == 200
            assert status.json()["runtime"] == "lotte-agent 0.0.11"
            assert status.json()["runtime_available"] is True
            assert status.json()["model"] == "gpt-live-test"
            assert status.json()["mcp_configured"] is True
            assert status.json()["memory_count"] == 3

            response = await client.post(
                "/demo/api/chat", json={"query": "한국어로 간결하게 답변해줘"}
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["runtime"] == "AsyncToolCallingAgent"
            assert payload["model"] == "gpt-live-test"
            assert payload["recalled"]
            assert "한국어" in payload["answer"]
            assert payload["execution_trace"]["plan"]["step_count"] >= 1
            assert payload["execution_trace"]["steps"][0]["title"]
            assert payload["memory_trace"]["long_term"]["status"] == "applied"
            assert payload["memory_trace"]["long_term"]["retriever"].startswith("BM25 · ")
            assert payload["memory_trace"]["short_term"]["status"] == "applied"
            assert payload["memory_trace"]["cultural"]["status"] == "not_configured"
            assert payload["preference_captured"] is False
            assert payload["mcp"] == {
                "status": "connected",
                "tool_count": 1,
                "tools": ["search_retrieve"],
            }
            assert any("[Mnemome 장기 기억]" in messages for messages in seen_messages)
            assert any("답변은 핵심부터 한국어로" in messages for messages in seen_messages)

            preference_text = "앞으로 지명을 나타낼 때는 한자 표기도 함께 표시해줘."
            preference_response = await client.post(
                "/demo/api/chat", json={"query": preference_text}
            )
            assert preference_response.status_code == 200, preference_response.text
            assert preference_response.json()["preference_captured"] is True

            memories = await client.get("/demo/api/memories")
            assert memories.json()["seeded_count"] == 3
            assert memories.json()["clearable_count"] >= 3
            kinds = [item["kind"] for item in memories.json()["items"]]
            assert "conversation" in kinds
            preferences = [
                item for item in memories.json()["items"] if item["kind"] == "preference"
            ]
            assert any(item["content"] == preference_text for item in preferences)

            seeded = next(item for item in memories.json()["items"] if item["is_seed"])
            protected = await client.delete(f"/demo/api/memories/{seeded['id']}")
            assert protected.status_code == 409

            cleared = await client.delete("/demo/api/memories")
            assert cleared.status_code == 200
            assert cleared.json()["cleared"] >= 3
            assert cleared.json()["preserved"] == 3
            remaining = await client.get("/demo/api/memories")
            assert remaining.json()["clearable_count"] == 0
            assert len(remaining.json()["items"]) == 3
            assert all(item["is_seed"] for item in remaining.json()["items"])

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://another.test"
        ) as isolated_client:
            isolated_status = await isolated_client.get("/demo/api/status")
            isolated_memories = await isolated_client.get("/demo/api/memories")
            assert isolated_status.json()["memory_count"] == 3
            assert len(isolated_memories.json()["items"]) == 3
