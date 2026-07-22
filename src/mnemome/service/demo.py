from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from pydantic import BaseModel, Field

from ..contracts import OpenRunRequest, SourceRef
from ..retrieval import recall_backend_label
from .demo_imports import (
    MAX_DEMO_MEMORIES,
    DemoImportPrepareBody,
    DemoImportPreviewBody,
    DemoImportProcessBody,
    DemoImportStudio,
)
from .prompting import build_demo_prompt_template

DEMO_COOKIE = "mnemome_demo_session"
DEMO_TENANT_PREFIX = "demo_"
DEMO_SESSION_LENGTH = 32
STATIC_DIR = Path(__file__).with_name("static")
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"
DEFAULT_MCP_TOOLS = (
    "sandbox_python_execute",
    "search_retrieve",
    "search_detail",
    "company_search",
    "stock_price",
    "market_data",
    "company_analysis",
)

logger = logging.getLogger("mnemome.service.demo")


def _preference_candidate(entry: Any) -> dict[str, Any]:
    metadata = dict(entry.metadata or {})
    condition = metadata.get("preference_condition")
    action = metadata.get("preference_action")
    structured = bool(condition and action)
    return {
        "id": entry.id,
        "condition": str(condition) if condition else None,
        "action": str(action) if action else None,
        "raw_rule": entry.content,
        "structure_status": "structured" if structured else "legacy_unstructured",
    }


class DemoMemoryBody(BaseModel):
    content: str = Field(min_length=1, max_length=2_000)
    kind: str = Field(default="fact", pattern="^(fact|preference|episode)$")
    tags: list[str] = Field(default_factory=list, max_length=10)


class DemoChatBody(BaseModel):
    query: str = Field(min_length=1, max_length=1_000)
    conversation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


class DemoRateLimiter:
    def __init__(self, *, max_sessions: int = 1_000, requests_per_minute: int = 30) -> None:
        self._max_sessions = max_sessions
        self._requests_per_minute = requests_per_minute
        self._requests: OrderedDict[str, deque[float]] = OrderedDict()
        self._lock = asyncio.Lock()

    async def check(self, session_id: str) -> None:
        now = time.monotonic()
        async with self._lock:
            history = self._requests.setdefault(session_id, deque())
            while history and now - history[0] > 60:
                history.popleft()
            if len(history) >= self._requests_per_minute:
                raise HTTPException(status_code=429, detail="잠시 후 다시 시도해 주세요.")
            history.append(now)
            self._requests.move_to_end(session_id)
            while len(self._requests) > self._max_sessions:
                self._requests.popitem(last=False)


