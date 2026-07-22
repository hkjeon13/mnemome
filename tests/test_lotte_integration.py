from __future__ import annotations

import json

import httpx
import pytest
from lotte_agent.agents.agent_types import AgentTask
from lotte_agent.agents.toolcall.history_ops import extract_task_text
from lotte_agent.agents.toolcall.prompt_builders import build_plan_prompt_messages
from lotte_agent.memory import MemoryEntry, MemoryEntryKind
from lotte_agent.models.base import AsyncModelBase
from lotte_agent.models.model_types import ModelOutput, TextInput
from lotte_agent.tools import ToolSpec

from mnemome import Mnemome
from mnemome.adapters import InMemoryStores
from mnemome.integrations.lotte_agent import MnemomeLongTermMemory
from mnemome.service.app import create_app
from mnemome.service.demo import _preference_candidate
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


@pytest.mark.asyncio
async def test_lotte_memory_groups_live_chat_turns_by_conversation_session() -> None:
    mnemome = Mnemome.in_memory()
    await mnemome.initialize()

    first = MnemomeLongTermMemory(
        mnemome.application,
        "tenant-a",
        conversation_session_id="session-a",
        conversation_query="첫 질문",
    )
    await first.store(
        MemoryEntry(
            id="run-1",
            kind=MemoryEntryKind.CONVERSATION,
            content="첫 답변",
            metadata={"run_id": "run-1"},
        )
    )
    second = MnemomeLongTermMemory(
        mnemome.application,
        "tenant-a",
        conversation_session_id="session-a",
        conversation_query="후속 질문",
    )
    assert await second.conversation_turns() == [
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "첫 답변"},
    ]
    await second.store(
        MemoryEntry(
            id="run-2",
            kind=MemoryEntryKind.CONVERSATION,
            content="후속 답변",
            metadata={"run_id": "run-2"},
        )
    )

    conversations = await second.list_all(kind=MemoryEntryKind.CONVERSATION)
    assert len(conversations) == 1
    assert conversations[0].id == "conversation:session-a"
    assert conversations[0].metadata["turn_count"] == 4
    assert conversations[0].metadata["conversation_turns"][-2:] == [
        {"role": "user", "content": "후속 질문"},
        {"role": "assistant", "content": "후속 답변"},
    ]


