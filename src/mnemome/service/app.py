import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
from dataclasses import asdict, replace
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, FastAPI, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..adapters import PostgresStores, SqliteStores, ValkeyCachedStores
from ..application import MnemomeApplication
from ..contracts import FactInput, OpenRunRequest, SourceRef
from ..errors import AuthenticationError, AuthorizationError, MnemomeError
from ..ports import Stores
from .demo import STATIC_DIR, build_demo_router
from .demo_auth import DemoAuthStore
from .schemas import (
    AgentEventBody,
    CheckpointBody,
    CompleteRunBody,
    CorrectFactBody,
    CreateFactBody,
    CulturalArtifactBody,
    FailRunBody,
    OpenRunBody,
    PublishCulturalSnapshotBody,
    RegisterAgentBody,
    ReviseCulturalArtifactBody,
)
from .settings import ApiPrincipal, Settings

logger = logging.getLogger("mnemome.service")


def _response(value: Any, *, status_code: int = 200) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=jsonable_encoder(value))


def _build_stores(configuration: Settings) -> Stores:
    if configuration.storage_backend == "postgres":
        assert configuration.database_url is not None
        stores: Stores = PostgresStores(
            configuration.database_url,
            min_pool_size=configuration.db_pool_min_size,
            max_pool_size=configuration.db_pool_max_size,
            command_timeout=configuration.db_command_timeout_s,
        )
    else:
        stores = SqliteStores(configuration.database_path)
    if configuration.valkey_url:
        return ValkeyCachedStores(
            stores,
            configuration.valkey_url,
            prefix=configuration.valkey_prefix,
            ttl_s=configuration.recall_cache_ttl_s,
        )
    return stores


