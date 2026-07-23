from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

import mnemome.service.demo_imports as demo_imports
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
async def test_huggingface_uses_split_size_and_fetches_only_requested_head_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    studio = DemoImportStudio()
    requested_pages: list[tuple[int, int]] = []
    sample_rows = [
        {
            "sessionId": f"sample-{index}",
            "conversation": [
                {"content": f"질문 {index}", "role": "user", "timestamp": ""},
                {"content": f"답변 {index}", "role": "assistant", "timestamp": ""},
            ],
        }
        for index in range(14)
    ]

    async def fake_hf_request(source: object, endpoint: str, **params: object) -> dict[str, object]:
        if endpoint == "first-rows":
            return {
                "features": [
                    {"name": "sessionId", "type": {"dtype": "string"}},
                    {"name": "conversation", "type": {"list": []}},
                ],
                "rows": [{"row": row} for row in sample_rows],
                "truncated": True,
            }
        if endpoint == "size":
            return {
                "size": {
                    "splits": [
                        {
                            "config": "default",
                            "split": "train",
                            "num_rows": 342_103,
                        }
                    ]
                }
            }
        if endpoint == "rows":
            offset = int(params["offset"])
            length = int(params["length"])
            requested_pages.append((offset, length))
            return {
                "rows": [
                    {
                        "row": {
                            "sessionId": f"full-{index}",
                            "conversation": [
                                {"content": "질문", "role": "user", "timestamp": ""},
                                {"content": "답변", "role": "assistant", "timestamp": ""},
                            ],
                        }
                    }
                    for index in range(offset, offset + length)
                ]
            }
        raise AssertionError(endpoint)

    monkeypatch.setattr(studio, "_hf_request", fake_hf_request)
    prepared = await studio.prepare(
        "tenant",
        DemoImportPrepareBody.model_validate(
            {
                "source": {
                    "type": "huggingface",
                    "repo_id": "psyche/chatgpt-log-processed",
                    "config": "default",
                    "split": "train",
                    "token": "test-token",
                }
            }
        ),
    )

    assert prepared["source"]["total_rows"] == 342_103
    assert prepared["source"]["max_process_rows"] == 40
    assert prepared["processing_allowed"] is True
    assert prepared["stats"]["input_rows"] == 5

    preparation = studio._get("tenant", prepared["preparation_id"])
    head_rows = await studio._all_rows(preparation, 3)

    assert len(head_rows) == 3
    assert requested_pages == [(0, 3)]

    with pytest.raises(HTTPException, match="최대 40 rows"):
        await studio.process(
            "tenant",
            prepared["preparation_id"],
            DemoImportProcessBody(code=prepared["code"], row_limit=41),
            application=object(),
        )


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
async def test_processing_runs_as_background_job_after_request_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_extraction(conversation: list[dict[str, object]]) -> dict[str, list[object]]:
        return {"facts": [], "preferences": [], "episodes": [], "culture": []}

    monkeypatch.setattr(demo_imports, "_extract_session_memories", no_extraction)

    class SlowApplication:
        def __init__(self) -> None:
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def list_facts(self, tenant_id: str, limit: int) -> list[object]:
            return []

        async def create_fact(self, *args: object, **kwargs: object) -> object:
            self.started.set()
            await self.release.wait()
            return SimpleNamespace(fact_id="conversation-memory")

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
    assert running["stage"] == "session 1/1 분석 중"

    application.release.set()
    await asyncio.wait_for(studio._tasks[accepted["job_id"]], timeout=1)
    completed = studio.job_status("tenant", accepted["job_id"])
    assert completed["status"] == "COMPLETED"
    assert completed["result"]["created"] == 1


