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
Persistent instructions expressed with markers such as `앞으로` or `항상` are
also promoted to preference memories and injected into every later run.
Recall uses BM25 over MeCab-compatible Korean noun tokens (system MeCab when
available, otherwise PeCab) and NLTK English noun tokens. Tokenization is
cached, and the same transport-independent implementation serves both the
embedded library and HTTP service profiles.
The memory vault marks the three built-in samples as protected. A browser can
clear every user-created and Agent-generated memory in its own isolated demo
tenant without affecting those samples or any other browser session.

The integration uses `MnemomeLongTermMemory`, an adapter for Lotte Agent's
public `LongTermMemory` protocol. The public demo uses Lotte Agent's real
`AsyncOpenAIClient`; the provider key stays in the server environment. Global
and per-session request limits plus a bounded output budget protect live calls.
The UI renders a sanitized Lotte Agent workflow trace with real plan titles,
step states, model-call counts, and latency. It also distinguishes persistent
long-term recall, run-scoped short-term context, and the currently unconfigured
cultural-memory provider, and identifies the retriever used for long-term
memory.

When `MNEMOME_MCP_URL` is configured, the demo connects Lotte Agent to the
remote streamable-HTTP MCP server inside the API process. The public profile
uses an explicit read-oriented allowlist for web/news retrieval, company and
market data, analysis, and sandboxed calculation. Document mutation, artifact
creation, and browser-control tools are not exposed to public demo users.

## Development

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
```

See [`docs/deployment.md`](./docs/deployment.md) for the small-server deployment
profile and current limitations.
