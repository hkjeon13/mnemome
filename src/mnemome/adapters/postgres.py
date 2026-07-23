from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from ..contracts import (
    AgentDescriptor,
    AgentEvent,
    AgentRun,
    Checkpoint,
    CulturalArtifact,
    CulturalArtifactStatus,
    CulturalSnapshot,
    DomainEvent,
    FactStatus,
    MemoryFact,
    RunStatus,
    SourceRef,
)


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)


def _loaded(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


class PostgresStores:
    """Async PostgreSQL adapter for the Mnemome service profile."""

    def __init__(
        self,
        database_url: str,
        *,
        min_pool_size: int = 1,
        max_pool_size: int = 10,
        command_timeout: float = 5.0,
    ) -> None:
        self.database_url = database_url
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size
        self.command_timeout = command_timeout
        self._pool: Any | None = None

    @property
    def pool(self) -> Any:
        if self._pool is None:
            raise RuntimeError("PostgresStores.initialize() must be called first")
        return self._pool

    async def initialize(self) -> None:
        import asyncpg

        self._pool = await asyncpg.create_pool(
            self.database_url,
            min_size=self.min_pool_size,
            max_size=self.max_pool_size,
            command_timeout=self.command_timeout,
        )
        async with self.pool.acquire() as connection:
            await connection.execute(_SCHEMA)

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    async def save_agent(self, agent: AgentDescriptor) -> None:
        await self.pool.execute(
            """
            INSERT INTO agents
              (tenant_id, agent_id, name, version, capabilities_json, created_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6)
            ON CONFLICT (tenant_id, agent_id) DO UPDATE SET
              name=EXCLUDED.name, version=EXCLUDED.version,
              capabilities_json=EXCLUDED.capabilities_json
            """,
            agent.tenant_id,
            agent.agent_id,
            agent.name,
            agent.version,
            _json(agent.capabilities),
            agent.created_at,
        )

    async def get_agent(self, tenant_id: str, agent_id: str) -> AgentDescriptor | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM agents WHERE tenant_id=$1 AND agent_id=$2",
            tenant_id,
            agent_id,
        )
        if row is None:
            return None
        return AgentDescriptor(
            agent_id=row["agent_id"],
            tenant_id=row["tenant_id"],
            name=row["name"],
            version=row["version"],
            capabilities=tuple(_loaded(row["capabilities_json"])),
            created_at=row["created_at"],
        )

    async def save_run(self, run: AgentRun) -> None:
        await self.pool.execute(
            """
            INSERT INTO runs
              (tenant_id, run_id, agent_id, agent_descriptor_version, status,
               context_version, cultural_snapshot_id, workspace_id, query_ref,
               write_episode, created_at, updated_at, outcome_json, response_ref,
               failure_json)
            VALUES
              ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13::jsonb,$14,$15::jsonb)
            ON CONFLICT (tenant_id, run_id) DO UPDATE SET
              status=EXCLUDED.status,
              context_version=EXCLUDED.context_version,
              updated_at=EXCLUDED.updated_at,
              outcome_json=EXCLUDED.outcome_json,
              response_ref=EXCLUDED.response_ref,
              failure_json=EXCLUDED.failure_json
            """,
            run.tenant_id,
            run.run_id,
            run.agent_id,
            run.agent_descriptor_version,
            run.status.value,
            run.context_version,
            run.cultural_snapshot_id,
            run.workspace_id,
            run.query_ref,
            run.write_episode,
            run.created_at,
            run.updated_at,
            _json(run.outcome) if run.outcome is not None else None,
            run.response_ref,
            _json(run.failure) if run.failure is not None else None,
        )

    async def get_run(self, tenant_id: str, run_id: str) -> AgentRun | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM runs WHERE tenant_id=$1 AND run_id=$2",
            tenant_id,
            run_id,
        )
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
            write_episode=row["write_episode"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            outcome=_loaded(row["outcome_json"]) if row["outcome_json"] else None,
            response_ref=row["response_ref"],
            failure=_loaded(row["failure_json"]) if row["failure_json"] else None,
        )

    async def append_agent_event(self, event: AgentEvent) -> None:
        await self.pool.execute(
            """
            INSERT INTO agent_events
              (tenant_id, run_id, event_id, sequence, event_type, payload_json, occurred_at)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7)
            ON CONFLICT (tenant_id, event_id) DO NOTHING
            """,
            event.tenant_id,
            event.run_id,
            event.event_id,
            event.sequence,
            event.event_type,
            _json(event.payload),
            event.occurred_at,
        )

    async def list_agent_events(self, tenant_id: str, run_id: str) -> list[AgentEvent]:
        rows = await self.pool.fetch(
            """
            SELECT * FROM agent_events
            WHERE tenant_id=$1 AND run_id=$2 ORDER BY sequence
            """,
            tenant_id,
            run_id,
        )
        return [
            AgentEvent(
                event_id=row["event_id"],
                run_id=row["run_id"],
                tenant_id=row["tenant_id"],
                sequence=row["sequence"],
                event_type=row["event_type"],
                payload=_loaded(row["payload_json"]),
                occurred_at=row["occurred_at"],
            )
            for row in rows
        ]

    async def save_checkpoint(self, checkpoint: Checkpoint) -> None:
        await self.pool.execute(
            """
            INSERT INTO checkpoints
              (tenant_id, run_id, checkpoint_id, context_version,
               checkpoint_ref, metadata_json, created_at)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7)
            ON CONFLICT (tenant_id, checkpoint_id) DO NOTHING
            """,
            checkpoint.tenant_id,
            checkpoint.run_id,
            checkpoint.checkpoint_id,
            checkpoint.context_version,
            checkpoint.checkpoint_ref,
            _json(checkpoint.metadata),
            checkpoint.created_at,
        )

    async def save_fact(self, fact: MemoryFact) -> None:
        await self.pool.execute(
            """
            INSERT INTO memory_facts
              (tenant_id, fact_id, statement, confidence, status, sources_json,
               created_at, supersedes_fact_id, kind, tags_json, metadata_json)
            VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10::jsonb,$11::jsonb)
            ON CONFLICT (tenant_id, fact_id) DO UPDATE SET
              status=EXCLUDED.status,
              statement=EXCLUDED.statement,
              confidence=EXCLUDED.confidence,
              sources_json=EXCLUDED.sources_json,
              kind=EXCLUDED.kind,
              tags_json=EXCLUDED.tags_json,
              metadata_json=EXCLUDED.metadata_json
            """,
            fact.tenant_id,
            fact.fact_id,
            fact.statement,
            fact.confidence,
            fact.status.value,
            _json([asdict(source) for source in fact.sources]),
            fact.created_at,
            fact.supersedes_fact_id,
            fact.kind,
            _json(fact.tags),
            _json(fact.metadata),
        )

    @staticmethod
    def _fact(row: Any) -> MemoryFact:
        return MemoryFact(
            fact_id=row["fact_id"],
            tenant_id=row["tenant_id"],
            statement=row["statement"],
            confidence=row["confidence"],
            status=FactStatus(row["status"]),
            sources=tuple(
                SourceRef(**source) for source in _loaded(row["sources_json"])
            ),
            created_at=row["created_at"],
            kind=row["kind"],
            tags=tuple(_loaded(row["tags_json"])),
            metadata=_loaded(row["metadata_json"]),
            supersedes_fact_id=row["supersedes_fact_id"],
        )

    async def get_fact(self, tenant_id: str, fact_id: str) -> MemoryFact | None:
        row = await self.pool.fetchrow(
            "SELECT * FROM memory_facts WHERE tenant_id=$1 AND fact_id=$2",
            tenant_id,
            fact_id,
        )
        return self._fact(row) if row else None

    async def list_facts(self, tenant_id: str) -> list[MemoryFact]:
        rows = await self.pool.fetch(
            """
            SELECT * FROM memory_facts
            WHERE tenant_id=$1 ORDER BY created_at DESC
            """,
            tenant_id,
        )
        return [self._fact(row) for row in rows]

    async def save_cultural_artifact(self, artifact: CulturalArtifact) -> None:
        await self.pool.execute(
            """
            INSERT INTO cultural_artifacts
              (tenant_id, artifact_id, scope, version, claim, conditions_json,
               restrictions_json, recovery, evidence_refs_json, status,
               metadata_json, supersedes_artifact_id, created_at)
            VALUES
              ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8,$9::jsonb,$10,$11::jsonb,$12,$13)
            ON CONFLICT (tenant_id, artifact_id) DO UPDATE SET
              status=EXCLUDED.status, metadata_json=EXCLUDED.metadata_json
            """,
            artifact.tenant_id,
            artifact.artifact_id,
            artifact.scope,
            artifact.version,
            artifact.claim,
            _json(artifact.conditions),
            _json(artifact.restrictions),
            artifact.recovery,
            _json([asdict(source) for source in artifact.evidence_refs]),
            artifact.status.value,
            _json(artifact.metadata),
            artifact.supersedes_artifact_id,
            artifact.created_at,
        )

    @staticmethod
    def _artifact(row: Any) -> CulturalArtifact:
        return CulturalArtifact(
            artifact_id=row["artifact_id"],
            tenant_id=row["tenant_id"],
            scope=row["scope"],
            version=row["version"],
            claim=row["claim"],
            conditions=tuple(_loaded(row["conditions_json"])),
            restrictions=tuple(_loaded(row["restrictions_json"])),
            recovery=row["recovery"],
            evidence_refs=tuple(
                SourceRef(**source) for source in _loaded(row["evidence_refs_json"])
            ),
            status=CulturalArtifactStatus(row["status"]),
            metadata=_loaded(row["metadata_json"]),
            supersedes_artifact_id=row["supersedes_artifact_id"],
            created_at=row["created_at"],
        )

    async def get_cultural_artifact(
        self, tenant_id: str, artifact_id: str
    ) -> CulturalArtifact | None:
        row = await self.pool.fetchrow(
            """
            SELECT * FROM cultural_artifacts
            WHERE tenant_id=$1 AND artifact_id=$2
            """,
            tenant_id,
            artifact_id,
        )
        return self._artifact(row) if row else None

    async def list_cultural_artifacts(
        self, tenant_id: str, scope: str | None = None
    ) -> list[CulturalArtifact]:
        if scope is None:
            rows = await self.pool.fetch(
                """
                SELECT * FROM cultural_artifacts
                WHERE tenant_id=$1 ORDER BY created_at DESC
                """,
                tenant_id,
            )
        else:
            rows = await self.pool.fetch(
                """
                SELECT * FROM cultural_artifacts
                WHERE tenant_id=$1 AND scope=$2 ORDER BY created_at DESC
                """,
                tenant_id,
                scope,
            )
        return [self._artifact(row) for row in rows]

    async def save_cultural_snapshot(self, snapshot: CulturalSnapshot) -> None:
        await self.pool.execute(
            """
            INSERT INTO cultural_snapshots
              (tenant_id, snapshot_id, scope, version, artifact_ids_json,
               content_digest, policy_version, previous_snapshot_id, created_at)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9)
            ON CONFLICT (tenant_id, snapshot_id) DO NOTHING
            """,
            snapshot.tenant_id,
            snapshot.snapshot_id,
            snapshot.scope,
            snapshot.version,
            _json(snapshot.artifact_ids),
            snapshot.content_digest,
            snapshot.policy_version,
            snapshot.previous_snapshot_id,
            snapshot.created_at,
        )

    @staticmethod
    def _snapshot(row: Any) -> CulturalSnapshot:
        return CulturalSnapshot(
            snapshot_id=row["snapshot_id"],
            tenant_id=row["tenant_id"],
            scope=row["scope"],
            version=row["version"],
            artifact_ids=tuple(_loaded(row["artifact_ids_json"])),
            content_digest=row["content_digest"],
            policy_version=row["policy_version"],
            previous_snapshot_id=row["previous_snapshot_id"],
            created_at=row["created_at"],
        )

    async def get_cultural_snapshot(
        self, tenant_id: str, snapshot_id: str
    ) -> CulturalSnapshot | None:
        row = await self.pool.fetchrow(
            """
            SELECT * FROM cultural_snapshots
            WHERE tenant_id=$1 AND snapshot_id=$2
            """,
            tenant_id,
            snapshot_id,
        )
        return self._snapshot(row) if row else None

    async def get_active_cultural_snapshot(
        self, tenant_id: str, scope: str
    ) -> CulturalSnapshot | None:
        row = await self.pool.fetchrow(
            """
            SELECT snapshots.*
            FROM cultural_snapshots AS snapshots
            JOIN active_cultural_snapshots AS active
              ON active.tenant_id=snapshots.tenant_id
             AND active.snapshot_id=snapshots.snapshot_id
            WHERE active.tenant_id=$1 AND active.scope=$2
            """,
            tenant_id,
            scope,
        )
        return self._snapshot(row) if row else None

    async def activate_cultural_snapshot(self, snapshot: CulturalSnapshot) -> None:
        await self.pool.execute(
            """
            INSERT INTO active_cultural_snapshots (tenant_id, scope, snapshot_id)
            VALUES ($1,$2,$3)
            ON CONFLICT (tenant_id, scope) DO UPDATE SET
              snapshot_id=EXCLUDED.snapshot_id
            """,
            snapshot.tenant_id,
            snapshot.scope,
            snapshot.snapshot_id,
        )

    async def append_domain_event(self, event: DomainEvent) -> None:
        await self.pool.execute(
            """
            INSERT INTO outbox_events
              (event_id, event_type, tenant_id, aggregate_type, aggregate_id,
               aggregate_version, occurred_at, payload_json)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8::jsonb)
            ON CONFLICT (event_id) DO NOTHING
            """,
            event.event_id,
            event.event_type,
            event.tenant_id,
            event.aggregate_type,
            event.aggregate_id,
            event.aggregate_version,
            event.occurred_at,
            _json(event.payload),
        )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS agents (
  tenant_id TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  name TEXT NOT NULL,
  version INTEGER NOT NULL,
  capabilities_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
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
  write_episode BOOLEAN NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL,
  outcome_json JSONB,
  response_ref TEXT,
  failure_json JSONB,
  PRIMARY KEY (tenant_id, run_id)
);
CREATE TABLE IF NOT EXISTS agent_events (
  tenant_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  sequence INTEGER NOT NULL,
  event_type TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, event_id),
  UNIQUE (tenant_id, run_id, sequence)
);
CREATE TABLE IF NOT EXISTS checkpoints (
  tenant_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  checkpoint_id TEXT NOT NULL,
  context_version INTEGER NOT NULL,
  checkpoint_ref TEXT NOT NULL,
  metadata_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, checkpoint_id)
);
CREATE TABLE IF NOT EXISTS memory_facts (
  tenant_id TEXT NOT NULL,
  fact_id TEXT NOT NULL,
  statement TEXT NOT NULL,
  confidence DOUBLE PRECISION NOT NULL,
  status TEXT NOT NULL,
  sources_json JSONB NOT NULL,
  created_at TIMESTAMPTZ NOT NULL,
  supersedes_fact_id TEXT,
  kind TEXT NOT NULL DEFAULT 'fact',
  tags_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY (tenant_id, fact_id)
);
CREATE INDEX IF NOT EXISTS ix_memory_facts_tenant_status
  ON memory_facts (tenant_id, status);
