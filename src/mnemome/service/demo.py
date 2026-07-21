from __future__ import annotations

import asyncio
import os
import secrets
import time
from collections import OrderedDict, deque
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from ..contracts import OpenRunRequest, SourceRef
from ..retrieval import recall_backend_label

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


class DemoMemoryBody(BaseModel):
    content: str = Field(min_length=1, max_length=2_000)
    kind: str = Field(default="fact", pattern="^(fact|preference|episode)$")
    tags: list[str] = Field(default_factory=list, max_length=10)


class DemoChatBody(BaseModel):
    query: str = Field(min_length=1, max_length=1_000)


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


def _memory_payload(fact: Any) -> dict[str, Any]:
    return {
        "id": fact.fact_id,
        "kind": fact.kind,
        "content": fact.statement,
        "tags": list(fact.tags),
        "created_at": fact.created_at,
        "source_count": len(fact.sources),
        "metadata": fact.metadata,
        "is_seed": bool(fact.metadata.get("seeded")),
    }


def _is_seed_memory(fact: Any) -> bool:
    return bool(fact.metadata.get("seeded"))


def _workflow_trace(payload: dict[str, Any] | None) -> dict[str, Any]:
    nodes = payload.get("nodes", []) if isinstance(payload, dict) else []
    safe_nodes = [node for node in nodes if isinstance(node, dict)]
    plan_node = next((node for node in safe_nodes if node.get("kind") == "plan"), None)
    run_node = next((node for node in safe_nodes if node.get("kind") == "run"), None)
    step_nodes = [node for node in safe_nodes if node.get("kind") == "step"]
    indexed_nodes = {
        node.get("metadata", {}).get("step_index"): node
        for node in step_nodes
        if isinstance(node.get("metadata"), dict)
    }
    raw_steps = []
    if plan_node and isinstance(plan_node.get("metadata"), dict):
        candidate_steps = plan_node["metadata"].get("steps", [])
        if isinstance(candidate_steps, list):
            raw_steps = candidate_steps

    steps: list[dict[str, Any]] = []
    for position, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            continue
        raw_index = raw_step.get("index")
        node = indexed_nodes.get(raw_index) or indexed_nodes.get(position - 1) or {}
        title = str(raw_step.get("text") or node.get("label") or f"Step {position}")
        steps.append(
            {
                "index": position,
                "title": title[:240],
                "tool": str(raw_step.get("tool") or "final_answer")[:80],
                "status": str(node.get("status") or "ok"),
                "latency_ms": node.get("latency_ms"),
            }
        )
    if not steps:
        for position, node in enumerate(step_nodes, start=1):
            steps.append(
                {
                    "index": position,
                    "title": str(node.get("label") or f"Step {position}")[:240],
                    "tool": str(node.get("metadata", {}).get("tool") or "final_answer")[:80],
                    "status": str(node.get("status") or "ok"),
                    "latency_ms": node.get("latency_ms"),
                }
            )

    llm_calls = 0
    for node in safe_nodes:
        metadata = node.get("metadata")
        if isinstance(metadata, dict) and isinstance(metadata.get("llm_events"), list):
            llm_calls += len(metadata["llm_events"])
    return {
        "plan": {
            "title": str((plan_node or {}).get("label") or "Direct response")[:180],
            "status": str((plan_node or {}).get("status") or "ok"),
            "latency_ms": (plan_node or {}).get("latency_ms"),
            "step_count": len(steps),
        },
        "steps": steps,
        "llm_calls": llm_calls,
        "total_latency_ms": (run_node or {}).get("latency_ms"),
    }


