from __future__ import annotations

import pytest

from mnemome import FactInput, Mnemome, OpenRunRequest


@pytest.mark.asyncio
async def test_sqlite_state_survives_reopen(tmp_path) -> None:
    path = tmp_path / "mnemome.db"
    first = Mnemome.sqlite(str(path))
    await first.initialize()
    agent = await first.register_agent("tenant-a", "durable-agent")
    environment = await first.agent_environment.open_run(
        OpenRunRequest(tenant_id="tenant-a", agent_id=agent.agent_id)
    )
    completed = await environment.complete(
        {"status": "ok"},
        facts=(FactInput(statement="Durable memory survives restart"),),
    )
    await first.close()

    second = Mnemome.sqlite(str(path))
    await second.initialize()
    restored = await second.application.get_run("tenant-a", completed.run_id)
    recalled = await second.application.recall("tenant-a", "durable memory")
    await second.close()

    assert restored.status.value == "COMPLETED"
    assert recalled[0].sources[0].source_id == completed.run_id
