# 20. Cultural Memory 데모 구현 계획

## 1. 목적

현재 `csp_none_*` placeholder로만 존재하는 Cultural Memory를 실제 library와 SaaS API 기능으로 구현하고, Playground에서는 서버가 주입한 기본 Cultural Snapshot을 읽기 전용으로 적용한다.

핵심 원칙은 다음과 같다.

- Cultural Memory는 개인의 Fact, Preference, Episode, Conversation과 분리한다.
- Core library와 `/v1` API에서는 생성, 버전 관리, 발행, 철회 기능을 제공한다.
- 데모의 `/demo/api`와 Playground만 사용자 변경을 허용하지 않는다.
- Agent Run은 시작 시 활성 Cultural Snapshot을 pin하며 실행 도중 변경하지 않는다.
- 문화적으로 승인된 지식은 도구 실행 권한이나 보안 정책을 부여하지 않는다.
- 기존 no-culture 실행과 공개 Agent Environment 계약의 하위 호환성을 유지한다.

---

## 2. 구현 범위

### 2.1 이번 vertical slice에 포함

- Tenant와 scope별 Cultural Artifact 저장
- Artifact version과 provenance 보존
- Immutable Cultural Snapshot 생성과 발행
- scope별 active snapshot 조회
- Agent Run 시작 시 snapshot pin
- ContextBundle과 Lotte Agent 입력에 Cultural Artifact 주입
- library facade와 정식 `/v1` 관리 API
- 서버 소유 데모 샘플 자동 생성
- Playground의 읽기 전용 문화적 메모리 표시
- 실행 추적의 적용 상태, artifact 수, snapshot ID 표시
- Artifact 철회와 다음 snapshot 발행
- In-memory와 SQLite adapter 구현 및 contract test

### 2.2 이번 vertical slice에서 제외

- Blind review와 reviewer 독립성 관리
- Structured Deliberation
- A/B Test와 Experiment Coordinator
- LLM Judge 기반 자동 승인
- Cross-tenant cultural federation
- Valkey hot snapshot cache와 분산 publisher

제외 항목은 `ssd/08-cultural-memory-and-deliberation.md`와 Phase 5–6에서 확장하며, 이번 데이터 모델과 API는 해당 확장을 막지 않아야 한다.

---

## 3. 데모 기본 Cultural Memory

데모는 각 브라우저의 격리 tenant에 동일한 서버 소유 Artifact와 Snapshot을 멱등 생성한다. 브라우저 사용자는 이 항목을 조회할 수 있지만 추가, 수정, 삭제, 철회 또는 발행할 수 없다.

초기 샘플은 다음과 같이 구성한다.

### Artifact A — 독도 응답 원칙

- Claim: 독도 관련 질문에는 대한민국 정부의 공식 입장과 대한민국의 실효적 지배 사실을 먼저 명시한다.
- Condition: `topic=dokdo`, `locale=ko`
- Restriction: 독도를 단순히 분쟁지역이라고만 표현하지 않는다.
- Provenance: 서버가 관리하는 데모 정책 문서와 버전

### Artifact B — 기억과 최신 정보의 구분

- Claim: 과거 대화 기억은 사용자 맥락으로만 사용하고 최신 사실을 대체하지 않는다.
- Condition: `freshness_sensitive=true`
- Recovery: 최신 정보가 필요하면 허용된 검색 도구로 다시 검증한다.
- Provenance: Mnemome 데모 운영 정책과 버전

데모 seed metadata에는 다음을 기록한다.

```json
{
  "managed_by": "mnemome_demo_server",
  "read_only": true,
  "seed_version": "demo-culture-v1"
}
```

`DELETE /demo/api/memories`는 개인 장기 메모리만 처리하며 Cultural Artifact와 Snapshot에는 접근하지 않는다.

---

## 4. Domain Model

### 4.1 Cultural Artifact

```text
CulturalArtifact
  artifact_id
  tenant_id
  scope
  version
  claim
  conditions
  restrictions
  recovery
  evidence_refs[]
  source_refs[]
  status: draft | published | withdrawn
  metadata
  created_at
  supersedes_artifact_id?
```