CREATE TABLE IF NOT EXISTS cultural_artifacts (
  tenant_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  version INTEGER NOT NULL,
  claim TEXT NOT NULL,
  conditions_json JSONB NOT NULL,
  restrictions_json JSONB NOT NULL,
  recovery TEXT,
  evidence_refs_json JSONB NOT NULL,
  status TEXT NOT NULL,
  metadata_json JSONB NOT NULL,
  supersedes_artifact_id TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, artifact_id)
);
CREATE INDEX IF NOT EXISTS ix_cultural_artifacts_scope
  ON cultural_artifacts (tenant_id, scope, status);
CREATE TABLE IF NOT EXISTS cultural_snapshots (
  tenant_id TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  version INTEGER NOT NULL,
  artifact_ids_json JSONB NOT NULL,
  content_digest TEXT NOT NULL,
  policy_version TEXT NOT NULL,
  previous_snapshot_id TEXT,
  created_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY (tenant_id, snapshot_id),
  UNIQUE (tenant_id, scope, version)
);
CREATE TABLE IF NOT EXISTS active_cultural_snapshots (
  tenant_id TEXT NOT NULL,
  scope TEXT NOT NULL,
  snapshot_id TEXT NOT NULL,
  PRIMARY KEY (tenant_id, scope)
);
CREATE TABLE IF NOT EXISTS outbox_events (
  event_id TEXT PRIMARY KEY,
  event_type TEXT NOT NULL,
  tenant_id TEXT NOT NULL,
  aggregate_type TEXT NOT NULL,
  aggregate_id TEXT NOT NULL,
  aggregate_version INTEGER NOT NULL,
  occurred_at TIMESTAMPTZ NOT NULL,
  payload_json JSONB NOT NULL,
  published_at TIMESTAMPTZ
);
"""
