# 04. Cultural Memory 기능 상세

상위 문서: [Cultural Memory & Collective Intelligence](../cultural-memory-hivemind.md)

## 1. 목적

이 문서는 Cultural Memory 계층 안의 여섯 핵심 기능을 상세히 설명한다.

1. Meme Artifact 식별과 명세화
2. 독립 검증과 lifecycle governance
3. Cultural Transmission, policy selection, recovery
4. 통제된 지식 확산과 전략 다양성 보존
5. Variant formation, lineage, withdrawal
6. 안전과 책임 경계

각 기능은 별도 책임을 가지지만 provenance, evidence independence, baseline, scope라는 공통 경계를 공유한다.

---

## 2. 전체 기능 클래스 다이어그램

```mermaid
classDiagram
    class MemeArtifact {
        claim
        applicability_conditions
        exclusion_conditions
        failure_boundary
        baseline_procedure
        recovery_policy
        provenance
    }
    class ArtifactSpecification {
        completeness
        testability
        scope
    }
    class ValidationCase {
        context
        baseline_result
        variant_result
    }
    class EvidenceGroup {
        independence_key
        correlation_reason
    }
    class Evaluation {
        dimension
        judgment
        uncertainty
    }
    class LifecycleDecision {
        status
        rationale
        restrictions
    }
    class TransmissionPolicy {
        allowed_population
        rollout_boundary
        stop_condition
    }
    class PolicySelection {
        agent_context
        selection_reason
    }
    class RecoveryRecord {
        failure_signal
        checkpoint
        recovery_outcome
    }
    class MemeVariant {
        parent
        change_summary
        status
    }
    class MemeLineage {
        ancestry
        derivation_type
    }
    class WithdrawalDecision {
        trigger
        affected_descendants
        action
    }
    class SafetyBoundary {
        privacy
        permission
        capability
        provenance
    }

    MemeArtifact o-- ArtifactSpecification
    MemeArtifact o-- ValidationCase
    ValidationCase --> EvidenceGroup
    ValidationCase o-- Evaluation
    MemeArtifact --> LifecycleDecision
    LifecycleDecision --> TransmissionPolicy
    TransmissionPolicy --> PolicySelection
    PolicySelection --> RecoveryRecord
    MemeArtifact <|-- MemeVariant
    MemeVariant --> MemeLineage
    MemeArtifact --> WithdrawalDecision
    WithdrawalDecision --> MemeLineage : traces_impact
    SafetyBoundary ..> ArtifactSpecification
    SafetyBoundary ..> ValidationCase
    SafetyBoundary ..> TransmissionPolicy
    SafetyBoundary ..> WithdrawalDecision
```

---

## 3. Meme Artifact 식별과 명세화

### 3.1 식별 기준

모든 Knowledge Artifact가 Meme Artifact는 아니다. 다음 질문에 모두 답할 수 있을 때만 Proposed Meme Variant로 명세한다.

1. 무엇이 다른 Agent에게 전달되는가?
2. Parent 또는 Baseline과 무엇이 다른가?
3. 어느 context에서 유효한가?
4. 어느 context에서는 사용하면 안 되는가?
5. 어떤 observation을 실패로 판단하는가?
6. 실패하면 어떤 procedure로 복귀하는가?
7. 어떤 source episode와 transformation에서 왔는가?
8. 다른 Agent가 같은 기준으로 재현할 수 있는가?

### 3.2 명세 구조

| 필드 | 설명 | 필수 이유 |
| --- | --- | --- |
| Claim | Artifact가 유효하다고 주장하는 내용 | 평가 대상을 고정함 |
| Expanded Form | Shortcut이 압축한 원래 경로 | 의미와 생략된 단계를 설명함 |
| Applicability Conditions | 사용할 수 있는 조건 | 무분별한 일반화를 막음 |
| Exclusion Conditions | 사용하면 안 되는 조건 | 위험한 context를 명시함 |
| Failure Boundary | 실패로 볼 observation | runtime 중 중단 판단을 가능하게 함 |
| Baseline Procedure | Artifact 없이 수행하는 원래 절차 | 비교와 복구 기준을 제공함 |
| Recovery Policy | 실패 후 안전한 복귀 절차 | 비가역적인 shortcut을 막음 |
| Provenance | Source, transformation, authoring context | 책임과 오염 경로를 추적함 |
| Evaluation Plan | 검증할 차원과 test case | popularity 대신 재현 가능한 판단을 만듦 |
| Lineage | Parent, derivation, related variant | 상관 근거와 descendant를 추적함 |

