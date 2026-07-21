# 04. 도메인 모델과 Lifecycle

## 1. Bounded Context

| Context | Aggregate root | 핵심 invariant |
| --- | --- | --- |
| Identity | Tenant, Principal | Principal의 tenant membership 없이 tenant resource 접근 불가 |
| Agent Interface | Agent, AgentDescriptorVersion | 외부 Agent descriptor와 protocol capability는 versioned |
| Interaction | AgentRun | Mnemome은 외부 실행을 관찰하며 inference를 수행하지 않음 |
| Working Memory | WorkingContext | Run과 snapshot version이 고정됨 |
| Long-Term Memory | Episode, MemoryFact | Derived fact는 source와 transformation을 가짐 |
| Workspace | Workspace, WorkspaceTask | Member와 visibility policy 없이 contribution 불가 |
| Culture | Meme, MemeArtifact, CulturalSnapshot | Published version은 immutable |
| Deliberation | Candidate, DeliberationSession | Review freeze 전 peer review 비공개 |
| Experiment | ExperimentPlan | Metric과 stop condition freeze 후 arm 실행 |
| Evaluation | EvaluationTask | Subject, rubric, input과 evaluator version을 고정한 뒤 실행 |
| Governance | GovernanceDecision | Decision은 Candidate version과 evidence set을 고정 참조 |
| Compliance | PrivacyRequest | 삭제와 derived impact가 완료되어야 terminal state |

---

## 2. 핵심 클래스 다이어그램

```mermaid
classDiagram
    class Tenant {
        tenant_id
        policy_version
        status
    }
    class Principal {
        principal_id
        type
        status
    }
    class Agent {
        agent_id
        owner_scope
    }
    class AgentDescriptorVersion {
        version
        protocol_capabilities
        endpoint_mode
    }
    class Run {
        run_id
        status
        snapshot_version
        agent_descriptor_version
    }
    class WorkingContext {
        context_version
        declared_plan_ref
        observations
        checkpoint
    }
    class Episode {
        episode_id
        scope
        outcome
        retention
    }
    class MemoryFact {
        fact_id
        statement
        confidence
        status
    }
    class SourceRef {
        source_type
        source_id
        span
    }
    class Workspace {
        workspace_id
        visibility
    }
    class WorkspaceContribution {
        contribution_id
        kind
        author
    }
    class Meme {
        meme_id
        operational_boundary
    }
    class MemeArtifact {
        artifact_id
        version
        status
        conditions
        baseline
    }
    class LineageEdge {
        relation
        parent_version
        child_version
    }
    class Candidate {
        candidate_id
        version
        status
    }
    class DeliberationSession {
        session_id
        phase
        session_version
    }
    class Evidence {
        evidence_id
        type
        result
    }
    class EvidenceGroup {
        group_id
        correlation_reason
    }
    class EvaluationTask {
        task_id
        subject_version
        spec_version
        status
    }
    class JudgeProfile {
        judge_id
        kind
        independence_key
    }
    class EvaluationResult {
        result_id
        verdict
        uncertainty
    }
    class GovernanceDecision {
        decision_id
        status
        scope
        rationale
    }
    class CulturalSnapshot {
        snapshot_version
        published_at
    }

    Tenant o-- Principal
    Tenant o-- Agent
    Agent o-- AgentDescriptorVersion
    AgentDescriptorVersion --> Run
    Run o-- WorkingContext
    Run --> Episode
    Episode o-- SourceRef
    Episode --> MemoryFact : supports
    MemoryFact o-- SourceRef
    Workspace o-- WorkspaceContribution
    WorkspaceContribution o-- SourceRef
    Meme "1" o-- "1..*" MemeArtifact
    MemeArtifact o-- LineageEdge
    Candidate --> MemeArtifact : proposes
    Candidate "1" --> "0..*" DeliberationSession
    DeliberationSession o-- Evidence
    EvidenceGroup o-- Evidence
    DeliberationSession o-- EvaluationTask
    EvaluationTask --> JudgeProfile
    EvaluationTask o-- EvaluationResult
    DeliberationSession --> GovernanceDecision
    GovernanceDecision --> MemeArtifact : changes_status
    CulturalSnapshot o-- MemeArtifact
```

---

## 3. Entity와 Value Object

### Entity

시간에 따라 상태가 바뀌며 identity로 추적한다.

- Tenant, Principal, Agent, Run
- Episode, MemoryFact
- Workspace, WorkspaceTask, Contribution
- Meme, MemeArtifact, Candidate
- DeliberationSession, ReviewAssignment
- ExperimentPlan, GovernanceDecision
- EvaluationSpec, EvaluationTask, JudgeProfile, EvaluationResult
- CulturalSnapshot, PrivacyRequest

### Value Object

값 전체로 동등성을 판단하며 가능한 한 immutable하게 둔다.

