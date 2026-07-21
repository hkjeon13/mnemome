from __future__ import annotations

import json

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
            root = await client.get("/")
            assert root.status_code == 307
            assert root.headers["location"] == "/playground"

            page = await client.get("/playground")
            assert page.status_code == 200
            assert "Mnemome · Agent Memory Lab" in page.text
            assert "기억을 넣고" not in page.text
            assert "Playground" in page.text
            assert "API Documents" in page.text
            assert "Lotte Agent</span>" not in page.text
            assert "데모 처리 흐름" not in page.text
            assert "runtime-status" not in page.text
            assert "memory-count" not in page.text
            assert "system-note" not in page.text
            assert 'id="trace-section"' in page.text
            assert 'id="trace-view-tab"' in page.text
            assert 'id="culture-view-tab"' in page.text
            assert 'id="culture-view"' in page.text
            assert "공유 응답 원칙" in page.text
            assert "읽기 전용" in page.text
            assert 'id="trace-section"' in page.text
            assert 'aria-label="Agent 실행 및 메모리 추적" hidden' in page.text
            assert "20260721-plan-step-status" in page.text
            assert "LOTTE AGENT TRACE" in page.text
            assert "메모리 적용 지점" in page.text
            assert "lucide-refresh-cw" in page.text
            assert 'id="open-new-conversation"' in page.text
            assert 'id="new-conversation-dialog"' in page.text
            assert 'id="clear-memories-dialog"' in page.text
            assert 'data-icon="lucide-trash-2"' in page.text
            assert "삭제한 기억은 되돌릴 수 없습니다" in page.text
            assert "저장된 장기 메모리는 그대로 유지" in page.text
            script = await client.get("/static/app.js")
            assert script.status_code == 200
            assert 'addEventListener("compositionstart"' in script.text
            assert 'addEventListener("compositionend"' in script.text
            assert "event.isComposing" in script.text
            assert 'event === "progress"' in script.text
            assert 'matchMedia("(max-width: 900px)")' in script.text
            assert 'appendMessage("assistant", "")' in script.text
            assert 'setAttribute("aria-label", "실행 계획 생성 중")' in script.text

            documents = await client.get("/documents")
            assert documents.status_code == 200
            assert "API Documents" in documents.text
            assert "준비 중" in documents.text

            status = await client.get("/demo/api/status")
            assert status.status_code == 200
            assert status.json()["runtime"] == "lotte-agent 0.0.11"
            assert status.json()["runtime_available"] is True
            assert status.json()["model"] == "gpt-live-test"
            assert status.json()["mcp_configured"] is True
            assert status.json()["cultural_memory_configured"] is True
            assert status.json()["memory_count"] == 3

            culture = await client.get("/demo/api/cultural-snapshot")
            assert culture.status_code == 200
            assert culture.json()["snapshot"]["read_only"] is True
            assert culture.json()["snapshot"]["policy_version"] == "mnemome-demo-culture-v1"
            assert len(culture.json()["items"]) == 2
            assert all(item["read_only"] for item in culture.json()["items"])

            streamed = await client.post(
                "/demo/api/chat/stream", json={"query": "한국어 스트림 응답을 보여줘"}
            )
            assert streamed.status_code == 200
            assert streamed.headers["content-type"].startswith("text/event-stream")
            assert "event: ready" in streamed.text
            assert "event: progress" in streamed.text
            assert "event: delta" in streamed.text
            assert "event: complete" in streamed.text
            progress_line = next(
                line
                for block in streamed.text.split("\n\n")
                if block.startswith("event: progress")
                for line in block.splitlines()
                if line.startswith("data: ")
            )
            progress_payload = json.loads(progress_line.removeprefix("data: "))
            assert progress_payload["kind"] == "plan"
            assert progress_payload["steps"]
            assert progress_payload["steps"][0]["title"]
            progress_payloads = [
                json.loads(line.removeprefix("data: "))
                for block in streamed.text.split("\n\n")
                if block.startswith("event: progress")
                for line in block.splitlines()
                if line.startswith("data: ")
            ]
            progress_kinds = {payload["kind"] for payload in progress_payloads}
            assert {"plan", "step_start", "step_complete"} <= progress_kinds
            complete_line = next(
                line
                for block in streamed.text.split("\n\n")
                if block.startswith("event: complete")
                for line in block.splitlines()
                if line.startswith("data: ")
            )
            streamed_payload = json.loads(complete_line.removeprefix("data: "))
            assert streamed_payload["answer"]
            assert streamed_payload["execution_trace"]["steps"]

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
            assert payload["memory_trace"]["cultural"]["status"] == "applied"
            assert payload["memory_trace"]["cultural"]["count"] == 2
            assert payload["preference_captured"] is False
            assert payload["mcp"] == {
                "status": "connected",
                "tool_count": 1,
                "tools": ["search_retrieve"],
            }
            assert any("[Mnemome 장기 기억]" in messages for messages in seen_messages)
            assert any("[문화적 기억" in messages for messages in seen_messages)
            assert any(
                "단순히 '분쟁지역'이라고 규정하지 않는다" in messages
                for messages in seen_messages
            )
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
            conversations = [
                item for item in memories.json()["items"] if item["kind"] == "conversation"
            ]
            assert all(item["conversation"]["query"] for item in conversations)
            assert all(item["conversation"]["answer"] == item["content"] for item in conversations)
            assert any(
                item["conversation"]["query"] == "한국어로 간결하게 답변해줘"
                for item in conversations
            )
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
            culture_after_clear = await client.get("/demo/api/cultural-snapshot")
            assert culture_after_clear.json()["snapshot"]["id"] == culture.json()["snapshot"]["id"]

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://another.test"
        ) as isolated_client:
            isolated_status = await isolated_client.get("/demo/api/status")
            isolated_memories = await isolated_client.get("/demo/api/memories")
            assert isolated_status.json()["memory_count"] == 3
            assert len(isolated_memories.json()["items"]) == 3