@pytest.mark.asyncio
async def test_processing_persists_each_session_before_job_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_extraction(conversation: list[dict[str, object]]) -> dict[str, list[object]]:
        return {"facts": [], "preferences": [], "episodes": [], "culture": []}

    monkeypatch.setattr(demo_imports, "_extract_session_memories", no_extraction)

    class IncrementalApplication:
        def __init__(self) -> None:
            self.created: list[dict[str, object]] = []
            self.calls = 0
            self.second_started = asyncio.Event()
            self.release_second = asyncio.Event()

        async def list_facts(self, tenant_id: str, limit: int) -> list[object]:
            return []

        async def create_fact(self, *args: object, **kwargs: object) -> object:
            self.calls += 1
            if self.calls == 2:
                self.second_started.set()
                await self.release_second.wait()
            self.created.append(kwargs)
            return SimpleNamespace(fact_id=f"memory-{self.calls}")

    studio = DemoImportStudio()
    prepared = await studio.prepare(
        "tenant",
        DemoImportPrepareBody.model_validate(
            {
                "source": {"type": "local", "file_name": "incremental.json"},
                "rows": [
                    {
                        "sessionId": session_id,
                        "conversation": [
                            {"content": f"{session_id} 질문", "role": "user", "timestamp": ""},
                            {"content": f"{session_id} 답변", "role": "assistant", "timestamp": ""},
                        ],
                    }
                    for session_id in ("session-1", "session-2")
                ],
            }
        ),
    )
    application = IncrementalApplication()
    accepted = await studio.process(
        "tenant",
        prepared["preparation_id"],
        DemoImportProcessBody(code=prepared["code"]),
        application,
    )

    await asyncio.wait_for(application.second_started.wait(), timeout=1)
    running = studio.job_status("tenant", accepted["job_id"])
    assert running["status"] == "RUNNING"
    assert running["created_memories"] == 1
    assert running["completed_sessions"] == 1
    assert len(application.created) == 1

    application.release_second.set()
    await asyncio.wait_for(studio._tasks[accepted["job_id"]], timeout=1)
    completed = studio.job_status("tenant", accepted["job_id"])
    assert completed["status"] == "COMPLETED"
    assert completed["created_memories"] == 2
    assert len(application.created) == 2


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
            registered = await client.post(
                "/demo/api/auth/register",
                json={"username": "import-test", "password": "test-password"},
            )
            assert registered.status_code == 201
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
            assert job["result"]["created"] == 2

            memories = await client.get("/demo/api/memories")
            imported = [
                item
                for item in memories.json()["items"]
                if item["metadata"].get("created_via") == "import_studio"
            ]
            assert len(imported) == 1
            assert imported[0]["kind"] == "conversation"
            assert imported[0]["conversation"]["query"] == "한국어로 답해 줘"
            assert imported[0]["conversation"]["turns"] == [
                {
                    "role": "user",
                    "content": "한국어로 답해 줘",
                    "timestamp": "2026-07-22T09:00:00Z",
                },
                {
                    "role": "assistant",
                    "content": "네, 한국어로 답하겠습니다.",
                    "timestamp": "2026-07-22T09:00:01Z",
                },
            ]
            derived = [
                item
                for item in memories.json()["items"]
                if item["metadata"].get("created_via") == "import_studio_auto_extract"
            ]
            assert len(derived) == 1
            assert derived[0]["kind"] == "episode"


