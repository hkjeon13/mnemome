from __future__ import annotations

from typing import Any

from lotte_agent.memory import MemoryEntry, MemoryEntryKind

from ..application import MnemomeApplication
from ..contracts import FactStatus, MemoryFact, SourceRef
from ..errors import NotFoundError


class MnemomeLongTermMemory:
    """Lotte Agent ``LongTermMemory`` adapter backed by a tenant-scoped Mnemome core."""

    def __init__(
        self,
        application: MnemomeApplication,
        tenant_id: str,
        *,
        max_entries: int | None = None,
        conversation_session_id: str | None = None,
        conversation_query: str | None = None,
    ) -> None:
        self._application = application
        self._tenant_id = tenant_id
        self._max_entries = max_entries
        self._conversation_session_id = conversation_session_id
        self._conversation_query = conversation_query

    @property
    def conversation_entry_id(self) -> str | None:
        if not self._conversation_session_id:
            return None
        return f"conversation:{self._conversation_session_id}"

    async def conversation_turns(self) -> list[dict[str, str]]:
        entry_id = self.conversation_entry_id
        if not entry_id:
            return []
        existing = await self.retrieve(entry_id)
        if existing is None:
            return []
        turns = (existing.metadata or {}).get("conversation_turns")
        if not isinstance(turns, list):
            return []
        return [
            {
                "role": str(turn.get("role") or ""),
                "content": str(turn.get("content") or ""),
            }
            for turn in turns
            if isinstance(turn, dict)
            and str(turn.get("role") or "") in {"user", "assistant"}
            and str(turn.get("content") or "").strip()
        ]

    async def store(self, entry: MemoryEntry) -> None:
        entry_id = entry.id
        entry_content = entry.content
        entry_tags = tuple(entry.tags)
        metadata = dict(entry.metadata or {})
        if entry.kind == MemoryEntryKind.CONVERSATION and self.conversation_entry_id:
            entry_id = self.conversation_entry_id
            existing = await self.retrieve(entry_id)
            existing_metadata = dict(existing.metadata or {}) if existing is not None else {}
            turns = await self.conversation_turns()
            run_id = str(metadata.get("run_id") or entry.id)
            run_ids = [str(item) for item in existing_metadata.get("run_ids", []) if item]
            if run_id not in run_ids:
                query = str(self._conversation_query or "").strip()
                if query:
                    turns.append({"role": "user", "content": query})
                if entry_content.strip():
                    turns.append({"role": "assistant", "content": entry_content.strip()})
                run_ids.append(run_id)
            metadata = {
                **existing_metadata,
                **metadata,
                "created_via": "demo_chat_session",
                "conversation_session_id": self._conversation_session_id,
                "conversation_turns": turns[-100:],
                "turn_count": len(turns[-100:]),
                "run_ids": run_ids[-50:],
                "latest_run_id": run_id,
                "task_text": f"[사용자 질문]\n{self._conversation_query or ''}",
            }
            entry_tags = tuple(sorted({*entry.tags, "conversation", "session"}))

        if self._max_entries is not None:
            existing = await self.retrieve(entry_id)
            facts = await self._application.list_facts(self._tenant_id, limit=500)
            if existing is None and len(facts) >= self._max_entries:
                conversations = [fact for fact in facts if fact.kind == "conversation"]
                if not conversations:
                    return
                oldest = min(conversations, key=lambda fact: fact.created_at)
                await self._application.suppress_fact(self._tenant_id, oldest.fact_id)
        source_id = str(metadata.get("run_id") or metadata.get("source_id") or entry_id)
        source_type = str(metadata.get("source_type") or "lotte_agent_runtime")
        metadata.update(
            {
                "lotte_created_at": entry.created_at,
                "lotte_last_accessed": entry.last_accessed,
                "lotte_access_count": entry.access_count,
            }
        )
        await self._application.create_fact(
            self._tenant_id,
            entry_content,
            confidence=float(metadata.get("confidence", 1.0)),
            sources=(SourceRef(source_type, source_id),),
            kind=entry.kind.value,
            tags=entry_tags,
            metadata=metadata,
            fact_id=entry_id,
        )

    async def retrieve(self, entry_id: str) -> MemoryEntry | None:
        try:
            fact = await self._application.get_fact(self._tenant_id, entry_id)
        except NotFoundError:
            return None
        if fact.status != FactStatus.ACTIVE:
            return None
        return self._to_entry(fact)

    async def search(
        self,
        query: str,
        *,
        top_k: int = 5,
        embedding: list[float] | None = None,
        kind: MemoryEntryKind | None = None,
        tags: list[str] | None = None,
    ) -> list[MemoryEntry]:
        del embedding
        candidates = await self._application.recall(
            self._tenant_id, query, limit=max(top_k * 5, top_k)
        )
        requested_tags = set(tags or ())
        entries: list[MemoryEntry] = []
        for recalled in candidates:
            fact = await self._application.get_fact(self._tenant_id, recalled.fact_id)
            if kind is not None and fact.kind != kind.value:
                continue
            if requested_tags and not requested_tags.issubset(set(fact.tags)):
                continue
            entries.append(self._to_entry(fact))
            if len(entries) >= top_k:
                break
        return entries

    async def delete(self, entry_id: str) -> bool:
        try:
            await self._application.suppress_fact(self._tenant_id, entry_id)
        except NotFoundError:
            return False
        return True

    async def list_all(
        self,
        *,
        kind: MemoryEntryKind | None = None,
        limit: int = 100,
    ) -> list[MemoryEntry]:
        facts = await self._application.list_facts(
            self._tenant_id, kind=kind.value if kind else None, limit=limit
        )
        return [self._to_entry(fact) for fact in facts]

    @staticmethod
    def _to_entry(fact: MemoryFact) -> MemoryEntry:
        metadata: dict[str, Any] = dict(fact.metadata)
        return MemoryEntry(
            id=fact.fact_id,
            kind=MemoryEntryKind(fact.kind),
            content=fact.statement,
            metadata=metadata,
            created_at=fact.created_at.timestamp(),
            last_accessed=metadata.get("lotte_last_accessed"),
            access_count=int(metadata.get("lotte_access_count") or 0),
            tags=list(fact.tags),
        )