### 3.3 활동 다이어그램

```mermaid
flowchart TD
    Start([반복 pattern · 의도적 proposal · exploratory result]) --> Unit[전달되는 단위를 평문으로 정의]
    Unit --> Baseline[현재 Baseline Procedure 식별]
    Baseline --> Difference[Pattern과 baseline의 차이 명시]
    Difference --> Conditions[적용·제외 조건 작성]
    Conditions --> Failure[Failure Boundary와 Recovery 작성]
    Failure --> Source[Source episode와 transformation 연결]
    Source --> Test[Testable claim과 evaluation plan 작성]
    Test --> Complete{필수 명세가 완전한가?}
    Complete -->|아니오| Pattern[Pattern 또는 Knowledge Artifact로 유지]
    Complete -->|예| Candidate[Proposed Meme Variant 생성]
    Candidate --> Quarantine[Under Validation으로 격리]
    Pattern --> End([명세 종료])
    Quarantine --> End
```

### 3.4 Shortcut 예시

`A → B → C → D → E`를 `A ⇒ E`로 줄이는 shortcut이라면 다음을 명시해야 한다.

- B, C, D를 생략해도 되는 조건
- E가 baseline과 동등한 결과인지 확인하는 관찰
- 생략된 단계가 담당하던 safety check
- Shortcut 실패 시 A 또는 안전 checkpoint에서 baseline으로 돌아가는 절차
- 단축된 step 수 외에 accuracy와 recoverability를 비교할 test

---

## 4. 독립 검증과 Lifecycle Governance

검증과 토론은 Online Execution이 아니라 별도의 Cultural Deliberation Workspace에서 비동기로 수행한다. Reviewer는 blind phase에서 먼저 독립 판단을 제출하고, review freeze 이후에만 서로의 판단을 비교한다. 시스템 컴포넌트와 session protocol은 [Cultural Deliberation 시스템 설계](./08-cultural-deliberation-system.md)를 따른다.

### 4.1 독립성의 단위

Agent 이름이 다르다는 이유만으로 독립 검증이 되지는 않는다. 최소한 다음 상관관계를 확인해야 한다.

- 같은 source episode를 사용했는가?
- 같은 Parent Meme 또는 descendant를 사용했는가?
- 같은 test data 또는 environment를 사용했는가?
- 서로의 결론을 본 뒤 판단했는가?
- 같은 generation process 또는 prompt를 공유했는가?

상관관계가 있으면 결과를 버리지 않고 하나의 Evidence Group으로 묶는다. 독립성은 evidence 개수를 줄이기 위한 벌점이 아니라 확신을 과대평가하지 않기 위한 구조다.

### 4.2 Lifecycle 상태

| 상태 | 의미 | Agent에게 제공 여부 |
| --- | --- | --- |
| Proposed | 명세가 제출되었으나 검토 전 | 제공하지 않음 |
| Under Validation | 격리된 상태에서 평가 중 | 일반 Agent에게 제공하지 않음 |
| Validated | 정의된 scope와 조건 안에서 근거가 충분함 | 조건과 함께 제한 제공 |
| Restricted | 일부 context 또는 subpopulation에만 허용 | 명시된 범위에서만 제공 |
| Revision Required | 문제를 수정한 descendant가 필요함 | 기존 상태에 따라 중단 또는 제한 |
| Rejected | Claim 또는 안전 경계가 부적합함 | 제공하지 않음 |
| Withdrawn | 이전에 제공되었으나 더 이상 사용하면 안 됨 | 신규 사용 중단, 영향 추적 |

### 4.3 활동 다이어그램

