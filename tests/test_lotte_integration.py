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
from mnemome.service.prompting import PROMPT_OVERLAY_PATH, build_demo_prompt_template
from mnemome.service.settings import ApiPrincipal, Settings


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


def test_demo_prompt_layers_policy_onto_lotte_default_yaml() -> None:
    prompt_template = build_demo_prompt_template()
    overlay_text = PROMPT_OVERLAY_PATH.read_text(encoding="utf-8")

    assert "STRICT PLAN GENERATION RULES" in prompt_template["plan"]
    assert "Mnemome unified memory-aware planning policy" in prompt_template["plan"]
    assert "For targets A, B, and C" in prompt_template["plan"]
    assert "every target retrieval step must use [search_retrieve]" in prompt_template["plan"]
    assert "Never use company_search, company_analysis" in prompt_template["plan"]
    assert "read-only memory question" in prompt_template["plan"]
    assert "never merge existing stored preferences" in prompt_template["plan"]
    assert "must never be included" in prompt_template["plan"]
    assert "Now, there is the actual planning task:" in prompt_template["plan"]
    assert "Now, there is the actual task:" in prompt_template["step"]
    assert "Mnemome MCP step execution policy" in prompt_template["step"]
    assert "the A step must query A only" in prompt_template["step"]
    assert "Exclude current-turn constraints" in prompt_template["step"]
    assert "Metadata.current_user_request is the authoritative source" in prompt_template["step"]
    assert "Never introduce the response as an equal A, B, C" in prompt_template["step"]
    assert "Mnemome preferences are intentionally unavailable" in prompt_template["step"]
    assert "Final Answer Instruction" in prompt_template["final_instruction"]
    assert "Mnemome final response policy" in prompt_template["final_instruction"]
    assert "A 주요 뉴스와 관련 기업(B, C)" in prompt_template["final_instruction"]
    assert "Do not say they were newly stored" in prompt_template["final_instruction"]
    assert "Mnemome unified memory-aware planning policy" in prompt_template["replan"]
    assert "Mnemome unified memory-aware planning policy" in prompt_template["plan_repair"]
    assert "NVIDIA" not in overlay_text
    assert "Samsung" not in overlay_text
    assert "SK hynix" not in overlay_text


