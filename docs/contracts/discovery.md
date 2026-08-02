# Discovery 계약

이 문서는 Discovery의 specification 상태, transition, 문서 schema 및 확인 조건을 정의한다. 공통 문서 형식과 placeholder 규칙은 [문서 형식과 완결성](document-format.md)을 따른다.

## Discovery specification status

Discovery의 `status`는 문제와 변경 필요성에 대한 governance 상태만 표현하며 다음 세 값만 허용한다.

```text
draft
confirmed
withdrawn
```

각 상태의 의미는 다음과 같다.

| Status | 의미 | REQ 승인 |
| --- | --- | --- |
| `draft` | 문제, 배경, 범위 및 위험을 탐색·검토·수정하는 중 | 금지 |
| `confirmed` | 문제와 변경 필요성이 확인되어 REQ의 근거로 사용할 수 있음 | 허용 |
| `withdrawn` | 해당 Discovery를 더 이상 진행하지 않음 | 금지 또는 중단 |

허용 transition은 다음과 같다.

```text
draft ───────→ confirmed
  │                 │
  └──→ withdrawn    ├──→ withdrawn
                    │
                    └──→ draft ──→ confirmed
                         의미 변경    재확인
```

규칙은 다음과 같다.

- `confirmed`된 Discovery만 같은 Line의 REQ를 `approved`로 전환할 수 있는 근거가 된다.
- Discovery를 `confirmed`로 전환하려면 Open Question의 `Status`를 포함하여 문서 전체에 governance placeholder가 없어야 한다. 남아 있는 `deferred` Open Question은 명시적인 `Owner`와 `Exit Condition`을 가져야 한다.
- Discovery가 `draft`이면 같은 Line의 REQ는 작성할 수 있지만 `approved`로 전환할 수 없다.
- `confirmed`된 Discovery의 문제, 범위 또는 변경 의도를 의미 있게 변경하려면 Discovery를 먼저 `draft`로 전환하고 다시 검토·확인해야 한다.
- Discovery의 의미 변경이 이미 승인된 REQ의 근거에 영향을 주면 영향받는 구현을 중단하고 REQ도 `draft`로 전환하여 대상 AC와 함께 재검토한 뒤 REQ를 재승인하고 AC lifecycle을 다시 확정해야 한다.
- `draft` 또는 `confirmed` Discovery는 `withdrawn`으로 전환할 수 있다.
- Discovery가 `withdrawn`이면 같은 Line의 REQ를 새로 승인할 수 없으며, 이미 승인된 REQ와 진행 중인 구현은 중단하고 REQ를 `withdrawn`으로 전환해야 한다.
- `withdrawn`은 terminal status이다. 같은 변경을 다시 진행하려면 독립적인 새 Line과 새 Discovery/REQ를 만든다.
- Discovery의 `confirmed`는 REQ 승인, 구현 완료, 검증 또는 delivery 완료를 의미하지 않는다.

## Discovery 문서 schema

### Frontmatter

```yaml
---
id: dcy-0001
status: draft
---
```

필수 field:

```text
id
status
```

### Markdown 본문

필수 본문 schema는 다음과 같다.

```markdown
# 제목

## Problem

## Evidence

## Scope

## Out of Scope

- 이번 Discovery에서 의도적으로 제외하는 범위 1
- 이번 Discovery에서 의도적으로 제외하는 범위 2
```

각 section의 canonical 의미는 다음과 같다.

- `Problem`: 해결해야 할 문제
- `Evidence`: 문제가 실제로 존재한다는 근거
- `Scope`: 이번 Discovery가 다루는 범위
- `Out of Scope`: 의도적으로 제외하는 범위

`Out of Scope`는 Markdown table이 아닌 unordered list로 작성한다. 서로 독립적인 제외 범위는 각각 별도 list item으로 기록한다. 명시적으로 제외할 범위가 없으면 `- 없음`이라고 기록한다.

다음 H2는 필요할 때만 `Out of Scope` 뒤에 추가할 수 있는 유일한 선택 section이다.

```markdown
## Risks and Unknowns
```

Open Question이 있으면 `Risks and Unknowns` 아래에 다음 H3를 사용한다.

```markdown
### Open Questions

- `OQ-001`
  - Type: `DECIDE`
  - Status: {{TODO: API 호환성 정책을 결정해야 함}}
  - Question: 기존 API 호환성을 유지해야 하는가?
  - Owner: product owner
  - Exit Condition: 호환성 정책을 명시적으로 결정하고 그 결과를 Scope 또는 Out of Scope에 반영한다.

- `OQ-002`
  - Type: `DATA`
  - Status: `deferred`
  - Question: 실제 장치의 최대 처리 지연은 얼마인가?
  - Owner: verification
  - Exit Condition: Line verification에서 장치 측정 결과를 기록한다.
```

Open Question은 별도 artifact나 frontmatter metadata로 만들지 않고 Discovery 본문이 소유한다. Markdown table은 사용하지 않으며, 각 질문을 최상위 unordered list item으로 기록하고 구조화된 field를 하위 list item으로 기록한다. 각 field의 규칙은 다음과 같다.

- `ID`는 같은 Discovery 안에서 `OQ-001`부터 시작하는 안정적인 local identity이다. ID를 renumber하거나 제거된 ID를 재사용하지 않는다.
- `Type`은 `DECIDE`, `CONFIRM`, `DATA` 중 하나여야 한다.
  - `DECIDE`는 책임 있는 사람 또는 authority의 product, policy, priority, scope 또는 risk 선택이 필요함을 뜻한다.
  - `CONFIRM`은 fact, interpretation 또는 boundary를 evidence로 확인해야 함을 뜻한다.
  - `DATA`는 측정이나 실행을 통해 후속 evidence를 수집해야 함을 뜻한다.
