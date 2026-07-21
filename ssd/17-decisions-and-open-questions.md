# 17. ADR과 미결정 사항

## 1. 결정 상태

| 상태 | 의미 |
| --- | --- |
| Decided | 초기 구현의 기준으로 채택 |
| Provisional | prototype/benchmark로 검증할 가설 |
| Open | 구현 또는 상품화 전에 결정 필요 |
| Deferred | 초기 release에서 제외 |

---

## 2. Architecture Decision Register

| ID | 결정 | 상태 | 핵심 이유 |
| --- | --- | --- | --- |
| ADR-001 | 초기 service는 modular monolith + 분리 가능한 worker로 시작 | Decided | domain 경계를 유지하면서 운영 복잡도 제한 |
| ADR-002 | domain logic을 `mnemome-core` library에 둔다 | Decided | embedded, on-prem, hybrid, SaaS의 단일 의미론 |
| ADR-003 | application/service는 port-adapter 구조를 사용 | Decided | storage/Agent connector/Judge/identity 교체와 고객 환경 대응 |
| ADR-004 | PostgreSQL을 durable source of truth로 사용 | Decided | transaction, RLS, lineage와 운영 성숙도 |
| ADR-005 | Valkey는 ephemeral/cache state로만 사용 | Decided | cache loss와 durable correctness 분리 |
| ADR-006 | Lineage는 relational edge로 시작 | Decided | 초기 query와 운영 복잡도에 충분 |
| ADR-007 | 비동기 전달은 transactional outbox로 시작 | Decided | domain commit과 event 유실 방지 |
| ADR-008 | AgentRun session state stream은 SSE를 기본으로 사용 | Decided | 단방향 state update와 재연결 요구에 적합 |
| ADR-009 | Cultural Snapshot은 immutable하고 AgentRun이 pin | Decided | 재현성과 mid-session 변화 방지 |
| ADR-010 | Cultural Learning은 Agent interaction path와 비동기 분리 | Decided | latency와 failure isolation |
| ADR-011 | Deliberation은 sealed review 후 bounded typed argument protocol | Decided | 독립성, 비용과 auditability |
| ADR-012 | Evidence independence는 Agent 수가 아니라 source/model lineage로 계산 | Decided | correlated evidence의 중복 집계 방지 |
| ADR-013 | vector retrieval은 pgvector로 시작 | Provisional | primary DB와 단순 운영; 규모 benchmark 필요 |
| ADR-014 | SaaS management plane은 on-prem correctness에 필수 아님 | Decided | offline/air-gapped와 고객 통제 지원 |
| ADR-015 | SaaS/on-prem은 동일 Core와 conformance suite 사용 | Decided | product drift 방지 |
| ADR-016 | graph DB는 projection 필요가 측정될 때 도입 | Deferred | 초기 운영 비용 대비 이익 불명확 |
| ADR-017 | 범용 event broker는 outbox 처리량 한계가 확인될 때 도입 | Deferred | 불필요한 infrastructure 조기 도입 방지 |
| ADR-018 | Mnemome은 Agent inference, planning, tool execution과 사용자 응답을 제공하지 않음 | Decided | 제품 책임과 외부 Agent 생태계 경계 명확화 |
| ADR-019 | Agent/Workspace/Deliberation/Experiment에 상태형 Environment wrapper 제공 | Decided | phase, scope, version, visibility를 client에서 안전하게 표현 |
| ADR-020 | 내부 LLM 사용은 versioned EvaluationTask를 수행하는 bounded Judge로 제한 | Decided | 일반 Agent와 평가 inference의 책임 분리 |

---

## 3. 주요 결정 설명

### ADR-002 — Library-first Core

Core에는 entity/value object, lifecycle, policy, orchestration primitive와 port interface가 들어간다. HTTP, OIDC, multi-tenant routing, distributed queue와 UI는 service shell 책임이다. Core가 전역 network client, process environment 또는 특정 cloud SDK를 직접 참조하지 않게 한다.

### ADR-009 — Immutable Snapshot

AgentEnvironment가 매 event마다 Cultural Registry를 원격 조회하지 않고 session 시작 시 scope에 맞는 snapshot을 고정한다. 긴급 철회는 denylist로 즉시 막고 다음 snapshot으로 수렴한다.

### ADR-018 — Agent를 제공하지 않는 Service

