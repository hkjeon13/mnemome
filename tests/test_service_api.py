from __future__ import annotations

import httpx
import pytest

from mnemome.adapters import InMemoryStores
from mnemome.service.app import create_app
from mnemome.service.settings import ApiPrincipal, Settings


@pytest.fixture
def settings() -> Settings:
    roles = frozenset({"agent", "memory:read", "memory:write"})
    return Settings(
        environment="test",
        database_path=":memory:",
        api_keys={
            "key-a": ApiPrincipal("tenant-a", "principal-a", roles),
            "key-b": ApiPrincipal("tenant-b", "principal-b", roles),
        },
        log_level="WARNING",
    )


@pytest.mark.asyncio
async def test_service_end_to_end_and_tenant_isolation(settings: Settings) -> None:
    app = create_app(settings, stores=InMemoryStores())
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            unauthenticated = await client.post("/v1/agents", json={"name": "agent"})
            assert unauthenticated.status_code == 401

            headers_a = {"Authorization": "Bearer key-a"}
            headers_b = {"Authorization": "Bearer key-b"}
            created = await client.post(
                "/v1/agents",
                headers=headers_a,
                json={"name": "incident-agent", "capabilities": ["memory.read"]},
            )
            assert created.status_code == 201
            agent_id = created.json()["agent_id"]

            wrong_tenant = await client.post(
                "/v1/runs", headers=headers_b, json={"agent_id": agent_id}
            )
            assert wrong_tenant.status_code == 404

            opened = await client.post(
                "/v1/runs",
                headers=headers_a,
                json={
                    "agent_id": agent_id,
                    "context_request": {"retrieval_text": "past incident"},
                },
            )
            assert opened.status_code == 201
            run_id = opened.json()["run_id"]

            event = await client.post(
                f"/v1/runs/{run_id}/agent-events",
                headers=headers_a,
                json={
                    "event_id": "caller-1",
                    "event_type": "observation",
                    "payload": {"detail": "timeout"},
                },
            )
            assert event.status_code == 201

            completed = await client.post(
                f"/v1/runs/{run_id}:complete",
                headers=headers_a,
                json={
                    "outcome": {"status": "resolved"},
                    "facts": [
                        {
                            "statement": "Timeout caused the past incident",
                            "confidence": 0.95,
                            "sources": [
                                {"source_type": "agent_event", "source_id": "caller-1"}
                            ],
                        }
                    ],
                },
            )
            assert completed.status_code == 200
            assert completed.json()["status"] == "COMPLETED"

            recall_a = await client.get(
                "/v1/memories:recall",
                headers=headers_a,
                params={"query": "past incident"},
            )
            recall_b = await client.get(
                "/v1/memories:recall",
                headers=headers_b,
                params={"query": "past incident"},
            )
            assert len(recall_a.json()["items"]) == 1
            assert recall_b.json()["items"] == []

            direct_memory = await client.post(
                "/v1/memory-facts",
                headers=headers_a,
                json={
                    "statement": "Prefer concise summaries",
                    "kind": "preference",
                    "tags": ["style"],
                },
            )
            assert direct_memory.status_code == 201
            listed = await client.get(
                "/v1/memory-facts",
                headers=headers_a,
                params={"kind": "preference"},
            )
            assert [item["fact_id"] for item in listed.json()["items"]] == [
                direct_memory.json()["fact_id"]
            ]

            replay = await client.get(f"/v1/runs/{run_id}/events", headers=headers_a)
            assert replay.status_code == 200
            assert "agent.event.recorded" in replay.text
