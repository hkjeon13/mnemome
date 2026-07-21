from __future__ import annotations

import pytest

from mnemome import Mnemome, OpenRunRequest, SourceRef


@pytest.mark.asyncio
async def test_cultural_snapshot_is_pinned_and_revision_is_versioned() -> None:
    memory = Mnemome.in_memory()
    await memory.initialize()
    artifact = await memory.cultural_memory.create(
        "tenant-a",
        "default",
        "Answer the direct question before adding context.",
        conditions=("all user questions",),
        restrictions=("Do not repeat a prior answer verbatim.",),
        evidence_refs=(SourceRef("policy", "culture-1"),),
    )
    first_snapshot = await memory.cultural_memory.publish("tenant-a", "default")
    agent = await memory.register_agent("tenant-a", "agent")
    first_environment = await memory.agent_environment.open_run(
        OpenRunRequest(tenant_id="tenant-a", agent_id=agent.agent_id)
    )
    first_context = await first_environment.get_context()

    revised = await memory.cultural_memory.revise(
        "tenant-a",
        artifact.artifact_id,
        claim="Answer in one direct sentence before adding context.",
        conditions=("all user questions",),
        restrictions=("Do not repeat a prior answer verbatim.",),
        evidence_refs=(SourceRef("policy", "culture-2"),),
    )
    second_snapshot = await memory.cultural_memory.publish("tenant-a", "default")
    second_environment = await memory.agent_environment.open_run(
        OpenRunRequest(tenant_id="tenant-a", agent_id=agent.agent_id)
    )
    second_context = await second_environment.get_context()

    assert artifact.status.value == "DRAFT"
    assert revised.version == 2
    assert revised.supersedes_artifact_id == artifact.artifact_id
    assert first_context.cultural_snapshot_id == first_snapshot.snapshot_id
    assert first_context.cultural_artifacts[0].claim == artifact.claim
    assert second_context.cultural_snapshot_id == second_snapshot.snapshot_id
    assert second_context.cultural_artifacts[0].claim == revised.claim
    assert second_snapshot.previous_snapshot_id == first_snapshot.snapshot_id
    await memory.close()


@pytest.mark.asyncio
async def test_sqlite_cultural_snapshot_survives_reopen(tmp_path) -> None:
    path = tmp_path / "culture.db"
    first = Mnemome.sqlite(str(path))
    await first.initialize()
    artifact = await first.cultural_memory.create(
        "tenant-a", "kr", "Use the approved Korean terminology."
    )
    snapshot = await first.cultural_memory.publish(
        "tenant-a", "kr", artifact_ids=(artifact.artifact_id,)
    )
    await first.close()

    second = Mnemome.sqlite(str(path))
    await second.initialize()
    restored, artifacts = await second.cultural_memory.resolve("tenant-a", "kr")
    assert restored is not None
    assert restored.snapshot_id == snapshot.snapshot_id
    assert artifacts[0].claim == artifact.claim
    await second.close()