```mermaid
flowchart TD
    Start([Under Validation Candidate]) --> Session[Deliberation Session과 Candidate version 고정]
    Session --> Independence[Validator, source, context 독립성 설계]
    Independence --> Blind[Blind independent review]
    Blind --> Freeze[Review freeze 후 결과 공개]
    Freeze --> Debate[Structured debate와 evidence request]
    Debate --> NeedTest{추가 실험이 필요한가?}
    NeedTest -->|예| Baseline[같은 조건에서 baseline 실행]
    Baseline --> Variant[Candidate 실행]
    Variant --> Dimensions[각 Evaluation Dimension 기록]
    Dimensions --> Debate
    NeedTest -->|아니오| Group[Correlated result를 Evidence Group으로 묶음]
    Group --> Counter[Counterexample과 safety signal 검토]
    Counter --> Decision{Governance 판단}
    Decision -->|근거 충분| Validate[Validated 또는 Restricted]
    Decision -->|수정 가능| Revise[Revision Required]
    Decision -->|근거 부족| Hold[Under Validation 유지]
    Decision -->|명확한 오류| Reject[Rejected]
    Decision -->|기존 승인에 중대한 반례| Withdraw[Withdrawn]
    Revise --> NewVariant[새 Meme Variant 작성]
    NewVariant --> Start
    Validate --> End([현재 검증 cycle 종료])
    Hold --> End
    Reject --> End
    Withdraw --> End
```

### 4.4 판단 원칙

- 하나의 종합 점수만으로 승인하지 않는다.
- Safety와 permission violation은 효율 향상으로 상쇄하지 않는다.
- Generalization evidence가 부족하면 scope를 좁혀 Restricted로 둘 수 있다.
- Negative result와 counterexample을 success evidence와 같은 lineage에 연결한다.
- Parent의 검증 상태를 descendant에 자동 상속하지 않는다.

---

## 5. Cultural Transmission, Policy Selection, Recovery

### 5.1 세 책임의 분리

| 책임 | 주체 | 질문 |
| --- | --- | --- |
| Transmission | Cultural Memory governance | 어느 population과 scope에 artifact를 제공할 수 있는가? |
| Policy Selection | 현재 Agent | 지금 Query와 context에서 사용할 것인가? |
| Recovery | Agent Control Loop | 조건이 깨졌을 때 어떻게 baseline으로 복귀할 것인가? |

Cultural Memory가 Validated로 판단했다고 해서 모든 Agent가 사용해야 하는 것은 아니다. Validation은 정의된 조건 안에서 사용할 자격이고, selection은 현재 context에서의 local decision이다.

### 5.2 활동 다이어그램

```mermaid
flowchart TD
    Start([Validated Artifact]) --> Scope[Transmission scope와 restrictions 확인]
    Scope --> Offer[Eligible Agent에 candidate 제공]
    Offer --> Context[Agent가 현재 context와 conditions 비교]
    Context --> Select{Artifact를 사용할 것인가?}
    Select -->|아니오| Baseline[Baseline Procedure 선택]
    Select -->|예| Pin[Conditions, failure boundary, baseline을 Working Memory에 기록]
    Pin --> Execute[Artifact를 포함한 Plan 실행]
    Baseline --> Execute
    Execute --> Observe[Observation 확인]
    Observe --> Violation{조건 이탈 또는 failure인가?}
    Violation -->|아니오| Done{Goal 완료인가?}
    Done -->|아니오| Execute
    Done -->|예| Record[Usage outcome 기록]
    Violation -->|예| Stop[Artifact 경로 중단]
    Stop --> Recover[안전 checkpoint에서 baseline 복귀]
    Recover --> Record
    Record --> Feedback[Outcome, selection reason, counterexample 연결]
    Feedback --> End([실행 종료])
```

### 5.3 Runtime latency 경계

- 후보 retrieval은 실행 준비 시 한 번 수행한다.
- 선택한 artifact의 조건은 Working Memory에 저장한다.
- Step loop에서는 local condition check를 수행한다.
- Usage feedback은 Response 이후 Cultural Learning Loop에 연결한다.
- Goal 또는 capability가 근본적으로 바뀐 re-plan에서만 예외적으로 새 후보를 요청한다.

---

## 6. 통제된 지식 확산과 전략 다양성 보존

### 6.1 목적

검증된 artifact도 population 전체에 즉시 확산하면 conformity bias와 correlated failure가 커질 수 있다. Cultural Transmission은 adoption을 최대화하는 과정이 아니라 **유효성을 재검증하면서 확산 반경을 조절하는 과정**이다.

### 6.2 Subpopulation 설계 원칙

- 서로 다른 strategy와 evidence source를 유지한다.
- 일부 subpopulation은 baseline 또는 alternative variant를 계속 사용한다.
- 같은 lineage의 결과를 여러 subpopulation의 독립 합의로 계산하지 않는다.
- 새 revision은 parent의 popularity를 상속하지 않는다.
- 높은 사용률을 correctness로 해석하지 않는다.

### 6.3 활동 다이어그램