@pytest.mark.asyncio
async def test_import_job_pause_resume_auto_extract_and_cascade_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    extraction_started = asyncio.Event()
    release_extraction = asyncio.Event()

    async def controlled_extraction(
        conversation: list[dict[str, object]],
    ) -> dict[str, list[dict[str, object]]]:
        extraction_started.set()
        await release_extraction.wait()
        return {
            "facts": [{"content": "프로젝트는 Docker로 배포된다.", "confidence": 0.95}],
            "preferences": [
                {
                    "content": "한국어로 간결하게 답한다.",
                    "condition": "사용자에게 답변할 때",
                    "action": "한국어로 간결하게 답한다",
                    "confidence": 0.9,
                }
            ],
            "episodes": [{"content": "배포 점검을 완료했다.", "confidence": 0.85}],
            "culture": [
                {
                    "claim": "배포 전 readiness를 확인한다.",
                    "conditions": ["배포 작업"],
                    "restrictions": ["확인 없이 완료로 표시하지 않는다"],
                    "recovery": "readiness를 다시 확인한다.",
                    "confidence": 0.9,
                }
            ],
        }

    monkeypatch.setattr(demo_imports, "_extract_session_memories", controlled_extraction)
    settings = Settings(
        environment="test", database_path=":memory:", api_keys={}, log_level="WARNING"
    )
    app = create_app(settings, stores=InMemoryStores())
    rows = [
        {
            "sessionId": "controlled-session",
            "conversation": [
                {"content": "배포 점검해 줘", "role": "user", "timestamp": ""},
                {"content": "readiness까지 확인했어요", "role": "assistant", "timestamp": ""},
            ],
        }
    ]

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            registered = await client.post(
                "/demo/api/auth/register",
                json={"username": "pause-test", "password": "test-password"},
            )
            assert registered.status_code == 201
            await client.get("/demo/api/memories")
            prepared = (
                await client.post(
                    "/demo/api/imports/prepare",
                    json={
                        "source": {"type": "local", "file_name": "controlled.json"},
                        "rows": rows,
                    },
                )
            ).json()
            accepted = await client.post(
                f"/demo/api/imports/{prepared['preparation_id']}/process",
                json={"code": prepared["code"]},
            )
            job_id = accepted.json()["job_id"]
            await asyncio.wait_for(extraction_started.wait(), timeout=1)

            paused = await client.post(f"/demo/api/imports/jobs/{job_id}/pause")
            assert paused.status_code == 200
            assert paused.json()["status"] == "PAUSED"
            release_extraction.set()
            await asyncio.sleep(0)
            still_paused = await client.get(f"/demo/api/imports/jobs/{job_id}")
            assert still_paused.json()["status"] == "PAUSED"
            assert still_paused.json()["created_memories"] == 1

            resumed = await client.post(f"/demo/api/imports/jobs/{job_id}/resume")
            assert resumed.status_code == 200
            for _ in range(100):
                status = (await client.get(f"/demo/api/imports/jobs/{job_id}")).json()
                if status["status"] in {"COMPLETED", "FAILED"}:
                    break
                await asyncio.sleep(0)
            assert status["status"] == "COMPLETED"
            assert status["memory_counts"] == {
                "conversation": 1,
                "fact": 1,
                "preference": 1,
                "episode": 1,
                "culture": 1,
            }

            memories = (await client.get("/demo/api/memories")).json()
            assert memories["imported_count"] == 4
            assert (await client.delete("/demo/api/memories")).json()["cleared"] == 0
            culture = (await client.get("/demo/api/cultural-snapshot")).json()
            assert any(
                item["claim"] == "배포 전 readiness를 확인한다." for item in culture["items"]
            )

            user_id = registered.json()["user"]["id"]
            tenant_id = f"demo_user_{user_id}"
            recovered_studio = DemoImportStudio()
            recovered_jobs = await recovered_studio.list_jobs(tenant_id, app.state.application)
            assert recovered_jobs["items"][0]["status"] == "COMPLETED"
            assert recovered_jobs["items"][0]["memory_counts"]["preference"] == 1
            deleted = await recovered_studio.delete_job(tenant_id, job_id, app.state.application)
            assert deleted["deleted_memories"] == 4
            assert deleted["deleted_cultural_memories"] == 1

            registry_cleanup = await client.delete(f"/demo/api/imports/jobs/{job_id}")
            assert registry_cleanup.status_code == 200
            assert (await client.get("/demo/api/memories")).json()["imported_count"] == 0
            assert (await client.get("/demo/api/imports/jobs")).json()["items"] == []
            culture = (await client.get("/demo/api/cultural-snapshot")).json()
            assert all(
                item["claim"] != "배포 전 readiness를 확인한다." for item in culture["items"]
            )