Artifact 수정은 in-place update가 아니라 새 version 생성으로 처리한다. Published Artifact의 payload는 변경할 수 없다.

### 4.2 Cultural Snapshot

```text
CulturalSnapshot
  snapshot_id
  tenant_id
  scope
  version
  artifact_versions[]
  withdrawn_versions[]
  policy_version
  content_digest
  previous_snapshot_id?
  created_at
```

Snapshot은 immutable이다. 동일 artifact manifest와 policy version으로 발행하면 같은 digest를 생성해야 한다.

### 4.3 Active Snapshot Pointer

```text
ActiveCulturalSnapshot
  tenant_id
  scope
  snapshot_id
  generation
  updated_at
```

발행 과정은 snapshot 저장 후 active pointer를 원자적으로 전환한다. 발행 실패 시 기존 pointer를 유지한다.

---

## 5. Core Library 설계

### 5.1 Repository port

`src/mnemome/ports.py`에 다음 계약을 추가한다.

```python
class CulturalRepository(Protocol):
    async def save_artifact(...): ...
    async def get_artifact(...): ...
    async def list_artifacts(...): ...
    async def save_snapshot(...): ...
    async def get_snapshot(...): ...
    async def get_active_snapshot(...): ...
    async def activate_snapshot(...): ...
```

In-memory와 SQLite adapter는 동일 contract test를 통과해야 한다.

### 5.2 Application use case

`MnemomeApplication`에 다음 use case를 추가한다.

- `create_cultural_artifact()`
- `revise_cultural_artifact()`
- `withdraw_cultural_artifact()`
- `publish_cultural_snapshot()`
- `resolve_cultural_snapshot()`
- `list_cultural_artifacts()`

`open_run()`은 `cultural_scope`의 active snapshot을 조회한다. 활성 snapshot이 없으면 기존과 동일하게 `csp_none_{scope}`를 사용한다.

### 5.3 Context contract

`ContextBundle`에 하위 호환 가능한 기본값을 갖는 필드를 추가한다.

```python
cultural_artifacts: tuple[ResolvedCulturalArtifact, ...] = ()
```

`ResolvedCulturalArtifact`는 Agent에 필요한 claim, conditions, restrictions, recovery, provenance만 포함하며 관리용 내부 metadata는 노출하지 않는다.

### 5.4 Embedded facade

Library 사용자는 HTTP 없이 동일 기능을 호출할 수 있어야 한다.

```python
culture = memory.culture
artifact = await culture.create_artifact(...)
snapshot = await culture.publish(scope="team/default")
resolved = await culture.resolve_snapshot(scope="team/default")
```

---

## 6. SQLite 저장 구조

초기 구현은 다음 table을 추가한다.

- `cultural_artifacts`
- `cultural_snapshots`
- `cultural_snapshot_artifacts`
- `active_cultural_snapshots`

모든 primary key와 unique index는 `tenant_id`를 포함한다. `active_cultural_snapshots`는 `(tenant_id, scope)`를 unique key로 사용한다.

Migration 요구사항:

- 기존 database를 데이터 손실 없이 expand한다.
- 기존 Run의 `csp_none_*` 값은 그대로 유효하다.
- 새 schema를 읽지 않는 이전 application으로 rollback할 수 있다.
- snapshot payload와 digest는 deterministic serialization을 사용한다.

---

## 7. 정식 SaaS API

정식 API 기능은 제한하지 않는다. 인증된 tenant principal은 role에 따라 다음 endpoint를 사용한다.

