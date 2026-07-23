from __future__ import annotations

from dataclasses import replace

import pytest

from mnemome.adapters import InMemoryStores, ValkeyCachedStores
from mnemome.contracts import FactStatus, MemoryFact, SourceRef, utc_now


class FakeValkey:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.deleted: list[str] = []

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def set(self, key: str, value: str, *, ex: int) -> None:
        del ex
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.values.pop(key, None)

    async def aclose(self) -> None:
        return None


def _fact(statement: str = "cached fact") -> MemoryFact:
    return MemoryFact(
        fact_id="fac_1",
        tenant_id="tenant-a",
        statement=statement,
        confidence=1.0,
        status=FactStatus.ACTIVE,
        sources=(SourceRef("test", "source-1"),),
        created_at=utc_now(),
    )


@pytest.mark.asyncio
async def test_valkey_cache_round_trip_and_invalidation() -> None:
    stores = InMemoryStores()
    await stores.initialize()
    await stores.save_fact(_fact())
    cached = ValkeyCachedStores(stores, "redis://unused")
    client = FakeValkey()
    cached._client = client

    first = await cached.list_facts("tenant-a")
    stores._facts.clear()
    second = await cached.list_facts("tenant-a")

    assert first == second
    assert second[0].statement == "cached fact"

    await cached.save_fact(replace(second[0], statement="updated"))
    assert client.deleted == ["mnemome:v1:facts:tenant-a"]
