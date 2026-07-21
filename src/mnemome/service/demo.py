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

DEMO_COOKIE = "mnemome_demo_session"
DEMO_TENANT_PREFIX = "demo_"
DEMO_SESSION_LENGTH = 32
STATIC_DIR = Path(__file__).with_name("static")
DEFAULT_OPENAI_MODEL = "gpt-4.1-mini"


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
    }


async def _run_lotte_agent(
    application: Any,
    tenant_id: str,
    query: str,
    run_id: str,
) -> tuple[str, list[Any], float, str]:
    try:
        from lotte_agent import AsyncToolCallingAgent
        from lotte_agent.models import AsyncOpenAIClient

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
    recalled = await memory.search(query, top_k=5)
    if recalled:
        memory_context = "\n".join(
            f"- [{entry.kind.value}] {entry.content}" for entry in recalled
        )
    else:
        memory_context = "- 관련 장기 기억 없음"
    agent_task = (
        "다음 Mnemome 장기 기억을 우선 근거로 사용해 사용자 질문에 한국어로 답하세요. "
        "기억과 질문이 직접 관련되면 기억의 내용을 정확히 반영하고, 없는 사실은 만들지 마세요.\n\n"
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
    async with AsyncToolCallingAgent(
        model=live_model,
        tools={},
        name="Mnemome Memory Guide",
        description=(
            "Mnemome 장기 기억을 실제로 검색해 답하는 한국어 Agent. "
            "주어진 long_term_memory를 우선 근거로 사용하고, 기억에 없는 사실을 지어내지 않는다."
        ),
        long_term_memory=memory,
        memory_search_top_k=5,
        memory_store_outputs=True,
        deterministic_trajectory=True,
        max_replans=0,
        num_steps=2,
        debug_verbosity="none",
    ) as agent:
        result = await asyncio.wait_for(
            agent.run(agent_task, run_id=run_id, metadata={"language": "ko"}), timeout=45
        )
    elapsed_ms = round((time.perf_counter() - started) * 1_000, 2)
    return result.text, recalled, elapsed_ms, model_name


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
        return {"items": [_memory_payload(memory) for memory in memories]}

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
        await application.suppress_fact(tenant_id, memory_id)
        return {"deleted": True}

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
            answer, recalled, elapsed_ms, model_name = await _run_lotte_agent(
                application, tenant_id, body.query, run.run_id
            )
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