def _looks_like_preference_instruction(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    persistence_markers = ("앞으로", "항상", "매번", "이후에는", "기억해", "선호")
    instruction_markers = ("해줘", "해주세요", "표기", "표시", "답변", "말해", "작성")
    return any(marker in normalized for marker in persistence_markers) and any(
        marker in normalized for marker in instruction_markers
    )


def _needs_fresh_search(text: str) -> bool:
    normalized = " ".join(text.casefold().split())
    freshness_markers = (
        "뉴스",
        "news",
        "최신",
        "최근",
        "오늘",
        "현재",
        "실시간",
        "검색해",
        "찾아줘",
    )
    return any(marker in normalized for marker in freshness_markers)


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
) -> tuple[str, list[Any], float, str, dict[str, Any], bool, dict[str, Any]]:
    try:
        from lotte_agent import AsyncToolCallingAgent
        from lotte_agent.memory import MemoryEntry, MemoryEntryKind
        from lotte_agent.models import AsyncOpenAIClient
        from lotte_agent.tools import McpToolSpecClient

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

    memory = MnemomeLongTermMemory(application, tenant_id, max_entries=50)
    captured_preference = False
    preferences = await memory.list_all(kind=MemoryEntryKind.PREFERENCE, limit=10)
    if _looks_like_preference_instruction(query):
        duplicate = next(
            (entry for entry in preferences if entry.content.casefold() == query.casefold()),
            None,
        )
        if duplicate is None:
            await memory.store(
                MemoryEntry(
                    id=f"{run_id}:preference",
                    kind=MemoryEntryKind.PREFERENCE,
                    content=query,
                    metadata={
                        "run_id": run_id,
                        "source_type": "demo_user_instruction",
                        "source_id": run_id,
                    },
                    tags=["conversation-derived", "instruction"],
                )
            )
            captured_preference = True
            preferences = await memory.list_all(kind=MemoryEntryKind.PREFERENCE, limit=10)

    relevant = await memory.search(query, top_k=5)
    recalled = list(preferences)
    recalled.extend(entry for entry in relevant if entry.id not in {item.id for item in recalled})
    recalled = recalled[:8]
    if recalled:
        memory_context = "\n".join(
            f"- [{entry.kind.value}] {entry.content}" for entry in recalled
        )
    else:
        memory_context = "- 관련 장기 기억 없음"
    needs_fresh_search = _needs_fresh_search(query)
    search_instruction = (
        "이 질문은 최신 정보 요청입니다. 반드시 search_retrieve를 domain='news', "
        "limit=15로 실행한 뒤 그 결과를 답변의 사실 근거로 사용하세요. "
        "query에는 사용자 원문의 뉴스/news 표현을 보존하고 기업명만으로 축약하지 마세요. "
        "company_search는 기업 식별 도구일 뿐 뉴스 검색을 대체할 수 없습니다. "
        "검색 결과 중 최신성과 중요도를 기준으로 중복을 제거한 5건 이내만 요약하고, "
        "각 항목에 확인 가능한 날짜와 출처 링크를 포함하세요. 검색 결과가 오래됐다면 "
        "현재 뉴스라고 단정하지 말고 확인된 최신 날짜를 명시하세요. "
        "장기 기억은 사용자 맥락과 선호에만 사용하세요.\n\n"
        if needs_fresh_search
        else ""
    )
    agent_task = (
        "Mnemome 장기 기억은 사용자의 선호와 과거 대화 맥락으로 사용하세요. "
        "현재 사실이나 외부 정보가 필요한 질문은 허용된 MCP 도구 결과를 우선 근거로 삼고, "
        "기억에 저장된 과거 답변을 최신 사실처럼 재사용하지 마세요. "
        "질문에는 한국어로 답하고 없는 사실은 만들지 마세요.\n\n"
        f"{search_instruction}"
        f"[Mnemome 장기 기억]\n{memory_context}\n\n"
        f"[사용자 질문]\n{query}"
    )

    live_model = AsyncOpenAIClient(
        api_key=api_key,
        model=model_name,
        base_url=base_url,
        generation_parameters={"max_output_tokens": 700},
    )

    started = time.perf_counter()
    workflow_payload: dict[str, Any] | None = None
    mcp_url, allowed_tools = _mcp_settings()
    mcp_status: dict[str, Any] = {
        "status": "not_configured" if not mcp_url else "unavailable",
        "tool_count": 0,
        "tools": [],
    }
    async def execute_agent(agent_tools: list[Any]) -> tuple[Any, dict[str, Any] | None]:
        async with AsyncToolCallingAgent(
            model=live_model,
            tools=agent_tools,
            name="Mnemome Memory Guide",
            description=(
                "Mnemome 장기 기억을 사용자 맥락으로 활용하고 MCP 도구로 현재 사실을 "
                "조회하는 한국어 Agent. 뉴스와 최신 정보 요청은 search_retrieve를 반드시 "
                "사용하며, 과거 기억을 현재 사실로 대체하지 않는다."
            ),
            long_term_memory=memory,
            memory_search_top_k=5,
            memory_store_outputs=True,
            deterministic_trajectory=True,
            max_replans=0,
            num_steps=4,
            debug_verbosity="none",
            workflow_cache_dir="/tmp/mnemome-workflows",
            tracking_workflow_detail="preview",
        ) as agent:
            async with asyncio.timeout(45):
                result = await agent.run(
                    agent_task,
                    run_id=run_id,
                    metadata={"language": "ko"},
                    tracking_workflow=True,
                )
            workflow_payload = agent.get_workflow_payload(run_id)
            artifact_path = agent.get_workflow_artifact_path(run_id)
            if artifact_path:
                try:
                    Path(artifact_path).unlink(missing_ok=True)
                except OSError:
                    pass
            return result, workflow_payload

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
                result, workflow_payload = await execute_agent(agent_tools)
        except Exception:
            if connected:
                raise
            mcp_status["detail"] = "MCP 도구 서버에 연결하지 못해 메모리 전용으로 실행했습니다."
            result, workflow_payload = await execute_agent([])
    else:
        result, workflow_payload = await execute_agent([])
    elapsed_ms = round((time.perf_counter() - started) * 1_000, 2)
    return (
        result.text,
        recalled,
        elapsed_ms,
        model_name,
        _workflow_trace(workflow_payload),
        captured_preference,
        mcp_status,
    )