```mermaid
flowchart TD
    Start([Validated 또는 Restricted Artifact]) --> Seed[제한된 Subpopulation 선택]
    Seed --> Preserve[Baseline과 alternative strategy 보존]
    Preserve --> Trial[각 Subpopulation에서 local trial]
    Trial --> Outcomes[Outcome, context, source 수집]
    Outcomes --> Correlation[Lineage와 source correlation 분석]
    Correlation --> Compare[성과와 strategy diversity 비교]
    Compare --> Decision{확산 범위를 바꿀 것인가?}
    Decision -->|근거 개선| Expand[다음 scope로 제한적 확장]
    Decision -->|혼합 결과| Maintain[현재 scope 유지]
    Decision -->|전략 수렴 위험| Diversify[Alternative strategy 비율 확대]
    Decision -->|위해 또는 반복 실패| Contract[Scope 축소 또는 중단]
    Expand --> Trial
    Diversify --> Trial
    Contract --> Review[Lifecycle 재검토]
    Maintain --> End([현재 transmission cycle 종료])
    Review --> End
```

### 6.4 관찰 지표

| 지표 | 해석 |
| --- | --- |
| Independent success rate | 독립 context에서 artifact가 재현되는 정도 |
| Failure concentration | 특정 subpopulation 또는 context에 실패가 몰리는지 |
| Strategy diversity | Alternative strategy가 유지되는지 |
| Recovery success | 실패 후 baseline으로 실제 복귀하는지 |
| Adoption concentration | 특정 source나 lineage에 과도하게 집중되는지 |
| Counterexample coverage | 알려진 실패 경계가 검증 범위에 포함되는지 |

---

## 7. Variant Formation, Lineage, Withdrawal

### 7.1 Variant가 필요한 경우

- Applicability condition을 좁히거나 넓힘
- Failure boundary를 새롭게 발견함
- Baseline Procedure 또는 Recovery Policy를 개선함
- 다른 context에 맞게 재맥락화함
- Parent의 claim 일부를 반례에 맞게 수정함
- 여러 parent의 요소를 recombination함

기존 artifact의 오탈자처럼 의미와 평가 결과에 영향을 주지 않는 수정은 표현 revision으로 처리할 수 있다. Claim, conditions, behavior가 달라지면 새 Meme Variant가 필요하다.

### 7.2 Lineage 관계

```mermaid
classDiagram
    class MemeLineage {
        lineage_id
        root
    }
    class MemeVariant {
        variant_id
        status
        change_summary
    }
    class Derivation {
        type
        rationale
        inherited_constraints
    }
    class EvidenceSet {
        independence_groups
        counterexamples
    }
    class WithdrawalImpact {
        affected_variants
        affected_decisions
        remediation
    }

    MemeLineage o-- MemeVariant
    MemeVariant "1" --> "0..*" MemeVariant : parent_of
    MemeVariant o-- EvidenceSet
    Derivation --> MemeVariant : parent
    Derivation --> MemeVariant : descendant
    WithdrawalImpact --> MemeLineage : analyzes
```

### 7.3 활동 다이어그램

```mermaid
flowchart TD
    Start([변경 필요성 발견]) --> Parent[Parent artifact와 status 고정]
    Parent --> Reason[Failure, new context, improvement reason 기록]
    Reason --> Change[Claim, conditions, baseline 변경점 명세]
    Change --> Variant[새 Meme Variant 생성]
    Variant --> Lineage[Parent와 derivation 관계 연결]
    Lineage --> Inherit[계승할 provenance와 safety constraint 지정]
    Inherit --> Reset[Validation state를 새로 시작]
    Reset --> Validate[독립 검증]
    Validate --> Failure{중대한 반례가 parent에도 영향을 주는가?}
    Failure -->|아니오| End([Variant lifecycle 계속])
    Failure -->|예| Impact[Parent와 descendant impact 분석]
    Impact --> Action{필요한 조치}
    Action -->|일부 조건 문제| Restrict[Scope 제한과 재검증]
    Action -->|명확한 위해| Withdraw[관련 artifact 회수]
    Action -->|표현 문제| Correct[Artifact 표현 수정]
    Restrict --> End
    Withdraw --> End
    Correct --> End
```

### 7.4 Withdrawal 원칙

