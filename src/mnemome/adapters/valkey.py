from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from typing import Any

from ..contracts import FactStatus, MemoryFact, SourceRef
from ..ports import Stores

logger = logging.getLogger("mnemome.valkey")


class ValkeyCachedStores:
    """Fail-open cache decorator for tenant fact lists used by recall."""

    def __init__(
        self,
        stores: Stores,
        url: str,
        *,
        prefix: str = "mnemome:v1",
        ttl_s: int = 60,
    ) -> None:
        self._stores = stores
        self._url = url
        self._prefix = prefix.rstrip(":")
        self._ttl_s = ttl_s
        self._client: Any | None = None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stores, name)

    async def initialize(self) -> None:
        await self._stores.initialize()
        try:
            from redis.asyncio import Redis

            self._client = Redis.from_url(
                self._url,
                decode_responses=True,
                socket_connect_timeout=0.5,
                socket_timeout=0.5,
            )
            await self._client.ping()
        except Exception as error:
            self._client = None
            logger.warning("Valkey cache unavailable during startup: %s", error)

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception as error:
                logger.warning("Valkey cache close failed: %s", error)
            self._client = None
        await self._stores.close()

    def _facts_key(self, tenant_id: str) -> str:
        return f"{self._prefix}:facts:{tenant_id}"

    async def save_fact(self, fact: MemoryFact) -> None:
        await self._stores.save_fact(fact)
        await self._delete(self._facts_key(fact.tenant_id))

    async def get_fact(self, tenant_id: str, fact_id: str) -> MemoryFact | None:
        return await self._stores.get_fact(tenant_id, fact_id)

    async def list_facts(self, tenant_id: str) -> list[MemoryFact]:
        key = self._facts_key(tenant_id)
        cached = await self._get(key)
        if cached is not None:
            try:
                return [self._fact_from_dict(item) for item in json.loads(cached)]
            except (TypeError, ValueError, KeyError):
                await self._delete(key)
        facts = await self._stores.list_facts(tenant_id)
        await self._set(
            key,
            json.dumps(
                [asdict(fact) for fact in facts],
                ensure_ascii=False,
                separators=(",", ":"),
                default=str,
            ),
        )
        return facts

    async def _get(self, key: str) -> str | None:
        if self._client is None:
            return None
        try:
            return await self._client.get(key)
        except Exception as error:
            logger.warning("Valkey cache read failed: %s", error)
            return None

    async def _set(self, key: str, value: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.set(key, value, ex=self._ttl_s)
        except Exception as error:
            logger.warning("Valkey cache write failed: %s", error)

    async def _delete(self, key: str) -> None:
        if self._client is None:
            return
        try:
            await self._client.delete(key)
        except Exception as error:
            logger.warning("Valkey cache invalidation failed: %s", error)

    @staticmethod
    def _fact_from_dict(value: dict[str, Any]) -> MemoryFact:
        return MemoryFact(
            fact_id=value["fact_id"],
            tenant_id=value["tenant_id"],
            statement=value["statement"],
            confidence=float(value["confidence"]),
            status=FactStatus(value["status"]),
            sources=tuple(SourceRef(**source) for source in value["sources"]),
            created_at=datetime.fromisoformat(value["created_at"]),
            kind=value.get("kind", "fact"),
            tags=tuple(value.get("tags", ())),
            metadata=dict(value.get("metadata", {})),
            supersedes_fact_id=value.get("supersedes_fact_id"),
        )
