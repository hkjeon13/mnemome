from __future__ import annotations

from typing import Any

import httpx


class MnemomeClient:
    """Async client for the service profile; it never performs agent inference."""

    def __init__(self, base_url: str, api_key: str, *, timeout: float = 15.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )

    async def __aenter__(self) -> MnemomeClient:
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def register_agent(
        self, name: str, capabilities: list[str] | None = None
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/agents", json={"name": name, "capabilities": capabilities or []}
        )
        response.raise_for_status()
        return response.json()

    async def open_run(
        self,
        agent_id: str,
        *,
        retrieval_text: str = "",
        query_ref: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/runs",
            json={
                "agent_id": agent_id,
                "context_request": {
                    "retrieval_text": retrieval_text,
                    "query_ref": query_ref,
                },
            },
        )
        response.raise_for_status()
        return response.json()

    async def record_event(
        self,
        run_id: str,
        event_type: str,
        payload: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"/v1/runs/{run_id}/agent-events",
            json={"event_type": event_type, "payload": payload, "event_id": event_id},
        )
        response.raise_for_status()
        return response.json()

    async def complete_run(
        self,
        run_id: str,
        outcome: dict[str, Any],
        *,
        response_ref: str | None = None,
        facts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"/v1/runs/{run_id}:complete",
            json={"outcome": outcome, "response_ref": response_ref, "facts": facts or []},
        )
        response.raise_for_status()
        return response.json()

    async def recall(self, query: str, *, limit: int = 10) -> list[dict[str, Any]]:
        response = await self._client.get(
            "/v1/memories:recall", params={"query": query, "limit": limit}
        )
        response.raise_for_status()
        return response.json()["items"]

    async def list_memories(
        self, *, kind: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"limit": limit}
        if kind:
            params["kind"] = kind
        response = await self._client.get("/v1/memory-facts", params=params)
        response.raise_for_status()
        return response.json()["items"]

    async def create_memory(
        self,
        statement: str,
        *,
        kind: str = "fact",
        tags: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/memory-facts",
            json={
                "statement": statement,
                "kind": kind,
                "tags": tags or [],
                "metadata": metadata or {},
            },
        )
        response.raise_for_status()
        return response.json()

    async def suppress_memory(self, fact_id: str) -> dict[str, Any]:
        response = await self._client.post(f"/v1/memory-facts/{fact_id}:suppress")
        response.raise_for_status()
        return response.json()

    async def create_cultural_artifact(
        self,
        claim: str,
        *,
        scope: str = "default",
        conditions: list[str] | None = None,
        restrictions: list[str] | None = None,
        recovery: str | None = None,
        evidence_refs: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/cultural-artifacts",
            json={
                "scope": scope,
                "claim": claim,
                "conditions": conditions or [],
                "restrictions": restrictions or [],
                "recovery": recovery,
                "evidence_refs": evidence_refs or [],
                "metadata": metadata or {},
            },
        )
        response.raise_for_status()
        return response.json()

    async def list_cultural_artifacts(
        self, *, scope: str | None = None, include_withdrawn: bool = False
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"include_withdrawn": include_withdrawn}
        if scope:
            params["scope"] = scope
        response = await self._client.get("/v1/cultural-artifacts", params=params)
        response.raise_for_status()
        return response.json()["items"]

    async def revise_cultural_artifact(
        self, artifact_id: str, **changes: Any
    ) -> dict[str, Any]:
        response = await self._client.post(
            f"/v1/cultural-artifacts/{artifact_id}:revise", json=changes
        )
        response.raise_for_status()
        return response.json()

    async def withdraw_cultural_artifact(self, artifact_id: str) -> dict[str, Any]:
        response = await self._client.post(
            f"/v1/cultural-artifacts/{artifact_id}:withdraw"
        )
        response.raise_for_status()
        return response.json()

    async def publish_cultural_snapshot(
        self,
        *,
        scope: str = "default",
        artifact_ids: list[str] | None = None,
        policy_version: str = "culture-policy-v1",
    ) -> dict[str, Any]:
        response = await self._client.post(
            "/v1/cultural-snapshots:publish",
            json={
                "scope": scope,
                "artifact_ids": artifact_ids,
                "policy_version": policy_version,
            },
        )
        response.raise_for_status()
        return response.json()

    async def resolve_cultural_snapshot(self, scope: str = "default") -> dict[str, Any]:
        response = await self._client.get(
            "/v1/cultural-snapshots:resolve", params={"scope": scope}
        )
        response.raise_for_status()
        return response.json()