- Artifact 하나만 숨기고 끝내지 않는다.
- Descendant가 잘못된 claim이나 evidence를 계승했는지 확인한다.
- 해당 artifact를 근거로 내린 lifecycle decision을 재검토한다.
- 이미 실행된 usage record와 outcome은 삭제하지 않고 회수 이유와 연결한다.
- Privacy 삭제 요구와 provenance 보존이 충돌하면 식별 정보를 제거하되 필요한 audit relation을 최소화해 유지한다.

---

## 8. 안전과 책임 경계

### 8.1 경계 종류

| 경계 | 핵심 질문 |
| --- | --- |
| Privacy | 개인 episode나 민감 정보가 population으로 노출되는가? |
| Permission | Source 정보를 다른 scope로 전달할 권한이 있는가? |
| Capability | Artifact가 Agent에게 새로운 권한을 부여하거나 우회하게 하는가? |
| Provenance | Source와 transformation을 추적할 수 있는가? |
| Instruction Integrity | 외부 instruction이 검증 없이 cultural knowledge로 승격되는가? |
| Accountability | 승인, revision, withdrawal의 이유와 책임을 설명할 수 있는가? |

### 8.2 활동 다이어그램

```mermaid
flowchart TD
    Start([Artifact 또는 contribution 수신]) --> Source[Source와 transformation 확인]
    Source --> Provenance{Provenance가 충분한가?}
    Provenance -->|아니오| Claim[검증된 지식이 아닌 untrusted claim으로 유지]
    Provenance -->|예| Privacy{민감 정보가 포함되는가?}
    Privacy -->|예| Sanitize[비식별화 또는 scope 축소]
    Sanitize --> Privacy
    Privacy -->|아니오| Permission{전달 permission이 있는가?}
    Permission -->|아니오| Reject[전이 거부]
    Permission -->|예| Capability{새 권한 또는 safety 우회가 필요한가?}
    Capability -->|예| Reject
    Capability -->|아니오| Injection{외부 instruction을 무비판적으로 계승하는가?}
    Injection -->|예| Quarantine[격리와 별도 검증]
    Injection -->|아니오| Accept[다음 lifecycle 단계로 수용]
    Claim --> End([경계 판단 종료])
    Reject --> End
    Quarantine --> End
    Accept --> End
```

### 8.3 책임 원칙

- Artifact는 기존 Agent와 Tool의 권한 범위를 넓히지 않는다.
- Validation은 permission grant가 아니다.
- 외부 source의 instruction은 evidence일 수 있으나 자동 policy가 아니다.
- Source가 없거나 변환 이력이 끊긴 artifact는 검증된 지식이 아니라 claim으로 취급한다.
- Withdrawal 결정은 이유, 영향 범위, 복구 조치와 함께 기록한다.

---

## 9. 기능 간 통합 절차

```mermaid
flowchart LR
    Identify["식별"] --> Specify["명세"]
    Specify --> Validate["독립 검증"]
    Validate --> Govern["Lifecycle 판단"]
    Govern --> Transmit["제한적 전달"]
    Transmit --> Select["Agent policy selection"]
    Select --> Observe["Usage와 outcome"]
    Observe --> Revise["Variant formation"]
    Observe --> Withdraw["Withdrawal 판단"]
    Revise --> Validate
    Withdraw --> Impact["Lineage impact 분석"]
    Safety["Safety boundary"] -.-> Specify
    Safety -.-> Validate
    Safety -.-> Transmit
    Safety -.-> Withdraw
    Diversity["Strategy diversity"] -.-> Transmit
    Diversity -.-> Govern
```

어느 한 기능도 단독으로 artifact를 population-level knowledge로 만들 수 없다. 명세, 독립 검증, lifecycle 판단, 안전 경계와 실제 사용 feedback이 연결되어야 한다.

---

## 10. 기능 설계 검토 체크리스트

- [ ] Artifact의 claim과 baseline을 비교할 수 있는가?
- [ ] Conditions, exclusion, failure, recovery가 구분되어 있는가?
- [ ] Evidence의 독립성과 correlation reason을 설명할 수 있는가?
- [ ] Lifecycle 상태마다 Agent 제공 여부가 명확한가?
- [ ] Transmission과 Agent policy selection이 분리되어 있는가?
- [ ] Runtime loop가 Cultural Memory에 Step마다 접근하지 않는가?
- [ ] Subpopulation과 alternative strategy가 유지되는가?
- [ ] Revision이 parent를 덮어쓰지 않는가?
- [ ] Withdrawal이 descendant와 decision impact를 추적하는가?
- [ ] Safety violation이 다른 성과 차원으로 상쇄되지 않는가?
