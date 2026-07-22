from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import HTTPException

from mnemome.adapters import InMemoryStores
from mnemome.service.app import create_app
from mnemome.service.demo_imports import (
    DemoImportPrepareBody,
    DemoImportProcessBody,
    DemoImportStudio,
    _heuristic_code,
    _profile_rows,
    _transform_rows,
)
from mnemome.service.settings import ApiPrincipal, Settings


def test_transform_groups_repeated_session_rows_in_turn_order() -> None:
    rows = [
        {
            "session_id": "session-a",
            "content": "두 번째 답변",
            "role": "assistant",
            "turn_index": 2,
            "timestamp": "2026-07-22T09:00:02+09:00",
        },
        {
            "session_id": "session-b",
            "content": "별도 대화",
            "role": "user",
            "turn_index": 1,
            "timestamp": "2026-07-22T09:01:00+09:00",
        },
        {
            "session_id": "session-a",
            "content": "첫 번째 질문",
            "role": "user",
            "turn_index": 1,
            "timestamp": "2026-07-22T09:00:00+09:00",
        },
    ]

    profile = _profile_rows(rows)
    result = _transform_rows(rows, _heuristic_code(profile))

    assert profile["layout"] == "TURN_PER_ROW"
    assert result["stats"] == {
        "input_rows": 3,
        "dropped_rows": 0,
        "fragments": 3,
        "sessions": 2,
        "turns": 3,
    }
    session = next(item for item in result["sessions"] if item["sessionId"] == "session-a")
    assert [turn["content"] for turn in session["conversation"]] == [
        "첫 번째 질문",
        "두 번째 답변",
    ]


def test_transform_supports_one_session_per_row() -> None:
    rows = [
        {
            "sessionId": "session-1",
            "conversation": [
                {"content": "안녕", "role": "user", "timestamp": "2026-07-22T09:00:00Z"},
                {
                    "content": "반가워요",
                    "role": "assistant",
                    "timestamp": "2026-07-22T09:00:01Z",
                },
            ],
        }
    ]

    profile = _profile_rows(rows)
    result = _transform_rows(rows, _heuristic_code(profile))

    assert profile["layout"] == "SESSION_PER_ROW"
    assert result["sessions"] == rows


@pytest.mark.asyncio
async def test_reused_session_id_is_detected_and_processing_is_blocked() -> None:
    rows = [
        {"session_id": "reused", "content": "첫 세션", "role": "user", "turn_index": 1},
        {"session_id": "reused", "content": "다른 세션", "role": "user", "turn_index": 1},
    ]
    studio = DemoImportStudio()
    prepared = await studio.prepare(
        "tenant",
        DemoImportPrepareBody.model_validate(
            {
                "source": {"type": "local", "file_name": "reused.jsonl"},
                "rows": rows,
            }
        ),
    )

    assert prepared["profile"]["layout"] == "REUSED_SESSION_ID_SUSPECTED"
    assert prepared["processing_allowed"] is False
    assert [item["sessionId"] for item in prepared["result"]] == [
        "reused:row-0",
        "reused:row-1",
    ]

    with pytest.raises(HTTPException, match="turn order"):
        await studio.process(
            "tenant",
            prepared["preparation_id"],
            DemoImportProcessBody(code=prepared["code"]),
            application=object(),
        )


@pytest.mark.asyncio
async def test_processing_runs_as_background_job_after_request_returns() -> None:
    class SlowApplication:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def list_facts(self, tenant_id: str, limit: int) -> list[object]:
            return []

        async def create_fact(self, *args: object, **kwargs: object) -> None:
            self.started.set()
            await self.release.wait()

    studio = DemoImportStudio()
    prepared = await studio.prepare(
        "tenant",
        DemoImportPrepareBody.model_validate(
            {
                "source": {"type": "local", "file_name": "background.json"},
                "rows": [
                    {
                        "sessionId": "background-session",
                        "conversation": [
                            {"content": "질문", "role": "user", "timestamp": ""},
                            {"content": "답변", "role": "assistant", "timestamp": ""},
                        ],
                    }
                ],
            }
        ),
    )
    application = SlowApplication()

    accepted = await studio.process(
        "tenant",
        prepared["preparation_id"],
        DemoImportProcessBody(code=prepared["code"]),
        application,
    )

    assert accepted["status"] == "QUEUED"
    await asyncio.wait_for(application.started.wait(), timeout=1)
    running = studio.job_status("tenant", accepted["job_id"])
    assert running["status"] == "RUNNING"
    assert running["stage"] == "대화 메모리에 반영하는 중"

    application.release.set()
    await asyncio.wait_for(studio._tasks[accepted["job_id"]], timeout=1)
    completed = studio.job_status("tenant", accepted["job_id"])
    assert completed["status"] == "COMPLETED"
    assert completed["result"]["created"] == 1


@pytest.mark.asyncio
async def test_import_studio_previews_and_persists_conversation_memories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = Settings(
        environment="test",
        database_path=":memory:",
        api_keys={
            "key": ApiPrincipal(
                "tenant",
                "principal",
                frozenset({"admin", "agent", "memory:read", "memory:write"}),
            )
        },
        log_level="WARNING",
    )
    app = create_app(settings, stores=InMemoryStores())
    rows = [
        {
            "session_id": "session-a",
            "text": "한국어로 답해 줘",
            "speaker": "human",
            "created_at": "2026-07-22T09:00:00Z",
            "turn_index": 1,
        },
        {
            "session_id": "session-a",
            "text": "네, 한국어로 답하겠습니다.",
            "speaker": "assistant",
            "created_at": "2026-07-22T09:00:01Z",
            "turn_index": 2,
        },
    ]

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            await client.get("/demo/api/memories")
            prepared = await client.post(
                "/demo/api/imports/prepare",
                json={
                    "source": {"type": "local", "file_name": "sample.jsonl"},
                    "rows": rows,
                    "instructions": "session_id로 묶어 줘",
                },
            )
            assert prepared.status_code == 200
            payload = prepared.json()
            assert payload["profile"]["layout"] == "TURN_PER_ROW"
            assert payload["result"][0]["conversation"][1]["role"] == "assistant"

            previewed = await client.post(
                f"/demo/api/imports/{payload['preparation_id']}/preview",
                json={"code": payload["code"]},
            )
            assert previewed.status_code == 200

            processed = await client.post(
                f"/demo/api/imports/{payload['preparation_id']}/process",
                json={"code": payload["code"]},
            )
            assert processed.status_code == 202
            job_id = processed.json()["job_id"]
            for _ in range(100):
                status = await client.get(f"/demo/api/imports/jobs/{job_id}")
                assert status.status_code == 200
                job = status.json()
                if job["status"] in {"COMPLETED", "FAILED"}:
                    break
                await asyncio.sleep(0)
            assert job["status"] == "COMPLETED"
            assert job["progress"] == 100
            assert job["result"]["created"] == 1

            memories = await client.get("/demo/api/memories")
            imported = [
                item
                for item in memories.json()["items"]
                if item["metadata"].get("created_via") == "import_studio"
            ]
            assert len(imported) == 1
            assert imported[0]["kind"] == "conversation"
            assert imported[0]["conversation"]["query"] == "한국어로 답해 줘"
