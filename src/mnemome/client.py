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
