# Mnemome

Mnemome is library-first memory infrastructure for external AI agents. The same
application use cases power an embedded Python facade and a multi-tenant HTTP
service. Mnemome stores context, events, outcomes, facts, and provenance; it does
not run an agent, choose tools, or generate a final answer.

The implementation follows the design in [`ssd/`](./ssd/README.md). Version
`0.1.0` is the deployable Phase 0–2 vertical slice: Agent Environment, Working
Memory, Long-Term Memory recall, source provenance, tenant isolation, and an
immutable no-culture snapshot contract.

## Embedded library

```python
import asyncio

from mnemome import Mnemome, OpenRunRequest


async def main() -> None:
    memory = Mnemome.in_memory()
    agent = await memory.register_agent(
        tenant_id="local",
        name="incident-agent",
        capabilities=("memory.read", "memory.write"),
    )
    environment = await memory.agent_environment.open_run(
        OpenRunRequest(
            tenant_id="local",
            agent_id=agent.agent_id,
            retrieval_text="지난 장애의 재발 방지책",
        )
    )
    context = await environment.get_context()
    # The host-owned agent runs here using context.
    await environment.record_event("observation", {"message": "cache miss"})
    await environment.complete(outcome={"status": "resolved"})
    print(context.run_id)


asyncio.run(main())
```

For durable embedding, use `Mnemome.sqlite("/path/to/mnemome.db")`.

## SaaS/service profile

```bash
cp .env.example .env
docker compose up -d --build
curl -H 'Authorization: Bearer local-development-key' http://localhost:8080/ready
```

The default development key is intentionally refused when
`MNEMOME_ENV=production`. Configure `MNEMOME_API_KEYS` as a JSON object whose
keys are API keys and whose values contain `tenant_id`, `principal_id`, and
`roles`.

```json
{
  "replace-with-a-secret": {
    "tenant_id": "tenant-a",
    "principal_id": "service-a",
    "roles": ["agent", "memory:read", "memory:write"]
  }
}
```

Interactive API documentation is available at `/docs`. All product endpoints
are under `/v1`; health probes are `/health` and `/ready`.

## Memory demo page

The service root provides an interactive memory lab. Each browser gets an
isolated demo tenant where it can create facts, preferences, and episodes,
search or suppress them, and ask a Lotte Agent runtime to use recalled memories.
Successful Agent output is stored back as a conversation memory.

The integration uses `MnemomeLongTermMemory`, an adapter for Lotte Agent's
public `LongTermMemory` protocol. The public demo deliberately uses a bounded,
deterministic model adapter so no provider key is exposed and public traffic
cannot create unbounded LLM cost.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
```

See [`docs/deployment.md`](./docs/deployment.md) for the small-server deployment
profile and current limitations.