| Method | Path | 권한 | 책임 |
| --- | --- | --- | --- |
| `GET` | `/v1/cultural-snapshots/active` | `culture:read` | scope별 active snapshot 조회 |
| `GET` | `/v1/cultural-snapshots/{id}` | `culture:read` | immutable snapshot 조회 |
| `GET` | `/v1/meme-artifacts` | `culture:read` | Artifact 목록 조회 |
| `GET` | `/v1/meme-artifacts/{id}` | `culture:read` | Artifact와 provenance 조회 |
| `POST` | `/v1/meme-artifacts` | `culture:write` | Draft Artifact 생성 |
| `POST` | `/v1/meme-artifacts/{id}:revise` | `culture:write` | 새 Artifact version 생성 |
| `POST` | `/v1/meme-artifacts/{id}:withdraw` | `culture:publish` | Artifact 철회 |
| `POST` | `/v1/cultural-snapshots:publish` | `culture:publish` | 새 immutable snapshot 발행 |

정책:

- API key와 tenant scope는 기존 인증 경계를 재사용한다.
- `culture:write`와 `culture:publish`를 분리한다.
- Published Artifact는 직접 수정할 수 없다.
- 철회는 기존 snapshot을 수정하지 않고 새 snapshot을 발행한다.
- Audit event와 outbox event를 함께 기록한다.

---

## 8. 데모 API와 접근 제한

데모 제한은 `/demo/api`와 Playground에만 적용한다.

### 제공

- `GET /demo/api/cultural-snapshot`
  - 현재 서버 관리 snapshot ID
  - 적용 Artifact의 표시용 claim과 condition
  - `read_only: true`

### 제공하지 않음

- 데모용 create/revise/withdraw/publish endpoint
- Playground의 문화 메모리 추가·삭제·발행 control
- 브라우저 session에서 `/v1` 관리 권한으로 승격하는 경로

정식 `/v1` API와 embedded library 기능은 이 제한의 영향을 받지 않는다.

---

## 9. Lotte Agent 통합

`_execute_demo_chat()`에서 Run을 연 뒤 해당 Run에 pin된 Cultural Snapshot payload를 사용한다. 활성 pointer를 다시 조회하지 않는다.

Agent 입력은 다음 순서를 갖는다.

```text
[시스템 안전·도구 정책]
[문화적 메모리 — tenant/scope의 published snapshot]
[개인 장기 메모리 — Fact/Preference/Episode/Conversation recall]
[사용자 질문]
```

충돌 우선순위:

1. 시스템 안전 정책과 도구 권한
2. Published Cultural Snapshot
3. 개인 Preference와 다른 장기 메모리
4. 과거 Conversation

Cultural Artifact는 도구 사용 권한을 추가하거나 system safety policy를 완화할 수 없다.

실행 결과의 `memory_trace.cultural`은 다음을 반환한다.

```json
{
  "status": "applied",
  "count": 2,
  "label": "문화적 기억",
  "detail": "서버가 발행한 읽기 전용 문화적 스냅샷을 적용했습니다.",
  "snapshot_id": "csp_...",
  "scope": "demo/default"
}
```

---

## 10. Playground UI

### Memory sidebar

- `문화적 메모리` 읽기 전용 view를 추가한다.
- 서버 기본 Artifact를 카드로 표시한다.
- 카드에는 `문화 규칙`, condition과 `읽기 전용` badge만 표시한다.
- 추가, 삭제, 수정, 발행 control은 렌더링하지 않는다.
- `사용자 기억 삭제` dialog의 대상 수량에 Cultural Artifact를 포함하지 않는다.

### 실행 추적

- 적용 여부
- Artifact 수
- snapshot ID
- scope
- `서버 관리 · 읽기 전용` 상태

Run 전에는 일반 설명만 표시하고, Run 후에는 실제 pin된 snapshot 정보를 표시한다.

---

## 11. 구현 순서

### 단계 1 — Contract와 domain

- Cultural Artifact/Snapshot value object와 validation
- ContextBundle 확장
- Repository port와 domain error
- deterministic digest unit test

### 단계 2 — Persistence

- In-memory adapter
- SQLite migration과 repository
- tenant isolation, immutable write와 active pointer test

### 단계 3 — Library use case

- create/revise/withdraw/publish/resolve
- embedded facade
- no-culture fallback 호환성

### 단계 4 — SaaS API

- request/response schema
- `/v1` endpoint와 role 분리
- audit/outbox event
- OpenAPI contract test