@pytest.mark.asyncio
async def test_demo_page_runs_lotte_agent_with_mnemome_memory(monkeypatch) -> None:
    seen_messages: list[str] = []
    search_calls: list[dict] = []

    class FakeLiveOpenAIModel(AsyncModelBase):
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, messages, *args, **kwargs):
            del args, kwargs
            message_text = str(messages)
            seen_messages.append(message_text)
            if "Now, there is the actual planning task:" in message_text:
                if "이 규칙을 선호로 저장해줘" in message_text:
                    text = (
                        '("[remember_preference] 엔비디아 뉴스 선호 저장하기", '
                        '"[final_answer] 저장 결과 안내하기")'
                    )
                elif "내가 선호하는 답변 방식은?" in message_text:
                    text = '("[final_answer] 저장된 선호를 읽어 답변하기",)'
                elif "내가 지금까지 뉴스 물어본 기업들은?" in message_text:
                    text = '("[final_answer] 저장된 뉴스 기업 기억으로 답변하기",)'
                elif (
                    "엔비디아 뉴스" in message_text
                    and "SK하이닉스와 삼성전자 관련 뉴스도 함께 포함한다" in message_text
                ):
                    text = (
                        '({"[search_retrieve] 엔비디아 뉴스 조회하기", '
                        '"[search_retrieve] 삼성전자 뉴스 조회하기", '
                        '"[search_retrieve] SK하이닉스 뉴스 조회하기"}, '
                        '"[final_answer] 기업별 최신 뉴스 요약하기")'
                    )
                else:
                    text = '("[final_answer] 저장된 장기 기억으로 한국어 답변하기",)'
                return ModelOutput(model="gpt-live-test", text=text, finish_reason="stop")
            if "Tool: remember_preference" in message_text:
                text = json.dumps(
                    [
                        {
                            "preference": (
                                "엔비디아 뉴스를 요청하면 SK하이닉스와 삼성전자 관련 뉴스도 "
                                "함께 포함한다."
                            )
                        }
                    ],
                    ensure_ascii=False,
                )
                return ModelOutput(model="gpt-live-test", text=text, finish_reason="stop")
            if "Tool: search_retrieve" in message_text:
                if "SK하이닉스 뉴스 조회하기" in message_text:
                    query = "SK하이닉스 뉴스"
                elif "삼성전자 뉴스 조회하기" in message_text:
                    query = "삼성전자 뉴스"
                else:
                    query = "엔비디아 뉴스"
                text = json.dumps(
                    [{"query": query, "domain": "news", "limit": 5}],
                    ensure_ascii=False,
                )
                return ModelOutput(model="gpt-live-test", text=text, finish_reason="stop")
            self.calls += 1
            if "저장된 선호를 읽어 답변하기" in message_text:
                text = (
                    "선호하는 답변 방식은 핵심부터 한국어로 간결하게 설명하고, "
                    "엔비디아 뉴스 요청 시 삼성전자와 SK하이닉스 뉴스도 함께 보는 것입니다."
                )
            elif "기업별 최신 뉴스 요약하기" in message_text:
                text = (
                    "엔비디아 주요 뉴스와 관련 기업(삼성전자, SK하이닉스) 뉴스도 함께 "
                    "준비해 제공합니다."
                )
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
        search_calls.append(kwargs)
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
            assert "20260721-streaming-markdown" in page.text
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
            assert 'matchMedia("(max-width: 56.25rem)")' in script.text
            assert 'appendMessage("assistant", "")' in script.text
            assert 'setAttribute("aria-label", "실행 계획 생성 중")' in script.text
            assert 'status === "running" ? "진행 중" : "시작 대기 중"' in script.text
            assert "pendingStreamingMarkdownStart" in script.text
            assert "renderAnswerMarkdown" in script.text
            assert "renderStreamingAnswerMarkdown" in script.text
            assert 'document.createElement("strong")' in script.text
            assert "requestAnimationFrame" in script.text
            assert 'setAttribute("aria-busy", "true")' in script.text
            stylesheet = await client.get("/static/app.css")
            assert stylesheet.status_code == 200
            assert "px" not in stylesheet.text
            assert "100dvh" in stylesheet.text
            assert ".memory-panel:not(.is-collapsed) .panel-heading" in stylesheet.text

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
            assert any(
                "Mnemome unified memory-aware planning policy" in item
                for item in seen_messages
            )
            assert any('"recalled_memories"' in item for item in seen_messages)
            assert any('"cultural_principles"' in item for item in seen_messages)
            assert any("독도 관련 질문" in messages for messages in seen_messages)
            assert any("답변은 핵심부터 한국어로" in messages for messages in seen_messages)

            preference_text = (
                "이 규칙을 선호로 저장해줘. 앞으로 엔비디아 뉴스를 요청하면 삼성전자와 "
                "SK하이닉스 뉴스도 함께 포함해줘. 지금은 뉴스를 조회하지 마."
            )
            normalized_preference = (
                "엔비디아 뉴스를 요청하면 SK하이닉스와 삼성전자 관련 뉴스도 함께 포함한다."
            )
            preference_message_start = len(seen_messages)
            preference_response = await client.post(
                "/demo/api/chat", json={"query": preference_text}
            )
            assert preference_response.status_code == 200, preference_response.text
            assert preference_response.json()["preference_captured"] is True
            assert all(
                "지금" not in entry["content"]
                for entry in preference_response.json()["recalled"]
                if "엔비디아" in entry["content"]
            )
            preference_messages = seen_messages[preference_message_start:]
            assert any("Tool: remember_preference" in item for item in preference_messages)
            assert any(
                '"preference":{"type":"string"' in item
                and '"required":["preference"]' in item
                for item in preference_messages
            )
            assert any(preference_text in item for item in preference_messages)
            assert search_calls == []

            preference_read = await client.post(
                "/demo/api/chat", json={"query": "내가 선호하는 답변 방식은?"}
            )
            assert preference_read.status_code == 200, preference_read.text
            preference_read_payload = preference_read.json()
            assert preference_read_payload["preference_captured"] is False
            assert [
                step["tool"] for step in preference_read_payload["execution_trace"]["steps"]
            ] == ["final_answer"]
            assert "핵심부터 한국어로 간결하게" in preference_read_payload["answer"]
            assert "엔비디아 뉴스 요청 시" in preference_read_payload["answer"]
            assert "저장되었습니다" not in preference_read_payload["answer"]
            assert "실행 중 문제" not in preference_read_payload["answer"]
            assert search_calls == []

            follow_up_message_start = len(seen_messages)
            follow_up = await client.post("/demo/api/chat", json={"query": "엔비디아 뉴스"})
            assert follow_up.status_code == 200, follow_up.text
            follow_up_messages = seen_messages[follow_up_message_start:]
            assert any(normalized_preference in item for item in follow_up_messages)
            search_step_messages = [
                item for item in follow_up_messages if "Tool: search_retrieve" in item
            ]
            assert len(search_step_messages) == 3
            assert all(normalized_preference not in item for item in search_step_messages)
            assert any("one retrieval tool" in item for item in follow_up_messages)
            assert any("Do not plan search A B C" in item for item in follow_up_messages)
            assert {call["query"] for call in search_calls} == {
                "엔비디아 뉴스",
                "삼성전자 뉴스",
                "SK하이닉스 뉴스",
            }
            assert all(call["domain"] == "news" for call in search_calls)
            assert all(call["limit"] == 5 for call in search_calls)
            assert follow_up.json()["answer"].startswith(
                "엔비디아 주요 뉴스와 관련 기업(삼성전자, SK하이닉스)"
            )

            memory_message_start = len(seen_messages)
            memory_query = await client.post(
                "/demo/api/chat", json={"query": "내가 지금까지 뉴스 물어본 기업들은?"}
            )
            assert memory_query.status_code == 200, memory_query.text
            memory_messages = seen_messages[memory_message_start:]
            assert any(normalized_preference in item for item in memory_messages)
            assert len(search_calls) == 3

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
            stored_preference = next(
                item for item in preferences if item["content"] == normalized_preference
            )
            assert stored_preference["metadata"]["original_instruction"] == preference_text
            assert stored_preference["metadata"]["prompt_strategy"] == "unified"

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