Mnemome은 외부 Agent에 ContextBundle과 interface object를 제공하고 Agent가 제출한 event/outcome을 기록한다. Plan, inference, Tool과 Response는 Agent host가 소유한다. 내부 LLM Judge는 별도 EvaluationSpec, frozen input과 typed result를 가진 제한된 evaluator이므로 이 금지와 충돌하지 않는다.

### ADR-012 — Evidence independence

여러 Agent가 동일 문서, 동일 Episode, 동일 generated proposal이나 파생 Artifact를 사용했다면 겉으로 다른 의견도 상관된 evidence다. 따라서 reviewer headcount와 Evidence Group count를 분리해 기록한다.

---

## 4. 미결정 사항

### 4.1 Product와 tenancy

| ID | 질문 | 결정에 필요한 근거 |
| --- | --- | --- |
| OQ-001 | Tenant와 Cultural Population은 1:1인가? | enterprise hierarchy와 federation use case |
| OQ-002 | 개인 Agent memory를 조직으로 이전할 수 있는가? | consent, IP, 퇴사/이동 정책 |
| OQ-003 | 기본 retention 기간은 상품 tier인가 tenant policy인가? | 법무, 비용, 고객 조사 |
| OQ-004 | on-prem license 만료 시 허용 동작은? | 계약, data portability 원칙 |

### 4.2 Governance

| ID | 질문 | 결정에 필요한 근거 |
| --- | --- | --- |
| OQ-005 | 어떤 risk level에서 human approval을 강제할까? | domain별 harm model |
| OQ-006 | 자동 Governance가 허용되는 범위는? | false promotion/withdrawal 비용 |
| OQ-007 | Reviewer/Governor separation of duty 기본값은? | 고객 조직 규모와 규제 |
| OQ-008 | cross-tenant Meme 공유 모델은 export/import인가 federation인가? | provenance, privacy, business model |

### 4.3 Experiment와 evaluation

| ID | 질문 | 결정에 필요한 근거 |
| --- | --- | --- |
| OQ-009 | production traffic A/B를 언제 허용할까? | consent, risk, sample size |
| OQ-010 | metric별 최소 effect/uncertainty threshold는? | 실제 benchmark 분포 |
| OQ-011 | 외부 Agent model 또는 LLM Judge update가 기존 validation을 무효화하는 범위는? | model sensitivity 실험 |
| OQ-012 | strategy diversity의 최소 보존 규칙은? | population collapse 사례 |

### 4.4 Technology와 deployment

| ID | 질문 | 결정에 필요한 근거 |
| --- | --- | --- |
| OQ-013 | Python 외 언어용 Environment SDK/Core binding이 필요한가? | 고객 application stack |
| OQ-014 | pgvector의 tenant/scale 한계는 어디인가? | representative load benchmark |
| OQ-015 | outbox 이후 broker 선택 기준은? | throughput, ordering, customer stack |
| OQ-016 | 지원할 Kubernetes/PostgreSQL/Valkey 최소 version은? | release/maintenance policy |
| OQ-017 | hybrid management metadata의 기본 allowlist는? | 고객 보안 요구와 support 필요 |
| OQ-018 | Agent assignment 기본 transport는 pull, callback, queue 중 무엇인가? | Agent platform 통합 요구와 방화벽 제약 |
| OQ-019 | LLM Judge의 기본 provider/모델을 제공할지 고객 adapter만 지원할지? | 품질, 비용, data residency 요구 |

---

## 5. ADR 작성 template

```markdown
# ADR-NNN: 제목

- Status: Proposed | Accepted | Superseded | Rejected
- Date: YYYY-MM-DD
- Owners: ...
- Supersedes: ...

## Context
결정해야 하는 문제, 제약과 측정 근거

## Decision
채택한 선택과 명확한 경계

## Alternatives
검토한 대안과 선택하지 않은 이유

## Consequences
장점, 비용, migration과 운영 영향

## Validation
결정을 재검토할 metric, test와 시점
```

---

## 6. 결정 운영 규칙

- Domain invariant, public contract, data ownership 또는 deployment portability를 바꾸면 ADR을 작성한다.
- 단순 library version update는 동작/호환성 의미가 바뀌지 않으면 ADR이 아니다.
- Provisional 결정에는 검증 책임자, metric과 review date를 추가한다.
- ADR이 바뀌어도 과거 snapshot/run의 당시 decision과 policy version은 보존한다.
- on-prem 고객별 fork로 해결하지 않고 capability/configuration 또는 명시적 extension point로 일반화할 수 있는지 먼저 검토한다.