### 단계 5 — Agent Run 통합

- `open_run()` snapshot pin
- ContextBundle payload
- Lotte Agent prompt 주입
- memory trace 확장

### 단계 6 — 데모 seed와 UI

- 브라우저 tenant별 서버 소유 sample seed
- read-only demo endpoint
- sidebar view와 실행 trace
- 사용자 기억 삭제와 완전 분리

### 단계 7 — 배포와 검증

- 기존 SQLite volume migration
- local E2E
- 원격 container rebuild
- public Playground behavior 검증
- snapshot rollback rehearsal

---

## 12. 테스트 계획

### Unit

- 같은 manifest가 같은 digest를 생성한다.
- Published Artifact와 Snapshot을 수정할 수 없다.
- 조건과 scope validation이 tenant 경계를 벗어나지 않는다.
- withdrawn Artifact는 새 snapshot에 포함되지 않는다.

### Adapter contract

- In-memory와 SQLite가 같은 cultural repository suite를 통과한다.
- active pointer 전환 실패 시 이전 snapshot이 유지된다.
- 서로 다른 tenant의 Artifact와 Snapshot을 조회할 수 없다.

### Application integration

- active snapshot이 없는 Run은 `csp_none_*`를 유지한다.
- active snapshot이 있는 Run은 실제 snapshot과 Artifact를 pin한다.
- Run 도중 새 snapshot을 발행해도 기존 Run context는 변경되지 않는다.
- 새 Run은 새 snapshot을 사용한다.
- 개인 Preference가 Cultural Artifact를 덮어쓰지 못한다.

### API

- `culture:read`, `culture:write`, `culture:publish` 권한이 분리된다.
- `/v1` 관리 API는 정상적으로 CRUD/version/publish를 수행한다.
- `/demo/api`에는 mutation route가 없다.
- 데모 browser tenant는 다른 tenant 문화 데이터를 조회하지 못한다.

### Playground E2E

- 기본 문화적 메모리가 별도 입력 없이 표시된다.
- 문화 카드에 편집·삭제 control이 없다.
- 사용자 기억 삭제 후에도 문화 카드와 snapshot이 유지된다.
- 독도 질문에서 sample Artifact가 실제 Agent 입력과 응답에 반영된다.
- 실행 추적에 `applied`, artifact 수와 snapshot ID가 표시된다.
- 새 대화에서도 동일 snapshot이 다시 적용된다.

---

## 13. 완료 기준

- Library와 `/v1` API에서 Cultural Artifact lifecycle과 snapshot 발행이 실제 동작한다.
- 기존 no-culture 사용자는 코드 변경 없이 계속 동작한다.
- Agent Run이 snapshot ID와 payload를 고정하며 재현할 수 있다.
- 데모 사용자는 문화적 메모리를 변경할 수 없다.
- 서버 기본 Cultural Snapshot이 모든 새 데모 browser tenant에 자동 주입된다.
- 개인 장기 메모리 삭제가 Cultural Artifact/Snapshot을 삭제하지 않는다.
- Lotte Agent 답변과 실행 추적에서 문화적 메모리 적용을 확인할 수 있다.
- In-memory, SQLite, API와 Playground E2E test가 통과한다.
- 원격 배포 후 `/health`, `/ready`, Playground와 `/v1` contract를 검증한다.

---

## 14. 예상 변경 파일

```text
src/mnemome/contracts.py
src/mnemome/ports.py
src/mnemome/application.py
src/mnemome/facade.py
src/mnemome/adapters/memory.py
src/mnemome/adapters/sqlite.py
src/mnemome/service/schemas.py
src/mnemome/service/app.py
src/mnemome/service/demo.py
src/mnemome/service/static/index.html
src/mnemome/service/static/app.js
src/mnemome/service/static/app.css
tests/test_cultural_memory.py
tests/test_service_api.py
tests/test_lotte_integration.py
```

구현 중 실제 module 경계가 달라질 경우 public contract와 repository port를 유지한 채 세부 파일만 조정한다.