def create_app(settings: Settings | None = None, *, stores: Stores | None = None) -> FastAPI:
    configuration = settings or Settings.from_environment()
    application = MnemomeApplication(stores or _build_stores(configuration))
    demo_auth = DemoAuthStore(configuration.database_path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=configuration.log_level)
        await application.initialize()
        demo_auth.initialize()
        app.state.application = application
        app.state.demo_auth = demo_auth
        app.state.settings = configuration
        logger.info("Mnemome API started in %s mode", configuration.environment)
        try:
            yield
        finally:
            demo_auth.close()
            await application.close()

    app = FastAPI(
        title="Mnemome API",
        version="0.1.0",
        description="Memory infrastructure for external agents; no agent inference endpoint.",
        lifespan=lifespan,
    )
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    app.include_router(build_demo_router())

    @app.exception_handler(MnemomeError)
    async def handle_mnemome_error(request: Request, error: MnemomeError) -> JSONResponse:
        request_id = request.headers.get("X-Request-Id", "unassigned")
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "request_id": request_id,
                    "retryable": error.retryable,
                    "details": error.details,
                }
            },
        )

    async def principal(
        authorization: Annotated[str | None, Header()] = None,
        delegated_tenant: Annotated[
            str | None, Header(alias="X-Mnemome-Tenant")
        ] = None,
        delegated_timestamp: Annotated[
            str | None, Header(alias="X-Mnemome-Timestamp")
        ] = None,
        delegated_signature: Annotated[
            str | None, Header(alias="X-Mnemome-Signature")
        ] = None,
    ) -> ApiPrincipal:
        if not authorization or not authorization.startswith("Bearer "):
            raise AuthenticationError("A bearer API key is required")
        candidate = authorization.removeprefix("Bearer ")
        for key, identity in configuration.api_keys.items():
            if hmac.compare_digest(candidate, key):
                if not delegated_tenant:
                    return identity
                if not identity.can("tenant:delegate"):
                    raise AuthorizationError(
                        "The principal cannot delegate a tenant scope"
                    )
                secret = configuration.tenant_delegation_secret
                if not secret or not delegated_timestamp or not delegated_signature:
                    raise AuthenticationError(
                        "Tenant delegation headers are incomplete"
                    )
                try:
                    timestamp = int(delegated_timestamp)
                except ValueError as error:
                    raise AuthenticationError(
                        "Tenant delegation timestamp is invalid"
                    ) from error
                if abs(int(time.time()) - timestamp) > configuration.tenant_delegation_max_skew_s:
                    raise AuthenticationError(
                        "Tenant delegation timestamp is outside the allowed window"
                    )
                if not delegated_tenant.startswith("usr_") or len(delegated_tenant) > 80:
                    raise AuthenticationError("Delegated tenant is invalid")
                payload = f"{delegated_tenant}\n{delegated_timestamp}".encode()
                expected = hmac.new(secret.encode(), payload, "sha256").hexdigest()
                if not hmac.compare_digest(delegated_signature, expected):
                    raise AuthenticationError(
                        "Tenant delegation signature is invalid"
                    )
                return replace(identity, tenant_id=delegated_tenant)
        raise AuthenticationError("The bearer API key is invalid")

    def require(role: str):
        async def dependency(identity: Annotated[ApiPrincipal, Depends(principal)]) -> ApiPrincipal:
            if not identity.can(role):
                raise AuthorizationError(f"The principal requires the {role} role")
            return identity

        return dependency

    AgentPrincipal = Annotated[ApiPrincipal, Depends(require("agent"))]
    MemoryReader = Annotated[ApiPrincipal, Depends(require("memory:read"))]
    MemoryWriter = Annotated[ApiPrincipal, Depends(require("memory:write"))]
    CultureReader = Annotated[ApiPrincipal, Depends(require("culture:read"))]
    CultureWriter = Annotated[ApiPrincipal, Depends(require("culture:write"))]
    CulturePublisher = Annotated[ApiPrincipal, Depends(require("culture:publish"))]

    @app.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["operations"])
    async def ready() -> dict[str, str]:
        return {"status": "ready", "storage": configuration.storage_backend}

    @app.post("/v1/agents", status_code=201, tags=["agents"])
    async def register_agent(body: RegisterAgentBody, identity: AgentPrincipal) -> JSONResponse:
        agent = await application.register_agent(
            identity.tenant_id, body.name, tuple(body.capabilities)
        )
        return _response(agent, status_code=201)

    @app.post("/v1/runs", status_code=201, tags=["runs"])
    async def open_run(body: OpenRunBody, identity: AgentPrincipal) -> JSONResponse:
        run, context = await application.open_run(
            OpenRunRequest(
                tenant_id=identity.tenant_id,
                agent_id=body.agent_id,
                agent_descriptor_version=body.agent_descriptor_version,
                retrieval_text=body.context_request.retrieval_text,
                workspace_id=body.workspace_id,
                recall=body.memory_policy.recall,
                write_episode=body.memory_policy.write_episode,
                cultural_scope=body.memory_policy.cultural_scope,
                query_ref=body.context_request.query_ref,
            )
        )
        return _response(
            {
                "run_id": run.run_id,
                "status": run.status,
                "context": context,
                "cultural_snapshot_id": run.cultural_snapshot_id,
                "stream_url": f"/v1/runs/{run.run_id}/events",
            },
            status_code=201,
        )

    @app.get("/v1/runs/{run_id}", tags=["runs"])
    async def get_run(run_id: str, identity: AgentPrincipal) -> JSONResponse:
        return _response(await application.get_run(identity.tenant_id, run_id))

    @app.post("/v1/runs/{run_id}/agent-events", status_code=201, tags=["runs"])
    async def append_event(
        run_id: str, body: AgentEventBody, identity: AgentPrincipal
    ) -> JSONResponse:
        event = await application.append_agent_event(
            identity.tenant_id,
            run_id,
            body.event_type,
            body.payload,
            caller_event_id=body.event_id,
        )
        return _response(event, status_code=201)

    @app.post("/v1/runs/{run_id}/checkpoints", status_code=201, tags=["runs"])
    async def checkpoint(
        run_id: str, body: CheckpointBody, identity: AgentPrincipal
    ) -> JSONResponse:
        value = await application.record_checkpoint(
            identity.tenant_id, run_id, body.checkpoint_ref, body.metadata
        )
        return _response(value, status_code=201)

    @app.post("/v1/runs/{run_id}:complete", tags=["runs"])
    async def complete_run(
        run_id: str, body: CompleteRunBody, identity: AgentPrincipal
    ) -> JSONResponse:
        facts = tuple(
            FactInput(
                statement=fact.statement,
                confidence=fact.confidence,
                sources=tuple(SourceRef(**source.model_dump()) for source in fact.sources),
            )
            for fact in body.facts
        )
        run = await application.complete_run(
            identity.tenant_id,
            run_id,
            body.outcome,
            response_ref=body.response_ref,
            facts=facts,
        )
        return _response(run)

    @app.post("/v1/runs/{run_id}:fail", tags=["runs"])
    async def fail_run(
        run_id: str, body: FailRunBody, identity: AgentPrincipal
    ) -> JSONResponse:
        return _response(await application.fail_run(identity.tenant_id, run_id, body.failure))

    @app.post("/v1/runs/{run_id}:request-cancel", tags=["runs"])
    async def request_cancel(run_id: str, identity: AgentPrincipal) -> JSONResponse:
        return _response(await application.request_cancel(identity.tenant_id, run_id))

    @app.get("/v1/runs/{run_id}/events", tags=["runs"])
    async def run_events(
        run_id: str,
        identity: AgentPrincipal,
        last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    ) -> StreamingResponse:
        events = await application.list_agent_events(identity.tenant_id, run_id)
        start = 0
        if last_event_id:
            for index, event in enumerate(events):
                if event.event_id == last_event_id:
                    start = index + 1
                    break

        async def stream():
            for event in events[start:]:
                data = json.dumps(asdict(event), ensure_ascii=False, default=str)
                yield f"id: {event.event_id}\nevent: agent.event.recorded\ndata: {data}\n\n"
            yield ": replay-complete\n\n"

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/v1/memories:recall", tags=["memory"])
    async def recall(
        identity: MemoryReader,
        query: Annotated[str, Query(max_length=20_000)] = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 10,
        mode: Literal["semantic", "recent", "temporal", "hybrid"] = "semantic",
        kind: Annotated[str | None, Query()] = None,
        created_after: Annotated[datetime | None, Query()] = None,
        created_before: Annotated[datetime | None, Query()] = None,
        order: Literal["relevance", "created_at_desc", "created_at_asc"] | None = None,
        exclude_tags: Annotated[list[str] | None, Query()] = None,
    ) -> dict[str, Any]:
        return {
            "items": await application.recall(
                identity.tenant_id,
                query,
                limit=limit,
                mode=mode,
                kind=kind,
                created_after=created_after,
                created_before=created_before,
                order=order,
                exclude_tags=tuple(exclude_tags or ()),
            )
        }

    @app.get("/v1/memory-facts", tags=["memory"])
    async def list_facts(
        identity: MemoryReader,
        kind: Annotated[str | None, Query()] = None,
        include_suppressed: bool = False,
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
    ) -> dict[str, Any]:
        return {
            "items": await application.list_facts(
                identity.tenant_id,
                kind=kind,
                include_suppressed=include_suppressed,
                limit=limit,
            )
        }

    @app.post("/v1/memory-facts", status_code=201, tags=["memory"])
    async def create_fact(body: CreateFactBody, identity: MemoryWriter) -> JSONResponse:
        sources = tuple(SourceRef(**source.model_dump()) for source in body.sources) or (
            SourceRef("principal", identity.principal_id),
        )
        fact = await application.create_fact(
            identity.tenant_id,
            body.statement,
            confidence=body.confidence,
            sources=sources,
            kind=body.kind,
            tags=tuple(body.tags),
            metadata=body.metadata,
            fact_id=body.fact_id,
        )
        return _response(fact, status_code=201)

    @app.get("/v1/memory-facts/{fact_id}", tags=["memory"])
    async def get_fact(fact_id: str, identity: MemoryReader) -> JSONResponse:
        return _response(await application.get_fact(identity.tenant_id, fact_id))

    @app.post("/v1/memory-facts/{fact_id}:correct", status_code=201, tags=["memory"])
    async def correct_fact(
        fact_id: str, body: CorrectFactBody, identity: MemoryWriter
    ) -> JSONResponse:
        corrected = await application.correct_fact(
            identity.tenant_id,
            fact_id,
            body.statement,
            confidence=body.confidence,
            sources=tuple(SourceRef(**source.model_dump()) for source in body.sources),
        )
        return _response(corrected, status_code=201)

    @app.post("/v1/memory-facts/{fact_id}:suppress", tags=["memory"])
    async def suppress_fact(fact_id: str, identity: MemoryWriter) -> JSONResponse:
        return _response(await application.suppress_fact(identity.tenant_id, fact_id))

    @app.get("/v1/cultural-artifacts", tags=["culture"])
    async def list_cultural_artifacts(
        identity: CultureReader,
        scope: Annotated[str | None, Query(max_length=200)] = None,
        include_withdrawn: bool = False,
    ) -> dict[str, Any]:
        return {
            "items": await application.list_cultural_artifacts(
                identity.tenant_id,
                scope=scope,
                include_withdrawn=include_withdrawn,
            )
        }

    @app.post("/v1/cultural-artifacts", status_code=201, tags=["culture"])
    async def create_cultural_artifact(
        body: CulturalArtifactBody, identity: CultureWriter
    ) -> JSONResponse:
        artifact = await application.create_cultural_artifact(
            identity.tenant_id,
            body.scope,
            body.claim,
            conditions=tuple(body.conditions),
            restrictions=tuple(body.restrictions),
            recovery=body.recovery,
            evidence_refs=tuple(
                SourceRef(**source.model_dump()) for source in body.evidence_refs
            ),
            metadata=body.metadata,
        )
        return _response(artifact, status_code=201)

    @app.get("/v1/cultural-artifacts/{artifact_id}", tags=["culture"])
    async def get_cultural_artifact(
        artifact_id: str, identity: CultureReader
    ) -> JSONResponse:
        return _response(
            await application.get_cultural_artifact(identity.tenant_id, artifact_id)
        )

    @app.post(
        "/v1/cultural-artifacts/{artifact_id}:revise", status_code=201, tags=["culture"]
    )
    async def revise_cultural_artifact(
        artifact_id: str,
        body: ReviseCulturalArtifactBody,
        identity: CultureWriter,
    ) -> JSONResponse:
        artifact = await application.revise_cultural_artifact(
            identity.tenant_id,
            artifact_id,
            claim=body.claim,
            conditions=tuple(body.conditions),
            restrictions=tuple(body.restrictions),
            recovery=body.recovery,
            evidence_refs=tuple(
                SourceRef(**source.model_dump()) for source in body.evidence_refs
            ),
            metadata=body.metadata,
        )
        return _response(artifact, status_code=201)

    @app.post("/v1/cultural-artifacts/{artifact_id}:withdraw", tags=["culture"])
    async def withdraw_cultural_artifact(
        artifact_id: str, identity: CultureWriter
    ) -> JSONResponse:
        return _response(
            await application.withdraw_cultural_artifact(identity.tenant_id, artifact_id)
        )

    @app.post("/v1/cultural-snapshots:publish", status_code=201, tags=["culture"])
    async def publish_cultural_snapshot(
        body: PublishCulturalSnapshotBody, identity: CulturePublisher
    ) -> JSONResponse:
        snapshot = await application.publish_cultural_snapshot(
            identity.tenant_id,
            body.scope,
            artifact_ids=(tuple(body.artifact_ids) if body.artifact_ids is not None else None),
            policy_version=body.policy_version,
        )
        return _response(snapshot, status_code=201)

    @app.get("/v1/cultural-snapshots:resolve", tags=["culture"])
    async def resolve_cultural_snapshot(
        identity: CultureReader,
        scope: Annotated[str, Query(min_length=1, max_length=200)] = "default",
    ) -> JSONResponse:
        snapshot, artifacts = await application.resolve_cultural_snapshot(
            identity.tenant_id, scope
        )
        return _response({"snapshot": snapshot, "artifacts": artifacts})

    @app.get("/v1/cultural-snapshots/{snapshot_id}", tags=["culture"])
    async def get_cultural_snapshot(
        snapshot_id: str, identity: CultureReader
    ) -> JSONResponse:
        return _response(
            await application.get_cultural_snapshot(identity.tenant_id, snapshot_id)
        )

    return app