- `Status`는 필수 field이며 다음 중 정확히 하나여야 한다.
  - `{{TODO: ...}}`, `{{UNKNOWN: ...}}`, `{{NEEDS_EVIDENCE: ...}}` 중 하나의 governance placeholder: 아직 해소되지 않아 Discovery confirmation을 차단하는 질문
  - `answered`: 질문이 해소되고 답이 canonical owner section에 반영된 상태
  - `deferred`: REQ 의미를 바꾸지 않으며 명시적인 후속 단계에서 해소하도록 이관한 상태
- 미해결 질문의 성격에 따라 결정이나 작업이 필요하면 `TODO`, 필요한 사실을 아직 모르면 `UNKNOWN`, 근거가 필요하면 `NEEDS_EVIDENCE`를 사용한다.
- 미해결 질문의 `Status`는 하나의 governance placeholder 전체로 표현하며 placeholder 밖에 별도의 상태 문자열을 함께 쓰지 않는다.
- `Owner`는 질문을 결정·확인하거나 후속 evidence를 수집할 책임 주체 또는 단계를 명시한다.
- `Exit Condition`은 질문을 해소할 판정 조건이나 명시적인 후속 stage·artifact·evidence를 기록한다. `나중에 결정`, `추후 확인`처럼 대상과 조건이 없는 표현은 허용하지 않는다.

질문의 답에 따라 Discovery의 Scope, REQ의 대상 AC 집합 또는 동일 구현의 PASS/FAIL 결과가 달라질 수 있으면 `Status`를 governance placeholder로 유지해야 하며 `deferred`로 전환할 수 없다. Product·policy·compatibility·scope·risk acceptance 결정도 같은 규칙을 따른다.

REQ 의미를 바꾸지 않고 후속 단계에서만 얻을 수 있는 implementation detail이나 measurement evidence는 `deferred`로 둘 수 있다. 이 경우 해소할 stage, artifact 또는 evidence를 `Exit Condition`에 구체적으로 기록해야 한다.

Discovery를 `confirmed`로 전환할 때는 다음 gate를 모두 만족해야 한다.

```text
Open Question을 포함한 Discovery 전체의 governance placeholder = 0개
모든 Open Question에 필수 field 존재
모든 Open Question의 Status가 구조적으로 유효함
모든 deferred Open Question에 명시적인 Owner와 Exit Condition 존재
```

Open Question 전용 blocking gate를 별도로 두지 않는다. 일반 artifact 완결성 gate가 문서 전체의 `{{...}}`를 검사하므로 미해결 Open Question의 `Status` placeholder도 다른 미완성 내용과 함께 Discovery confirmation을 차단한다. 다만 `Status` field 삭제로 이 검사를 우회할 수 없도록 Open Question 구조 검증은 confirmation gate 전에 항상 수행한다.

질문에 답이 나오면 답을 해당 사실의 canonical owner section에 먼저 반영한 뒤 `Status`를 `answered`로 전환한다.

```text
fact 또는 근거 확인       → Evidence
포함 범위 결정            → Scope
제외 범위 결정            → Out of Scope
risk 또는 dependency      → Risks and Unknowns
Line delivery 목표        → REQ Objective
implementation 범위       → REQ Scope
atomic product behavior   → AC Criterion
판정 방법                 → AC Verification
```

REQ 작성 전에는 Scope에서 변경을 `create`, `update`, `retire`, 기존 active AC를 변경하지 않는 `satisfy`, release evidence 또는 Line 밖 housekeeping으로 분류한다. `create`를 선택할 때는 가장 가까운 active AC와 `update` 가능성, version independence, 독립 PASS/FAIL 및 장기 active 가치를 검토한다. 분류가 불명확하면 confirmation을 차단하는 Open Question으로 기록하며 사용자가 최종 semantic 판단을 소유한다.

`answered`는 Discovery confirmation을 차단하지 않는다. 현재 상태 중심의 문서를 유지하려면 답의 반영을 검토한 뒤 해당 Open Question 항목을 제거할 수 있으며, 질문과 해소 이력은 Git history가 보존한다. 항목을 유지하더라도 답의 canonical source of truth는 `Evidence`, `Scope`, `Out of Scope` 또는 그 밖의 해당 owner section이며 Open Question에 답을 중복 기록하지 않는다.

질문이 더 이상 유효하지 않으면 그에 따른 canonical owner section의 변경을 먼저 반영한 뒤 해당 list item을 제거한다. 별도의 `dropped` 상태는 두지 않으며 제거 이력은 Git history가 보존한다.

무엇을 물어야 할지조차 아직 작성하지 못한 일반 drafting placeholder와 구조화된 Open Question을 혼동하지 않는다. Open Question은 ID, Type, Status, Question, Owner 및 Exit Condition을 모두 가져야 하며, 이 중 `Status` placeholder만 그 질문이 아직 confirmation을 차단한다는 상태를 소유한다. Confirmed Discovery에는 governance placeholder가 없어야 하지만 명시적으로 `deferred`되거나 이미 `answered`된 Open Question은 남을 수 있다.

Discovery 결론은 `status: confirmed`가 소유하므로 별도 `Decision` section을 두지 않는다.