def build_demo_router() -> APIRouter:
    router = APIRouter(include_in_schema=False)
    limiter = DemoRateLimiter()
    chat_limiter = DemoRateLimiter(requests_per_minute=6)
    global_chat_limiter = DemoRateLimiter(max_sessions=1, requests_per_minute=30)

    @router.get("/")
    async def demo_page() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html", media_type="text/html")

    @router.get("/demo/api/status")
    async def demo_status(request: Request, response: Response) -> dict[str, Any]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        application = request.app.state.application
        await _seed_memories(application, tenant_id)
        memories = await application.list_facts(tenant_id, limit=100)
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
        }

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
        memories = await application.list_facts(tenant_id, kind=kind, limit=100)
        clearable_count = sum(not _is_seed_memory(memory) for memory in memories)
        return {
            "items": [_memory_payload(memory) for memory in memories],
            "seeded_count": len(memories) - clearable_count,
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
        memories = await application.list_facts(tenant_id, limit=100)
        if len(memories) >= 50:
            raise HTTPException(status_code=409, detail="데모 세션은 기억을 50개까지 저장합니다.")
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
        memories = await application.list_facts(tenant_id, limit=100)
        target = next((memory for memory in memories if memory.fact_id == memory_id), None)
        if target is None:
            raise HTTPException(status_code=404, detail="기억을 찾을 수 없습니다.")
        if _is_seed_memory(target):
            raise HTTPException(status_code=409, detail="기본 샘플 기억은 유지됩니다.")
        await application.suppress_fact(tenant_id, memory_id)
        return {"deleted": True}

    @router.delete("/demo/api/memories")
    async def clear_memories(request: Request, response: Response) -> dict[str, int]:
        session_id, tenant_id = _session(request, response)
        await limiter.check(session_id)
        application = request.app.state.application
        memories = await application.list_facts(tenant_id, limit=100)
        clearable = [memory for memory in memories if not _is_seed_memory(memory)]
        for memory in clearable:
            await application.suppress_fact(tenant_id, memory.fact_id)
        return {"cleared": len(clearable), "preserved": len(memories) - len(clearable)}

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
        await _seed_memories(application, tenant_id)
        agent = await application.register_agent(
            tenant_id,
            "Mnemome Memory Guide",
            ("memory.read", "memory.write", "lotte-agent.runtime"),
        )
        run, _context = await application.open_run(
            OpenRunRequest(
                tenant_id=tenant_id,
                agent_id=agent.agent_id,
                retrieval_text=body.query,
                query_ref="demo-ui",
            )
        )
        try:
            (
                answer,
                recalled,
                elapsed_ms,
                model_name,
                execution_trace,
                preference_captured,
                mcp_status,
            ) = await _run_lotte_agent(application, tenant_id, body.query, run.run_id)
            completed = await application.complete_run(
                tenant_id,
                run.run_id,
                {"status": "answered", "runtime": "lotte-agent"},
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
                        "현재 질문과 plan/step 실행 문맥에만 유지되며 "
                        "다음 요청에는 초기화됩니다."
                    ),
                    "scope": completed.run_id,
                },
                "cultural": {
                    "status": "not_configured"
                    if completed.cultural_snapshot_id.startswith("csp_none_")
                    else "applied",
                    "count": 0,
                    "label": "문화적 기억",
                    "detail": "현재 데모에는 문화적 메모리 공급자가 연결되지 않았습니다."
                    if completed.cultural_snapshot_id.startswith("csp_none_")
                    else "문화적 스냅샷을 Agent 실행에 고정했습니다.",
                    "snapshot_id": completed.cultural_snapshot_id,
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

    return router
