from __future__ import annotations

import httpx
import pytest
from lotte_agent.memory import MemoryEntry, MemoryEntryKind

from mnemome import Mnemome
from mnemome.adapters import InMemoryStores
from mnemome.integrations.lotte_agent import MnemomeLongTermMemory
from mnemome.service.app import create_app
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
async def test_demo_page_runs_lotte_agent_with_mnemome_memory() -> None:
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

            status = await client.get("/demo/api/status")
            assert status.status_code == 200
            assert status.json()["runtime"] == "lotte-agent 0.0.11"
            assert status.json()["runtime_available"] is True
            assert status.json()["memory_count"] == 3

            response = await client.post(
                "/demo/api/chat", json={"query": "한국어로 간결하게 답변해줘"}
            )
            assert response.status_code == 200, response.text
            payload = response.json()
            assert payload["runtime"] == "AsyncToolCallingAgent"
            assert payload["recalled"]
            assert "한국어" in payload["answer"]

            memories = await client.get("/demo/api/memories")
            kinds = [item["kind"] for item in memories.json()["items"]]
            assert "conversation" in kinds

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://another.test"
        ) as isolated_client:
            isolated_status = await isolated_client.get("/demo/api/status")
            isolated_memories = await isolated_client.get("/demo/api/memories")
            assert isolated_status.json()["memory_count"] == 3
            assert len(isolated_memories.json()["items"]) == 3
