from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts import (
    AgentDescriptor,
    AgentEvent,
    AgentRun,
    Checkpoint,
    DomainEvent,
    FactStatus,
    MemoryFact,
    RunStatus,
    SourceRef,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SqliteStores:
    """Single-process durable adapter for pilots and small on-prem deployments."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._connection: sqlite3.Connection | None = None
        self._lock = asyncio.Lock()

    @property
    def connection(self) -> sqlite3.Connection:
        if self._connection is None:
            raise RuntimeError("SqliteStores.initialize() must be called first")
        return self._connection

    async def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(self.path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS agents (
                tenant_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                name TEXT NOT NULL,
                version INTEGER NOT NULL,
                capabilities_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, agent_id)
            );
            CREATE TABLE IF NOT EXISTS runs (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                agent_descriptor_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                context_version INTEGER NOT NULL,
                cultural_snapshot_id TEXT NOT NULL,
                workspace_id TEXT,
                query_ref TEXT,
                write_episode INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                outcome_json TEXT,
                response_ref TEXT,
                failure_json TEXT,
                PRIMARY KEY (tenant_id, run_id)
            );
            CREATE TABLE IF NOT EXISTS agent_events (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                event_id TEXT NOT NULL,
                sequence INTEGER NOT NULL,
                event_type TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                occurred_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, event_id),
                UNIQUE (tenant_id, run_id, sequence)
            );
            CREATE TABLE IF NOT EXISTS checkpoints (
                tenant_id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                checkpoint_id TEXT NOT NULL,
                context_version INTEGER NOT NULL,
                checkpoint_ref TEXT NOT NULL,
                metadata_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (tenant_id, checkpoint_id)
            );
            CREATE TABLE IF NOT EXISTS memory_facts (
                tenant_id TEXT NOT NULL,
                fact_id TEXT NOT NULL,
                statement TEXT NOT NULL,
                confidence REAL NOT NULL,
                status TEXT NOT NULL,
                sources_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                supersedes_fact_id TEXT,
                PRIMARY KEY (tenant_id, fact_id)
            );
            CREATE INDEX IF NOT EXISTS ix_memory_facts_tenant_status
                ON memory_facts (tenant_id, status);
            CREATE TABLE IF NOT EXISTS outbox_events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                aggregate_version INTEGER NOT NULL,
                occurred_at TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                published_at TEXT
            );
            """
        )
        self._connection.commit()

    async def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
            self._connection = None

    async def save_agent(self, agent: AgentDescriptor) -> None:
        async with self._lock:
            self.connection.execute(
                """INSERT INTO agents VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, agent_id) DO UPDATE SET
                  name=excluded.name, version=excluded.version,
                  capabilities_json=excluded.capabilities_json""",
                (
                    agent.tenant_id,
                    agent.agent_id,
                    agent.name,
                    agent.version,
                    _json(agent.capabilities),
                    agent.created_at.isoformat(),
                ),
            )
            self.connection.commit()

    async def get_agent(self, tenant_id: str, agent_id: str) -> AgentDescriptor | None:
        row = self.connection.execute(
            "SELECT * FROM agents WHERE tenant_id=? AND agent_id=?", (tenant_id, agent_id)
        ).fetchone()
        if row is None:
            return None
        return AgentDescriptor(
            agent_id=row["agent_id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            version=row["version"],
            capabilities=tuple(json.loads(row["capabilities_json"])),
            created_at=_dt(row["created_at"]),
        )

    async def save_run(self, run: AgentRun) -> None:
        async with self._lock:
            self.connection.execute(
                """INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, run_id) DO UPDATE SET
                  status=excluded.status, context_version=excluded.context_version,
                  updated_at=excluded.updated_at, outcome_json=excluded.outcome_json,
                  response_ref=excluded.response_ref, failure_json=excluded.failure_json""",
                (
                    run.tenant_id,
                    run.run_id,
                    run.agent_id,
                    run.agent_descriptor_version,
                    run.status.value,
                    run.context_version,
                    run.cultural_snapshot_id,
                    run.workspace_id,
                    run.query_ref,
                    int(run.write_episode),
                    run.created_at.isoformat(),
                    run.updated_at.isoformat(),
                    _json(run.outcome) if run.outcome is not None else None,
                    run.response_ref,
                    _json(run.failure) if run.failure is not None else None,
                ),
            )
            self.connection.commit()

    async def get_run(self, tenant_id: str, run_id: str) -> AgentRun | None:
        row = self.connection.execute(
            "SELECT * FROM runs WHERE tenant_id=? AND run_id=?", (tenant_id, run_id)
        ).fetchone()
        if row is None:
            return None
        return AgentRun(
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            agent_id=row["agent_id"],
            agent_descriptor_version=row["agent_descriptor_version"],
            status=RunStatus(row["status"]),
            context_version=row["context_version"],
            cultural_snapshot_id=row["cultural_snapshot_id"],
            workspace_id=row["workspace_id"],
            query_ref=row["query_ref"],
            write_episode=bool(row["write_episode"]),
            created_at=_dt(row["created_at"]),
            updated_at=_dt(row["updated_at"]),
            outcome=json.loads(row["outcome_json"]) if row["outcome_json"] else None,
            response_ref=row["response_ref"],
            failure=json.loads(row["failure_json"]) if row["failure_json"] else None,
        )

    async def append_agent_event(self, event: AgentEvent) -> None:
        async with self._lock:
            self.connection.execute(
                """INSERT OR IGNORE INTO agent_events
                (tenant_id, run_id, event_id, sequence, event_type, payload_json, occurred_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.tenant_id,
                    event.run_id,
                    event.event_id,
                    event.sequence,
                    event.event_type,
                    _json(event.payload),
                    event.occurred_at.isoformat(),
                ),
            )
            self.connection.commit()

    async def list_agent_events(self, tenant_id: str, run_id: str) -> list[AgentEvent]:
        rows = self.connection.execute(
            """SELECT * FROM agent_events WHERE tenant_id=? AND run_id=?
            ORDER BY sequence""",
            (tenant_id, run_id),
        ).fetchall()
        return [
            AgentEvent(
                event_id=row["event_id"],
                run_id=row["run_id"],
                tenant_id=row["tenant_id"],
                sequence=row["sequence"],
                event_type=row["event_type"],
                payload=json.loads(row["payload_json"]),
                occurred_at=_dt(row["occurred_at"]),
            )
            for row in rows
        ]

    async def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        async with self._lock:
            self.connection.execute(
                """INSERT OR IGNORE INTO checkpoints VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    checkpoint.tenant_id,
                    checkpoint.run_id,
                    checkpoint.checkpoint_id,
                    checkpoint.context_version,
                    checkpoint.checkpoint_ref,
                    _json(checkpoint.metadata),
                    checkpoint.created_at.isoformat(),
                ),
            )
            self.connection.commit()

    async def save_fact(self, fact: MemoryFact) -> None:
        sources = [asdict(source) for source in fact.sources]
        async with self._lock:
            self.connection.execute(
                """INSERT INTO memory_facts VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(tenant_id, fact_id) DO UPDATE SET status=excluded.status""",
                (
                    fact.tenant_id,
                    fact.fact_id,
                    fact.statement,
                    fact.confidence,
                    fact.status.value,
                    _json(sources),
                    fact.created_at.isoformat(),
                    fact.supersedes_fact_id,
                ),
            )
            self.connection.commit()

    def _fact_from_row(self, row: sqlite3.Row) -> MemoryFact:
        return MemoryFact(
            fact_id=row["fact_id"],
            tenant_id=row["tenant_id"],
            statement=row["statement"],
            confidence=row["confidence"],
            status=FactStatus(row["status"]),
            sources=tuple(SourceRef(**source) for source in json.loads(row["sources_json"])),
            created_at=_dt(row["created_at"]),
            supersedes_fact_id=row["supersedes_fact_id"],
        )

    async def get_fact(self, tenant_id: str, fact_id: str) -> MemoryFact | None:
        row = self.connection.execute(
            "SELECT * FROM memory_facts WHERE tenant_id=? AND fact_id=?",
            (tenant_id, fact_id),
        ).fetchone()
        return self._fact_from_row(row) if row else None

    async def list_facts(self, tenant_id: str) -> list[MemoryFact]:
        rows = self.connection.execute(
            "SELECT * FROM memory_facts WHERE tenant_id=? ORDER BY created_at DESC", (tenant_id,)
        ).fetchall()
        return [self._fact_from_row(row) for row in rows]

    async def append_domain_event(self, event: DomainEvent) -> None:
        async with self._lock:
            self.connection.execute(
                """INSERT OR IGNORE INTO outbox_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)""",
                (
                    event.event_id,
                    event.event_type,
                    event.tenant_id,
                    event.aggregate_type,
                    event.aggregate_id,
                    event.aggregate_version,
                    event.occurred_at.isoformat(),
                    _json(event.payload),
                ),
            )
            self.connection.commit()