def test_demo_prompt_layers_policy_onto_lotte_default_yaml() -> None:
    prompt_template = build_demo_prompt_template()
    overlay_text = PROMPT_OVERLAY_PATH.read_text(encoding="utf-8")

    assert "STRICT PLAN GENERATION RULES" in prompt_template["plan"]
    assert "Mnemome unified memory-aware planning policy" in prompt_template["plan"]
    assert "For targets A, B, and C" in prompt_template["plan"]
    assert "equal multi-target summary" in prompt_template["plan"]
    assert "every target retrieval step must use [search_retrieve]" in prompt_template["plan"]
    assert "Never use company_search, company_analysis" in prompt_template["plan"]
    assert "read-only memory question" in prompt_template["plan"]
    assert "planner LLM owns preference applicability decisions" in prompt_template["plan"]
    assert "active instructions" in prompt_template["plan"]
    assert "legacy_unstructured" in prompt_template["plan"]
    assert "A condition about one company" in prompt_template["plan"]
    assert "never merge existing stored preferences" in prompt_template["plan"]
    assert "must never be included" in prompt_template["plan"]
    assert "repeatable class of content, requests, or situations" in prompt_template["plan"]
    assert "presentation rules such as" in prompt_template["plan"]
    assert "does not explicitly request current" in prompt_template["plan"]
    assert "Do not copy prior entities" in prompt_template["plan"]
    assert "complete applicability condition" in prompt_template["plan"]
    assert "Now, there is the actual planning task:" in prompt_template["plan"]
    assert "**Metadata:** {{metadata}}" not in prompt_template["plan"]
    assert "**Memory Context:** {{metadata.memory" in prompt_template["plan"]
    assert "**Plan Prerequisites:** {{prerequisites}}" in prompt_template["plan"]
    assert "History contains the ordered USER/ASSISTANT turns" in prompt_template["plan"]
    assert "Now, there is the actual task:" in prompt_template["step"]
    assert "Mnemome MCP step execution policy" in prompt_template["step"]
    assert "the A step must query A only" in prompt_template["step"]
    assert "current-turn constraints such as do not search now" in prompt_template["step"]
    assert (
        "planner must make the remember_preference Input self-contained" in prompt_template["step"]
    )
    assert "a plan that omits the applicable action has failed" in prompt_template["plan"]
    assert "every semantically applicable candidate" in prompt_template["plan"]
    assert "알겠습니다. 앞으로는 ...하겠습니다." in prompt_template["plan"]
    assert "equally requested targets" in prompt_template["step"]
    assert prompt_template["step"].rindex("Mnemome final response policy") > prompt_template[
        "step"
    ].index("Input: {{input}}")
    assert "preference candidates are intentionally unavailable" in prompt_template["step"]
    assert "appended once to Input as [metadata]" in prompt_template["step"]
    assert "Final Answer Instruction" in prompt_template["final_instruction"]
    assert "Never tell the user that a preference" in prompt_template["final_instruction"]
    assert "Mnemome final response policy" in prompt_template["final_instruction"]
    assert "preference-added targets as related" in prompt_template["final_instruction"]
    assert "Do not say they were newly stored" in prompt_template["final_instruction"]
    assert "Mnemome unified memory-aware planning policy" in prompt_template["replan"]
    assert "Mnemome unified memory-aware planning policy" in prompt_template["plan_repair"]
    assert "NVIDIA" not in overlay_text
    assert "Samsung" not in overlay_text
    assert "SK hynix" not in overlay_text
    assert "엔비디아" not in overlay_text
    assert "삼성전자" not in overlay_text
    assert "SK하이닉스" not in overlay_text


def test_demo_plan_places_conversation_turns_in_history_not_metadata() -> None:
    prompt_template = build_demo_prompt_template()
    task = AgentTask(
        input="",
        inputs=[
            TextInput(
                text="Q2",
                history=[TextInput(text="USER: Q1"), TextInput(text="ASSISTANT: A1")],
            )
        ],
    )
    assert extract_task_text(task) == "Q2"
    _, user_prompt = build_plan_prompt_messages(
        task,
        tools_desc="remember_preference(...)",
        template=prompt_template["plan"],
        metadata={
            "memory": {
                "long_term_evidence": [{"id": "fact-1", "kind": "fact", "content": "장기 기억"}],
                "cultural": {"artifacts": []},
            },
            "plan_prerequisites": {
                "memory": {
                    "preference_candidates": [],
                }
            },
            "current_date": "2026-07-22",
            "current_datetime": "2026-07-22T12:00:00+09:00",
            "timezone": "Asia/Seoul",
        },
        language="ko",
    )

    assert "**History:** - USER: Q1\n- ASSISTANT: A1" in user_prompt
    assert "**Metadata:**" not in user_prompt
    assert "current_user_request" not in user_prompt
    assert "current_conversation" not in user_prompt
    assert "turn_count" not in user_prompt
    assert user_prompt.count("preference_candidates") == 1
    assert user_prompt.count("long_term_evidence") == 1
    assert "장기 기억" in user_prompt
    assert "Q2" in user_prompt