def _session(request: Request, response: Response) -> tuple[str, str]:
    session_id = request.cookies.get(DEMO_COOKIE, "")
    if len(session_id) != DEMO_SESSION_LENGTH or not session_id.isalnum():
        session_id = secrets.token_hex(DEMO_SESSION_LENGTH // 2)
        response.set_cookie(
            DEMO_COOKIE,
            session_id,
            max_age=60 * 60 * 24 * 7,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="lax",
        )
    return session_id, f"{DEMO_TENANT_PREFIX}{session_id}"


async def _seed_memories(application: Any, tenant_id: str) -> None:
    if await application.list_facts(tenant_id, limit=1):
        return
    samples = (
        (
            "preference",
            "답변은 핵심부터 한국어로 간결하게 설명해 주세요.",
            ("response-style", "korean"),
        ),
        (
            "fact",
            "Mnemome 데모 프로젝트의 배포 환경은 Docker Compose입니다.",
            ("project", "deployment"),
        ),
        (
            "episode",
            "지난 배포 점검에서 readiness와 재시작 후 메모리 영속성을 확인했습니다.",
            ("deployment", "verification"),
        ),
    )
    for kind, content, tags in samples:
        await application.create_fact(
            tenant_id,
            content,
            sources=(SourceRef("demo_seed", "mnemome_demo_v1"),),
            kind=kind,
            tags=tags,
            metadata={"seeded": True},
        )


async def _seed_cultural_memory(application: Any, tenant_id: str) -> None:
    snapshot, _ = await application.resolve_cultural_snapshot(tenant_id, "default")
    if snapshot is not None:
        return
    samples = (
        {
            "claim": (
                "독도 관련 질문에는 대한민국 정부의 공식 입장과 실효적 지배 사실을 먼저 "
                "명확히 설명하고, 단순히 '분쟁지역'이라고 규정하지 않는다."
            ),
            "conditions": ("독도, 영유권, 한일 관계에 관한 질문",),
            "restrictions": (
                "사용자 질문에 직접 답하지 않고 과거 답변을 그대로 반복하지 않는다.",
                "일본의 주장을 대한민국의 공식 입장과 동등한 확정 사실로 표현하지 않는다.",
            ),
            "recovery": "불확실한 세부 사실은 공식 자료나 최신 출처를 확인해 구분해서 설명한다.",
            "metadata": {"seeded": True, "read_only": True, "label": "독도 응답 원칙"},
        },
        {
            "claim": (
                "과거 대화 기억은 사용자 맥락과 선호를 위한 것이며 최신 뉴스나 현재 사실의 "
                "근거를 대체하지 않는다. 시의성이 필요한 질문은 검색 도구로 새로 확인한다."
            ),
            "conditions": ("뉴스, 최신, 최근, 오늘, 현재 등 시의성 있는 정보 요청",),
            "restrictions": ("저장된 과거 Agent 답변을 현재 사실처럼 재사용하지 않는다.",),
            "recovery": (
                "검색이 불가능하면 확인 한계를 밝히고 기억만으로 최신 사실을 단정하지 않는다."
            ),
            "metadata": {"seeded": True, "read_only": True, "label": "최신성 검증 원칙"},
        },
    )
    for sample in samples:
        await application.create_cultural_artifact(
            tenant_id,
            "default",
            sample["claim"],
            conditions=sample["conditions"],
            restrictions=sample["restrictions"],
            recovery=sample["recovery"],
            evidence_refs=(SourceRef("demo_policy", "mnemome_culture_v1"),),
            metadata=sample["metadata"],
        )
    await application.publish_cultural_snapshot(
        tenant_id, "default", policy_version="mnemome-demo-culture-v1"
    )


def _cultural_payload(snapshot: Any, artifacts: tuple[Any, ...]) -> dict[str, Any]:
    return {
        "snapshot": {
            "id": snapshot.snapshot_id,
            "scope": snapshot.scope,
            "version": snapshot.version,
            "policy_version": snapshot.policy_version,
            "content_digest": snapshot.content_digest,
            "created_at": snapshot.created_at,
            "read_only": True,
        },
        "items": [
            {
                "id": artifact.artifact_id,
                "version": artifact.version,
                "claim": artifact.claim,
                "conditions": list(artifact.conditions),
                "restrictions": list(artifact.restrictions),
                "recovery": artifact.recovery,
                "read_only": True,
            }
            for artifact in artifacts
        ],
    }


def _conversation_query(fact: Any) -> str | None:
    if fact.kind != "conversation":
        return None
    task_text = fact.metadata.get("task_text")
    if not isinstance(task_text, str) or not task_text.strip():
        return None
    marker = "[사용자 질문]\n"
    query = task_text.rsplit(marker, 1)[-1].strip() if marker in task_text else task_text.strip()
    lines = [line.strip() for line in query.splitlines() if line.strip()]
    if lines and all(line == lines[0] for line in lines):
        return lines[0]
    return query


def _memory_payload(fact: Any) -> dict[str, Any]:
    payload = {
        "id": fact.fact_id,
        "kind": fact.kind,
        "content": fact.statement,
        "tags": list(fact.tags),
        "created_at": fact.created_at,
        "source_count": len(fact.sources),
        "metadata": fact.metadata,
        "is_seed": bool(fact.metadata.get("seeded")),
        "is_imported": bool(fact.metadata.get("import_job_id")),
    }
    stored_turns = fact.metadata.get("conversation_turns")
    turns = (
        [
            {
                "role": str(turn.get("role") or ""),
                "content": str(turn.get("content") or ""),
                **({"timestamp": str(turn.get("timestamp"))} if turn.get("timestamp") else {}),
            }
            for turn in stored_turns
            if isinstance(turn, dict)
            and str(turn.get("role") or "") in {"user", "assistant"}
            and str(turn.get("content") or "").strip()
        ]
        if isinstance(stored_turns, list)
        else []
    )
    query = next(
        (turn["content"] for turn in turns if turn["role"] == "user"),
        _conversation_query(fact),
    )
    if query:
        latest_answer = next(
            (turn["content"] for turn in reversed(turns) if turn["role"] == "assistant"),
            fact.statement,
        )
        payload["conversation"] = {
            "query": query,
            "answer": latest_answer,
            "run_id": fact.metadata.get("latest_run_id") or fact.metadata.get("run_id"),
            "session_id": fact.metadata.get("conversation_session_id"),
            "turns": turns
            or [
                {"role": "user", "content": query},
                {"role": "assistant", "content": fact.statement},
            ],
            "turn_count": len(turns) if turns else 2,
            "is_live_session": fact.metadata.get("created_via") == "demo_chat_session",
        }
    return payload


def _is_seed_memory(fact: Any) -> bool:
    return bool(fact.metadata.get("seeded"))


def _is_import_memory(fact: Any) -> bool:
    return bool(fact.metadata.get("import_job_id"))


def _is_clearable_memory(fact: Any) -> bool:
    return not _is_seed_memory(fact) and not _is_import_memory(fact)


def _mcp_settings() -> tuple[str, set[str]]:
    url = os.getenv("MNEMOME_MCP_URL", "").strip()
    configured = os.getenv("MNEMOME_MCP_TOOL_ALLOWLIST", "").strip()
    allowed = {name.strip() for name in configured.split(",") if name.strip()}
    return url, allowed or set(DEFAULT_MCP_TOOLS)


async def _run_lotte_agent(
    application: Any,
    tenant_id: str,
    query: str,
    run_id: str,
    conversation_id: str,
    cultural_artifacts: tuple[Any, ...] = (),
    *,
    stream_delta: Callable[[str], Awaitable[None]] | None = None,
    stream_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> tuple[str, list[Any], float, str, dict[str, Any], bool, dict[str, Any]]:
    try:
        from lotte_agent import AsyncToolCallingAgent
        from lotte_agent.agents.agent_types import AgentTask
        from lotte_agent.memory import MemoryEntry, MemoryEntryKind
        from lotte_agent.models import AsyncOpenAIClient
        from lotte_agent.models.model_types import TextInput
        from lotte_agent.tools import McpToolSpecClient, ToolSpec

        from ..integrations.lotte_agent import MnemomeLongTermMemory
    except ImportError as error:
        raise HTTPException(
            status_code=503,
            detail="Lotte Agent runtime이 설치되지 않았습니다.",
        ) from error

    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="OPENAI_API_KEY가 설정되지 않았습니다.")
    model_name = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
    base_url = os.getenv("OPENAI_BASE_URL", "").strip() or None
    live_model = AsyncOpenAIClient(
        api_key=api_key,
        model=model_name,
        base_url=base_url,
    )

    memory = MnemomeLongTermMemory(
        application,
        tenant_id,
        max_entries=50,
        conversation_session_id=conversation_id,
        conversation_query=query,
    )
    conversation_turns = await memory.conversation_turns()
    captured_preference = False
    preferences = await memory.list_all(kind=MemoryEntryKind.PREFERENCE, limit=10)

    relevant = [
        entry
        for entry in await memory.search(query, top_k=5)
        if entry.id != memory.conversation_entry_id and entry.kind != MemoryEntryKind.PREFERENCE
    ]
    recalled = [*preferences, *relevant]
    cultural_context = {
        "artifacts": [
            {
                "id": artifact.artifact_id,
                "claim": artifact.claim,
                "conditions": list(artifact.conditions),
                "restrictions": list(artifact.restrictions),
                "recovery": artifact.recovery,
            }
            for artifact in cultural_artifacts
        ]
    }

    async def remember_preference(condition: str, action: str) -> dict[str, str]:
        nonlocal captured_preference, preferences, recalled
        normalized_condition = " ".join(condition.split())
        normalized_action = " ".join(action.split())
        if not normalized_condition or not normalized_action:
            raise ValueError("condition and action must not be empty")
        normalized = f"{normalized_condition}: {normalized_action}"
        duplicate = next(
            (
                entry
                for entry in preferences
                if (
                    str((entry.metadata or {}).get("preference_condition", "")).casefold()
                    == normalized_condition.casefold()
                    and str((entry.metadata or {}).get("preference_action", "")).casefold()
                    == normalized_action.casefold()
                )
                or entry.content.casefold() == normalized.casefold()
            ),
            None,
        )
        if duplicate is not None:
            return {
                "status": "already_exists",
                "preference": duplicate.content,
                "condition": normalized_condition,
                "action": normalized_action,
            }
        await memory.store(
            MemoryEntry(
                id=f"{run_id}:preference",
                kind=MemoryEntryKind.PREFERENCE,
                content=normalized,
                metadata={
                    "run_id": run_id,
                    "source_type": "demo_user_instruction",
                    "source_id": run_id,
                    "original_instruction": query,
                    "prompt_strategy": "unified",
                    "preference_condition": normalized_condition,
                    "preference_action": normalized_action,
                    "applicability_owner": "planner_llm",
                },
                tags=["conversation-derived", "instruction", "agent-stored"],
            )
        )
        captured_preference = True
        preferences = await memory.list_all(kind=MemoryEntryKind.PREFERENCE, limit=10)
        recalled = [*preferences, *relevant]
        return {
            "status": "stored",
            "preference": normalized,
            "condition": normalized_condition,
            "action": normalized_action,
        }

    task_history = [
        TextInput(text=f"{turn['role'].upper()}: {turn['content']}")
        for turn in conversation_turns[-20:]
    ]
    agent_task = AgentTask(
        task_id=run_id,
        # Lotte Agent 0.0.11 otherwise mirrors the first TextInput into ``input`` and
        # concatenates both values when it builds the long-term-memory search query.
        input="",
        inputs=[TextInput(text=query, history=task_history or None)],
    )
    agent_metadata = {
        "memory": {
            "long_term_evidence": [
                {
                    "id": entry.id,
                    "kind": entry.kind.value,
                    "content": entry.content,
                    "tags": list(entry.tags),
                }
                for entry in relevant
            ],
            "cultural": cultural_context,
        },
        "plan_prerequisites": {
            "memory": {
                "preference_policy": {
                    "decision_owner": "planner_llm",
                    "decision_priority": "mandatory_first_gate_before_using_history",
                    "instruction": (
                        "Evaluate every candidate condition semantically against the current User "
                        "Request. "
                        "Use its action only when the condition applies. Presence in candidates "
                        "does not mean it applies. For legacy_unstructured candidates, infer "
                        "condition and action from raw_rule before deciding."
                    ),
                    "write_intent": (
                        "A current request that establishes a response behavior for a recurring "
                        "class of future requests is a durable preference write."
                    ),
                    "save_only_plan": (
                        "When that request does not explicitly ask for current execution, use only "
                        "remember_preference followed by a brief, natural commitment about future "
                        "behavior. The final response must not mention saving, storage, memory, "
                        "preferences, registration, tools, or systems. Never copy the preceding "
                        "turn's entities, tools, or output from History."
                    ),
                    "application_contract": (
                        "For each semantically applicable candidate, copy its behavior into the "
                        "relevant self-contained plan action as a mandatory constraint. Execution "
                        "steps cannot see candidates, so merely recalling or mentioning one is not "
                        "application. Ignore non-applicable candidates completely."
                    ),
                },
                "preference_candidates": [_preference_candidate(entry) for entry in preferences],
            }
        },
    }

    memory_tool = ToolSpec(
        name="remember_preference",
        fn=remember_preference,
        description=(
            "사용자가 현재 요청에만 한정하지 않은 조건부·반복 행동이나 응답 선호를 정하면, "
            "'저장' 또는 '기억'이라고 명시하지 않아도 전체 문장의 의미로 지속 선호인지 판단해 "
            "적용 조건과 동작을 분리하여 장기 기억에 저장합니다. 반복 가능한 콘텐츠나 요청 "
            "유형에 형식·구조·말투 같은 응답 방식을 지정하는 평서형 지시도 지속 선호입니다. "
            "현재 실행도 함께 요구하면 저장과 실행을 모두 계획하고, 명시하지 않았다면 직전 "
            "작업을 임의로 다시 실행하지 않습니다. 단순 질문이나 일회성 요청에는 사용하지 마세요."
        ),
        properties={
            "condition": {
                "type": "string",
                "description": (
                    "이 선호를 적용해야 하는 의미적 조건. 대상, 요청 유형, 상황을 포함해 "
                    "자립적으로 작성하고, 무조건 적용하라는 선호에만 '항상'을 사용합니다."
                ),
            },
            "action": {
                "type": "string",
                "description": (
                    "조건이 충족됐을 때 수행할 동작만 자립적으로 작성합니다. 지금, 현재, "
                    "이번 요청에만 적용되는 실행 지시는 제외합니다."
                ),
            },
        },
        required=["condition", "action"],
    )

    started = time.perf_counter()
    execution_trace: dict[str, Any] = {
        "plan": {
            "title": "Direct response",
            "status": "ok",
            "latency_ms": None,
            "step_count": 0,
        },
        "steps": [],
        "llm_calls": 0,
        "total_latency_ms": None,
    }
    mcp_url, allowed_tools = _mcp_settings()
    mcp_status: dict[str, Any] = {
        "status": "not_configured" if not mcp_url else "unavailable",
        "tool_count": 0,
        "tools": [],
    }

    async def execute_agent(agent_tools: list[Any]) -> str:
        async with AsyncToolCallingAgent(
            model=live_model,
            tools=agent_tools,
            name="Mnemome Memory Guide",
            description=(
                "Mnemome 장기 기억을 사용자 맥락으로 활용하고 MCP 도구로 현재 사실을 "
                "조회하는 한국어 Agent. 뉴스와 최신 정보 요청은 search_retrieve를 반드시 "
                "사용하며, 과거 기억을 현재 사실로 대체하지 않는다."
            ),
            prompt_template=build_demo_prompt_template(),
            long_term_memory=memory,
            memory_search_top_k=5,
            memory_store_outputs=True,
            deterministic_trajectory=True,
            max_replans=0,
            num_steps=6,
            debug_verbosity="none",
        ) as agent:
            final_parts: list[str] = []
            async for chunk in agent.run_stream(
                agent_task,
                run_id=run_id,
                metadata=agent_metadata,
                language="ko",
                tracking_workflow=False,
                return_trimmed_stream=False,
            ):
                if chunk.type == "plan":
                    plan_payload = chunk.plan if isinstance(chunk.plan, dict) else {}
                    raw_steps = plan_payload.get("steps", [])
                    safe_steps = [
                        {
                            "index": step.get("index"),
                            "title": str(step.get("text") or "")[:240],
                            "tool": str(step.get("tool") or "")[:80],
                        }
                        for step in raw_steps
                        if isinstance(step, dict) and str(step.get("text") or "").strip()
                    ]
                    if safe_steps:
                        execution_trace["plan"] = {
                            "title": f"Plan ({len(safe_steps)} steps)",
                            "status": "ok",
                            "latency_ms": None,
                            "step_count": len(safe_steps),
                        }
                        execution_trace["steps"] = [
                            {
                                **step,
                                "status": "pending",
                                "latency_ms": None,
                            }
                            for step in safe_steps
                        ]
                        if stream_progress is not None:
                            await stream_progress(
                                {
                                    "kind": "plan",
                                    "replan": bool(plan_payload.get("replan")),
                                    "steps": safe_steps,
                                }
                            )
                elif chunk.type == "step_start" and chunk.title:
                    for position, step in enumerate(execution_trace["steps"], start=1):
                        if chunk.index in {step.get("index"), position}:
                            step["status"] = "running"
                            break
                    if stream_progress is not None:
                        await stream_progress(
                            {
                                "kind": "step_start",
                                "index": chunk.index,
                                "title": str(chunk.title)[:240],
                            }
                        )
                elif chunk.type == "text" and chunk.is_last_chunk:
                    matched_step = False
                    for position, step in enumerate(execution_trace["steps"], start=1):
                        if chunk.index in {step.get("index"), position}:
                            step["status"] = "error" if chunk.finish_reason == "error" else "ok"
                            matched_step = True
                            break
                    if (
                        matched_step
                        and chunk.finish_reason != "error"
                        and stream_progress is not None
                    ):
                        await stream_progress(
                            {
                                "kind": "step_complete",
                                "index": chunk.index,
                            }
                        )
                delta = chunk.delta_text if chunk.type == "text" else ""
                if not delta or not chunk.is_last_step:
                    continue
                final_parts.append(delta)
                if stream_delta is not None:
                    await stream_delta(delta)
            execution_trace["llm_calls"] = 1 + sum(
                step["status"] in {"ok", "error"} for step in execution_trace["steps"]
            )
            return "".join(final_parts)

    if mcp_url:
        connected = False
        try:
            async with McpToolSpecClient(mcp_url) as discovered:
                connected = True
                agent_tools = [tool for tool in discovered if tool.name in allowed_tools]
                mcp_status = {
                    "status": "connected",
                    "tool_count": len(agent_tools),
                    "tools": sorted(tool.name for tool in agent_tools),
                }
                result_text = await execute_agent([memory_tool, *agent_tools])
        except Exception:
            if connected:
                raise
            mcp_status["detail"] = "MCP 도구 서버에 연결하지 못해 메모리 전용으로 실행했습니다."
            result_text = await execute_agent([memory_tool])
    else:
        result_text = await execute_agent([memory_tool])
    elapsed_ms = round((time.perf_counter() - started) * 1_000, 2)
    execution_trace["total_latency_ms"] = elapsed_ms
    return (
        result_text,
        recalled,
        elapsed_ms,
        model_name,
        execution_trace,
        captured_preference,
        mcp_status,
    )


async def _execute_demo_chat(
    application: Any,
    tenant_id: str,
    query: str,
    conversation_id: str | None = None,
    *,
    stream_delta: Callable[[str], Awaitable[None]] | None = None,
    stream_progress: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    await _seed_memories(application, tenant_id)
    await _seed_cultural_memory(application, tenant_id)
    agent = await application.register_agent(
        tenant_id,
        "Mnemome Memory Guide",
        ("memory.read", "memory.write", "lotte-agent.runtime"),
    )
    run, context = await application.open_run(
        OpenRunRequest(
            tenant_id=tenant_id,
            agent_id=agent.agent_id,
            retrieval_text=query,
            query_ref="demo-ui",
        )
    )
    resolved_conversation_id = conversation_id or run.run_id
    try:
        (
            answer,
            recalled,
            elapsed_ms,
            model_name,
            execution_trace,
            preference_captured,
            mcp_status,
        ) = await _run_lotte_agent(
            application,
            tenant_id,
            query,
            run.run_id,
            resolved_conversation_id,
            context.cultural_artifacts,
            stream_delta=stream_delta,
            stream_progress=stream_progress,
        )
        completed = await application.complete_run(
            tenant_id,
            run.run_id,
            {
                "status": "answered",
                "runtime": "lotte-agent",
                "prompt_strategy": "lotte-agent-default+mnemome-unified-v1",
            },
            response_ref=f"demo:{run.run_id}",
        )
    except Exception as error:
        await application.fail_run(
            tenant_id,
            run.run_id,
            {"type": type(error).__name__, "message": "Demo Agent execution failed"},
        )
        raise
    return {
        "answer": answer,
        "run_id": completed.run_id,
        "conversation_id": resolved_conversation_id,
        "conversation_memory_id": f"conversation:{resolved_conversation_id}",
        "runtime": "AsyncToolCallingAgent",
        "model": model_name,
        "elapsed_ms": elapsed_ms,
        "execution_trace": execution_trace,
        "preference_captured": preference_captured,
        "mcp": mcp_status,
        "memory_trace": {
            "long_term": {
                "status": "applied" if recalled else "empty",
                "count": len(recalled),
                "label": "Mnemome 장기 기억",
                "detail": (
                    "BM25와 MeCab + NLTK 토큰화를 사용해 관련 영속 기억을 조회하고 "
                    "Agent 입력에 적용했습니다."
                ),
                "retriever": recall_backend_label(),
                "kinds": sorted({entry.kind.value for entry in recalled}),
            },
            "short_term": {
                "status": "applied",
                "count": 1 + len(execution_trace["steps"]),
                "label": "Lotte Agent 단기 기억",
                "detail": (
                    "현재 질문과 plan/step 실행 문맥에만 유지되며 다음 요청에는 초기화됩니다."
                ),
                "scope": completed.run_id,
            },
            "cultural": {
                "status": "applied" if context.cultural_artifacts else "not_configured",
                "count": len(context.cultural_artifacts),
                "label": "문화적 기억",
                "detail": (
                    "서버가 관리하는 읽기 전용 문화적 스냅샷을 Agent 실행에 고정했습니다."
                    if context.cultural_artifacts
                    else "이 scope에 게시된 문화적 스냅샷이 없습니다."
                ),
                "snapshot_id": completed.cultural_snapshot_id,
                "scope": "default",
                "artifact_ids": [artifact.artifact_id for artifact in context.cultural_artifacts],
            },
        },
        "recalled": [
            {
                "id": entry.id,
                "kind": entry.kind.value,
                "content": entry.content,
                "tags": entry.tags,
            }
            for entry in recalled
        ],
    }


def _sse_event(event: str, payload: dict[str, Any]) -> str:
    data = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event}\ndata: {data}\n\n"


def build_demo_router() -> APIRouter:
    router = APIRouter(include_in_schema=False)
    limiter = DemoRateLimiter()
    chat_limiter = DemoRateLimiter(requests_per_minute=6)
    global_chat_limiter = DemoRateLimiter(max_sessions=1, requests_per_minute=30)
    import_job_limiter = DemoRateLimiter(requests_per_minute=180)
    import_studio = DemoImportStudio()

    @router.get("/")
    async def demo_root() -> RedirectResponse:
        return RedirectResponse(url="/playground", status_code=307)

    @router.get("/playground")
    async def demo_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @router.get("/documents")
    async def documents_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "documents.html", media_type="text/html")

    @router.get("/demo/api/status")
    async def demo_status(request: Request, response: Response) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        application = request.app.state.application
        await _seed_memories(application, tenant_id)
        await _seed_cultural_memory(application, tenant_id)
        memories = await application.list_facts(tenant_id, limit=MAX_DEMO_MEMORIES)
        try:
            import lotte_agent
            from lotte_agent.models import AsyncOpenAIClient  # noqa: F401

            runtime = f"lotte-agent {lotte_agent.__version__}"
            model = os.getenv("OPENAI_MODEL", DEFAULT_OPENAI_MODEL).strip() or DEFAULT_OPENAI_MODEL
            runtime_available = bool(os.getenv("OPENAI_API_KEY", "").strip())
        except ImportError:
            runtime = "lotte-agent unavailable"
            model = None
            runtime_available = False
        return {
            "status": "ready" if runtime_available else "degraded",
            "runtime": runtime,
            "runtime_available": runtime_available,
            "model": model,
            "storage": "mnemome-sqlite",
            "memory_count": len(memories),
            "mcp_configured": bool(_mcp_settings()[0]),
            "cultural_memory_configured": True,
        }

    @router.get("/demo/api/cultural-snapshot")
    async def cultural_snapshot(request: Request, response: Response) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        application = request.app.state.application
        await _seed_cultural_memory(application, tenant_id)
        snapshot, artifacts = await application.resolve_cultural_snapshot(tenant_id, "default")
        if snapshot is None:
            raise HTTPException(status_code=503, detail="문화적 메모리 스냅샷이 없습니다.")
        return _cultural_payload(snapshot, artifacts)

    @router.get("/demo/api/memories")
    async def list_memories(
        request: Request,
        response: Response,
        kind: str | None = None,
    ) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        application = request.app.state.application
        await _seed_memories(application, tenant_id)
        memories = await application.list_facts(tenant_id, kind=kind, limit=MAX_DEMO_MEMORIES)
        clearable_count = sum(_is_clearable_memory(memory) for memory in memories)
        return {
            "items": [_memory_payload(memory) for memory in memories],
            "seeded_count": sum(_is_seed_memory(memory) for memory in memories),
            "imported_count": sum(_is_import_memory(memory) for memory in memories),
            "clearable_count": clearable_count,
        }

    @router.post("/demo/api/memories", status_code=201)
    async def create_memory(
        body: DemoMemoryBody,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        application = request.app.state.application
        memories = await application.list_facts(tenant_id, limit=MAX_DEMO_MEMORIES)
        if len(memories) >= MAX_DEMO_MEMORIES:
            raise HTTPException(
                status_code=409,
                detail=f"데모 세션은 기억을 {MAX_DEMO_MEMORIES}개까지 저장합니다.",
            )
        memory = await application.create_fact(
            tenant_id,
            body.content,
            sources=(SourceRef("demo_user", session_id),),
            kind=body.kind,
            tags=tuple(body.tags),
            metadata={"created_via": "demo_ui"},
        )
        return _memory_payload(memory)

    @router.delete("/demo/api/memories/{memory_id}")
    async def delete_memory(
        memory_id: str,
        request: Request,
        response: Response,
    ) -> dict[str, bool]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        application = request.app.state.application
        memories = await application.list_facts(tenant_id, limit=MAX_DEMO_MEMORIES)
        target = next((memory for memory in memories if memory.fact_id == memory_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="기억을 찾을 수 없습니다.")
        if _is_seed_memory(target):
            raise HTTPException(status_code=409, detail="기본 샘플 기억은 유지됩니다.")
        if _is_import_memory(target):
            raise HTTPException(
                status_code=409,
                detail="가져온 기억은 Processing 목록의 작업 메뉴에서 함께 삭제해 주세요.",
            )
        await application.suppress_fact(tenant_id, memory_id)
        return {"deleted": True}

    @router.delete("/demo/api/memories")
    async def clear_memories(request: Request, response: Response) -> dict[str, int]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        application = request.app.state.application
        memories = await application.list_facts(tenant_id, limit=MAX_DEMO_MEMORIES)
        clearable = [memory for memory in memories if _is_clearable_memory(memory)]
        for memory in clearable:
            await application.suppress_fact(tenant_id, memory.fact_id)
        return {"cleared": len(clearable), "preserved": len(memories) - len(clearable)}

    @router.post("/demo/api/imports/prepare")
    async def prepare_import(
        body: DemoImportPrepareBody,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        return await import_studio.prepare(tenant_id, body)

    @router.post("/demo/api/imports/{preparation_id}/preview")
    async def preview_import(
        preparation_id: str,
        body: DemoImportPreviewBody,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        return await import_studio.preview(tenant_id, preparation_id, body)

    @router.post("/demo/api/imports/{preparation_id}/process", status_code=202)
    async def process_import(
        preparation_id: str,
        body: DemoImportProcessBody,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        return await import_studio.process(
            tenant_id,
            preparation_id,
            body,
            request.app.state.application,
        )

    @router.get("/demo/api/imports/jobs/{job_id}")
    async def import_job_status(
        job_id: str,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await import_job_limiter.check(session_id)
        return import_studio.job_status(tenant_id, job_id)

    @router.get("/demo/api/imports/jobs")
    async def import_jobs(request: Request, response: Response) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await import_job_limiter.check(session_id)
        return await import_studio.list_jobs(tenant_id, request.app.state.application)

    @router.post("/demo/api/imports/jobs/{job_id}/pause")
    async def pause_import_job(
        job_id: str,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await import_job_limiter.check(session_id)
        return await import_studio.pause_job(tenant_id, job_id)

    @router.post("/demo/api/imports/jobs/{job_id}/resume")
    async def resume_import_job(
        job_id: str,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await import_job_limiter.check(session_id)
        return await import_studio.resume_job(tenant_id, job_id)

    @router.delete("/demo/api/imports/jobs/{job_id}")
    async def delete_import_job(
        job_id: str,
        request: Request,
        response: Response,
    ) -> dict[str, int | bool]:
        session_id, tenant_id = _session(request, response)
        await import_job_limiter.check(session_id)
        return await import_studio.delete_job(
            tenant_id,
            job_id,
            request.app.state.application,
        )

    @router.post("/demo/api/chat")
    async def chat(
        body: DemoChatBody,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        await chat_limiter.check(session_id)
        await global_chat_limiter.check("global")
        application = request.app.state.application
        return await _execute_demo_chat(
            application,
            tenant_id,
            body.query,
            conversation_id=body.conversation_id,
        )

    @router.post("/demo/api/chat/stream")
    async def chat_stream(body: DemoChatBody, request: Request) -> StreamingResponse:
        session_id = ""
        tenant_id = ""
        queue: asyncio.Queue[tuple[str, dict[str, Any]] | None] = asyncio.Queue()

        async def emit_delta(delta: str) -> None:
            await queue.put(("delta", {"delta": delta}))

        async def emit_progress(progress: dict[str, Any]) -> None:
            await queue.put(("progress", progress))

        async def produce() -> None:
            try:
                result = await _execute_demo_chat(
                    request.app.state.application,
                    tenant_id,
                    body.query,
                    conversation_id=body.conversation_id,
                    stream_delta=emit_delta,
                    stream_progress=emit_progress,
                )
                await queue.put(("complete", result))
            except asyncio.CancelledError:
                raise
            except HTTPException as error:
                await queue.put(("error", {"message": str(error.detail)}))
            except Exception:
                logger.exception("Demo streaming chat failed")
                await queue.put(("error", {"message": "응답 스트림을 완료하지 못했습니다."}))
            finally:
                await queue.put(None)

        async def event_stream():
            producer = asyncio.create_task(produce())
            yield _sse_event("ready", {"stream": True})
            try:
                while True:
                    try:
                        item = await asyncio.wait_for(queue.get(), timeout=10)
                    except TimeoutError:
                        yield ": keep-alive\n\n"
                        continue
                    if item is None:
                        break
                    event, payload = item
                    yield _sse_event(event, payload)
            finally:
                if not producer.done():
                    producer.cancel()
                await asyncio.gather(producer, return_exceptions=True)

        streaming_response = StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "X-Accel-Buffering": "no",
            },
        )
        session_id, tenant_id = _session(request, streaming_response)
        await limiter.check(session_id)
        await chat_limiter.check(session_id)
        await global_chat_limiter.check("global")
        return streaming_response

    return router