- Scope: tenant/user/agent/workspace/population
- CapabilitySet
- ApplicabilityCondition
- FailureBoundary
- BaselineProcedure
- RecoveryPolicy
- SourceSpan
- EvidenceIndependenceKey
- EvaluationDimensionResult
- RetentionPolicy
- SnapshotVersion

---

## 4. 외부 AgentRun lifecycle

```mermaid
stateDiagram-v2
    [*] --> Created
    Created --> ContextPreparing: authorization and recall
    ContextPreparing --> Active: ContextBundle issued
    ContextPreparing --> Failed: preparation failure
    Active --> Active: external Agent event
    Active --> CancelRequested: cooperative signal
    CancelRequested --> Cancelled: Agent acknowledgement
    CancelRequested --> Active: Agent declines or resumes
    Active --> Completed: Agent reports outcome
    Active --> Failed: Agent reports failure
    Active --> SuspectedAbandoned: heartbeat expiry
    SuspectedAbandoned --> Active: Agent reconnects
    SuspectedAbandoned --> Abandoned: retention policy closes
    Completed --> EpisodePending
    Cancelled --> EpisodePending
    Failed --> EpisodePending
    Abandoned --> EpisodePending
    EpisodePending --> Finalized: episode event recorded
    Finalized --> [*]
```

`Completed`, `Cancelled`, `Failed`, `Abandoned`는 session terminal state다. Mnemome의 cancel은 협력적 signal이며 외부 process 종료를 보장하지 않는다. Episode finalization 실패는 외부 Agent가 이미 반환한 Response를 취소하지 않는다.

---

## 5. Memory fact lifecycle

```mermaid
stateDiagram-v2
    [*] --> Extracted
    Extracted --> Active: source and scope validated
    Extracted --> Rejected: invalid or unsafe
    Active --> Corrected: counterevidence
    Corrected --> Superseded: replacement active
    Active --> Superseded: newer fact
    Active --> Expired: retention or staleness
    Active --> Redacted: privacy action
    Corrected --> Redacted
    Superseded --> Redacted
    Expired --> [*]
    Redacted --> [*]
```

Fact relation:

- `SUPPORTED_BY`: Fact → SourceRef
- `CONTRADICTS`: Fact ↔ Fact
- `SUPERSEDES`: New Fact → Old Fact
- `REFINES`: Specific Fact → General Fact
- `DERIVED_FROM`: Fact → Episode/Fact set

---

## 6. Workspace task lifecycle

```mermaid
stateDiagram-v2
    [*] --> Open
    Open --> InProgress
    InProgress --> Blocked
    Blocked --> InProgress
    InProgress --> Decided
    Decided --> Completed
    Decided --> Reopened: new evidence
    Reopened --> InProgress
    Open --> Cancelled
    InProgress --> Cancelled
    Completed --> Archived
    Cancelled --> Archived
    Archived --> [*]
```

Workspace Decision은 cultural truth가 아니다. `Decided`는 현재 workspace task의 coordination outcome일 뿐이다.

---

## 7. Cultural lifecycle

```mermaid
stateDiagram-v2
    [*] --> Proposed
    Proposed --> UnderValidation: candidate qualified
    UnderValidation --> Validated: governance approval
    UnderValidation --> Restricted: scope-limited approval
    UnderValidation --> RevisionRequired
    UnderValidation --> Rejected
    RevisionRequired --> Proposed: child version
    Validated --> Restricted: new boundary
    Restricted --> Validated: broader evidence
    Validated --> Deprecated: better alternative
    Restricted --> Deprecated
    Validated --> Withdrawn: critical counterexample
    Restricted --> Withdrawn
    Deprecated --> UnderValidation: revalidation
    Withdrawn --> Proposed: revised child
    Rejected --> [*]
```

Status 변경은 existing row overwrite가 아니라 decision append와 new current status projection으로 표현한다.

---

## 8. Aggregate transaction boundary

| Command | Transaction 안에서 변경 | Event |
| --- | --- | --- |
| OpenAgentRun | AgentRun, ContextBundle, initial event, outbox | AgentRunOpened |
| AppendAgentEvent | RunEvent, WorkingContext version | AgentEventRecorded |
| CompleteAgentRun | AgentRun terminal state, outbox | AgentRunCompleted |
| FinalizeEpisode | Episode, SourceRef, outbox | EpisodeRecorded |
| SubmitWorkspaceContribution | Contribution, EvidenceRef, outbox | WorkspaceContributionSubmitted |
| QualifyCandidate | Candidate version, outbox | CandidateQualified |
| SealReview | IndependentReview, assignment state, outbox | IndependentReviewSealed |
| RecordEvaluationResult | JudgeRun, EvaluationResult, outbox | EvaluationResultRecorded |
| RecordGovernanceDecision | Decision, artifact status projection, lineage, outbox | GovernanceDecisionRecorded |
| PublishSnapshot | Snapshot metadata, current pointer, outbox | CulturalSnapshotPublished |

Cross-aggregate action은 event-driven process manager로 이어간다.