def test_preference_candidates_preserve_structure_for_planner_decisions() -> None:
    structured = MemoryEntry(
        id="nvidia-news",
        kind=MemoryEntryKind.PREFERENCE,
        content="엔비디아 뉴스를 요청할 때: 삼성전자와 SK하이닉스 뉴스도 함께 보여준다",
        metadata={
            "preference_condition": "엔비디아 뉴스를 요청할 때",
            "preference_action": "삼성전자와 SK하이닉스 뉴스도 함께 보여준다",
        },
    )
    legacy = MemoryEntry(
        id="legacy",
        kind=MemoryEntryKind.PREFERENCE,
        content="답변은 한국어로 간결하게",
    )

    assert _preference_candidate(structured) == {
        "id": "nvidia-news",
        "condition": "엔비디아 뉴스를 요청할 때",
        "action": "삼성전자와 SK하이닉스 뉴스도 함께 보여준다",
        "raw_rule": structured.content,
        "structure_status": "structured",
    }
    assert _preference_candidate(legacy)["structure_status"] == "legacy_unstructured"
    assert _preference_candidate(legacy)["condition"] is None


@pytest.mark.asyncio
async def test_demo_page_runs_lotte_agent_with_mnemome_memory(monkeypatch) -> None:
    seen_messages: list[str] = []
    search_calls: list[dict] = []
    model_init_calls: list[dict] = []
    agent_init_calls: list[dict] = []
    run_stream_calls: list[tuple[tuple, dict]] = []

    class FakeLiveOpenAIModel(AsyncModelBase):
        def __init__(self) -> None:
            self.calls = 0

        async def generate(self, messages, *args, **kwargs):
            del args, kwargs
            message_text = str(messages)
            seen_messages.append(message_text)
            if "Now, there is the actual planning task:" in message_text:
                if "앞으로 뉴스 나타낼때는 표로 나타내줘" in message_text:
                    text = (
                        '("[remember_preference] 조건 뉴스를 표시할 때 동작 표 형식으로 '
                        '표시하도록 저장하기", '
                        '"[final_answer] 알겠다고 답하고 앞으로 뉴스를 표 형식으로 제공하겠다고 안내하기")'
                    )
                elif "뉴스 기사 나타낼 때는 항목 형식으로 나타내줘" in message_text:
                    text = (
                        '("[remember_preference] 조건 뉴스 기사를 표시할 때 동작 각 기사를 '
                        '항목 형식으로 표시하도록 저장하기", '
                        '"[final_answer] 저장 결과 안내하기")'
                    )
                elif "이 규칙을 선호로 저장해줘" in message_text:
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
                if "조건 뉴스를 표시할 때" in message_text:
                    params = {
                        "condition": "뉴스를 표시할 때",
                        "action": "표 형식으로 표시한다.",
                    }
                elif "조건 뉴스 기사를 표시할 때" in message_text:
                    params = {
                        "condition": "뉴스 기사를 표시할 때",
                        "action": "각 기사를 항목 형식으로 표시한다.",
                    }
                else:
                    params = {
                        "condition": "엔비디아 뉴스를 요청할 때",
                        "action": "SK하이닉스와 삼성전자 관련 뉴스도 함께 포함한다.",
                    }
                text = json.dumps([params], ensure_ascii=False)
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
            elif "앞으로 뉴스를 표 형식으로 제공하겠다고 안내하기" in message_text:
                text = "네, 알겠습니다. 다음부터 뉴스는 표 형식으로 보여드리겠습니다."
            else:
                text = "저장된 선호에 따라 한국어로 간결하게 답변합니다."
            return ModelOutput(model="gpt-live-test", text=text, finish_reason="stop")

        def generate_stream(self, messages, *args, **kwargs):
            async def iterator():
                yield await self.generate(messages, *args, **kwargs)

            return iterator()

    import lotte_agent.models
    import lotte_agent.tools

    live_agent_class = lotte_agent.AsyncToolCallingAgent
    original_run_stream = live_agent_class.run_stream

    async def observed_run_stream(self, *args, **kwargs):
        run_stream_calls.append((args, kwargs))
        async for chunk in original_run_stream(self, *args, **kwargs):
            yield chunk

    monkeypatch.setattr(live_agent_class, "run_stream", observed_run_stream)
    monkeypatch.setattr(
        lotte_agent,
        "AsyncToolCallingAgent",
        lambda **kwargs: (
            agent_init_calls.append(kwargs),
            live_agent_class(**kwargs),
        )[1],
    )

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
        lambda **kwargs: (model_init_calls.append(kwargs), FakeLiveOpenAIModel())[1],
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
            assert "20260723-natural-pref-ack-1" in page.text
            assert "LOTTE AGENT TRACE" in page.text
            assert "메모리 적용 지점" in page.text
            assert "lucide-refresh-cw" in page.text
            assert 'id="open-new-conversation"' in page.text
            assert 'id="new-conversation-dialog"' in page.text
            assert 'id="clear-memories-dialog"' in page.text
            assert 'data-icon="lucide-trash-2"' in page.text
            assert "사용자 기억을 삭제할까요?" in page.text
            assert "저장된 장기 메모리는 그대로 유지" in page.text
            script = await client.get("/static/app.js")
            assert script.status_code == 200
            assert 'addEventListener("compositionstart"' in script.text
            assert 'addEventListener("compositionend"' in script.text
            assert "event.isComposing" in script.text
            assert 'event === "progress"' in script.text
            assert 'matchMedia("(max-width: 56.25rem)")' in script.text
            assert "if (compactLayoutQuery.matches)" in script.text
            assert "startNewConversation({ focusInput: false })" in script.text
            assert "setMemoryPanelCollapsed(true)" in script.text
            assert "else elements.chatInput.blur()" in script.text
            assert "20260723-natural-pref-ack-1" in page.text
            assert "conversation_id: state.conversationId" in script.text
            assert "memory.conversation?.turns" in script.text
            assert 'appendMessage("assistant", "")' in script.text
            assert 'setAttribute("aria-label", "실행 계획 생성 중")' in script.text
            assert 'status === "running" ? "진행 중" : "시작 대기 중"' in script.text
            assert "pendingStreamingMarkdownStart" in script.text
            assert "renderAnswerMarkdown" in script.text
            assert "renderStreamingAnswerMarkdown" in script.text
            assert "선호 지시를 감지해 장기 기억에 저장했습니다" not in script.text
            assert "markdownTableAt" in script.text
            assert 'document.createElement("table")' in script.text
            assert 'document.createElement("thead")' in script.text
            assert 'document.createElement("strong")' in script.text
            assert "requestAnimationFrame" in script.text
            assert 'setAttribute("aria-busy", "true")' in script.text
            stylesheet = await client.get("/static/app.css")
            assert stylesheet.status_code == 200
            assert "100dvh" in stylesheet.text
            assert ".markdown-table-wrap table" in stylesheet.text
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
                "/demo/api/chat/stream",
                json={
                    "query": "한국어 스트림 응답을 보여줘",
                    "conversation_id": "history-session",
                },
            )
            assert streamed.status_code == 200
            assert model_init_calls
            assert agent_init_calls
            assert all(call["num_steps"] == 6 for call in agent_init_calls)
            assert all("generation_parameters" not in call for call in model_init_calls)
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
                "/demo/api/chat",
                json={
                    "query": "한국어로 간결하게 답변해줘",
                    "conversation_id": "history-session",
                },
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
            task_args, stream_kwargs = run_stream_calls[-1]
            assert isinstance(task_args[0], AgentTask)
            assert task_args[0].input == ""
            assert extract_task_text(task_args[0]) == "한국어로 간결하게 답변해줘"
            assert task_args[0].inputs[0].text == "한국어로 간결하게 답변해줘"
            assert [item.text for item in task_args[0].inputs[0].history] == [
                "USER: 한국어 스트림 응답을 보여줘",
                "ASSISTANT: 저장된 선호에 따라 한국어로 간결하게 답변합니다.",
            ]
            assert stream_kwargs["language"] == "ko"
            assert stream_kwargs["tracking_workflow"] is False
            assert stream_kwargs["return_trimmed_stream"] is False
            assert "timeout" not in stream_kwargs
            assert "valid_tools" not in stream_kwargs
            assert set(stream_kwargs["metadata"]) == {"memory", "plan_prerequisites"}
            assert "long_term_evidence" in stream_kwargs["metadata"]["memory"]
            assert "cultural" in stream_kwargs["metadata"]["memory"]
            assert set(stream_kwargs["metadata"]["plan_prerequisites"]) == {"memory"}
            assert (
                "preference_candidates" in stream_kwargs["metadata"]["plan_prerequisites"]["memory"]
            )
            serialized_metadata = json.dumps(stream_kwargs["metadata"], ensure_ascii=False)
            assert "current_user_request" not in serialized_metadata
            assert "current_conversation" not in serialized_metadata
            assert "turn_count" not in serialized_metadata
            assert "prompt_strategy" not in serialized_metadata
            assert "history-session" not in serialized_metadata
            assert any(
                "Mnemome unified memory-aware planning policy" in item for item in seen_messages
            )
            assert any('"long_term_evidence"' in item for item in seen_messages)
            assert any('"cultural"' in item for item in seen_messages)
            assert any("독도 관련 질문" in messages for messages in seen_messages)
            assert any("답변은 핵심부터 한국어로" in messages for messages in seen_messages)

            preference_text = (
                "이 규칙을 선호로 저장해줘. 앞으로 엔비디아 뉴스를 요청하면 삼성전자와 "
                "SK하이닉스 뉴스도 함께 포함해줘. 지금은 뉴스를 조회하지 마."
            )
            normalized_preference = (
                "엔비디아 뉴스를 요청할 때: SK하이닉스와 삼성전자 관련 뉴스도 함께 포함한다."
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
                "Never represent saving as no_tool" in item
                and "exact [remember_preference] tool tag" in item
                for item in preference_messages
            )
            assert any(
                "Decide preference-write intent in this plan semantically" in item
                and "not from keywords" in item
                and "both establish a conditional behavior" in item
                for item in preference_messages
            )
            assert not any(
                "semantic preference-intent decision stage" in item for item in preference_messages
            )
            assert any(
                "현재 요청에만 한정하지 않은 조건부·반복 행동" in item
                and "저장과 실행을 모두 계획하고" in item
                and "직전 작업을 임의로 다시 실행하지 않습니다" in item
                for item in preference_messages
            )
            assert any(
                '"condition":{"type":"string"' in item
                and '"action":{"type":"string"' in item
                and '"required":["condition","action"]' in item
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
            assert any('"decision_owner": "planner_llm"' in item for item in follow_up_messages)
            assert any(
                '"condition": "엔비디아 뉴스를 요청할 때"' in item
                and '"structure_status": "structured"' in item
                for item in follow_up_messages
            )
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
                item["conversation"]["query"] == "한국어 스트림 응답을 보여줘"
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
            assert stored_preference["metadata"]["preference_condition"] == (
                "엔비디아 뉴스를 요청할 때"
            )
            assert stored_preference["metadata"]["preference_action"] == (
                "SK하이닉스와 삼성전자 관련 뉴스도 함께 포함한다."
            )
            assert stored_preference["metadata"]["applicability_owner"] == "planner_llm"

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

            implicit_preference = await isolated_client.post(
                "/demo/api/chat",
                json={"query": "앞으로 뉴스 나타낼때는 표로 나타내줘."},
            )
            assert implicit_preference.status_code == 200, implicit_preference.text
            assert implicit_preference.json()["preference_captured"] is True
            assert implicit_preference.json()["answer"] == (
                "네, 알겠습니다. 다음부터 뉴스는 표 형식으로 보여드리겠습니다."
            )
            assert [
                step["tool"] for step in implicit_preference.json()["execution_trace"]["steps"]
            ] == ["remember_preference", "final_answer"]
            assert len(search_calls) == 3
            implicit_memories = await isolated_client.get("/demo/api/memories")
            assert any(
                item["kind"] == "preference"
                and item["metadata"].get("preference_condition") == "뉴스를 표시할 때"
                and item["metadata"].get("preference_action") == "표 형식으로 표시한다."
                for item in implicit_memories.json()["items"]
            )
