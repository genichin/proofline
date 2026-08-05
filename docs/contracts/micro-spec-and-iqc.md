# Micro-SPEC과 IQC 계약

이 문서는 Micro-SPEC의 specification·implementation 상태, Micro-SPEC별 IQC 결과 및 두 artifact의 문서 schema를 정의한다. REQ·AC binding은 [REQ와 AC 계약](requirements-and-criteria.md), 공통 문서 형식은 [문서 형식과 완결성](document-format.md)을 따른다.

## Micro-SPEC status

Micro-SPEC은 계획 승인과 구현 진행을 혼합하지 않고 `spec_status`와 `implementation_status` 두 축으로 관리한다.

### Specification status

`spec_status`는 다음 세 값만 허용한다.

```text
draft
approved
withdrawn
```

| Status | 의미 |
| --- | --- |
| `draft` | 구현 범위와 방법을 작성·검토·수정하는 중 |
| `approved` | 구현 범위와 방법이 승인되어 구현을 시작할 수 있음 |
| `withdrawn` | 해당 Micro-SPEC을 더 이상 구현하지 않음 |

허용 transition은 다음과 같다.

```text
draft ───────→ approved
  │                │
  └──→ withdrawn   ├──→ withdrawn
                   │
                   └──→ draft ──→ approved
                        의미 변경    재승인
```

- parent REQ가 `approved`여야 Micro-SPEC을 `approved`로 전환할 수 있다.
- Micro-SPEC의 `spec_status`가 `approved`여야 구현을 시작할 수 있다.
- 승인된 구현 범위나 방법을 의미 있게 변경하려면 `approved → draft`로 전환하고 영향받는 구현을 중단한다.
- 변경된 Micro-SPEC을 재검토하여 다시 `approved`로 전환한 뒤 구현을 재개한다.
- `withdrawn`은 terminal status이다.

### Draft review와 approval ownership

후속 Line의 canonical choreography는 `A < H < S0 < S < P < I < Q`다. 구현 에이전트인 draft author는 Line worktree에서 clean exact draft commit `S0`를 만든 뒤 mutation을 멈춘다. 독립 specification reviewer는 exact `S0`의 substantive bytes, parent REQ·AC 범위와 criteria coverage를 read-only로 검토하고 PASS 또는 correction recommendation만 제공한다. Correction에는 fresh `S0`와 fresh review가 필요하다.

Review PASS 뒤 **사용자만** exact reviewed bytes의 approval authority를 가진다. Governance lead는 사용자 승인을 대신하지 않는 status-only recorder다. 구현 에이전트가 중지되고 clean branch HEAD가 exact `S0`임을 확인한 뒤 같은 worktree에서 `spec_status: draft → approved`만 기록한 `S`를 만들고, `S.parent=S0`와 substantive bytes 불변을 read-back한 다음 exact `S`를 구현 에이전트에게 handback한다. Self-approval, reviewer mutation, stale draft, concurrent mutation, lead 단독 approval과 substantive 변경이 섞인 approval은 허용하지 않는다.

Current-validator migration인 Line 0020만 bootstrap exception으로 REQ·AC·Micro-SPEC combined exact draft를 같은 독립 review와 user-only approval 뒤 main의 status-only commit `S=A`에서 승인한다. Chronology는 `S=A < H < P < I < Q`이며 exact `H` 뒤 `S0/S`를 다시 만들지 않는다.

### Implementation status

`implementation_status`는 다음 세 값만 허용한다.

```text
not_started
in_progress
implemented
```

허용 transition은 다음과 같다.

```text
not_started → in_progress → implemented
implemented → in_progress → implemented
              재작업
```

- `not_started`는 승인 여부와 별개로 구현 작업을 시작하지 않은 상태이다.
- `in_progress`는 코드, test 또는 관련 문서를 구현 중인 상태이다.
- `implemented`는 Micro-SPEC이 요구한 구현 변경을 완료한 상태이다.
- `implemented`는 검증 통과를 의미하지 않는다.
- `spec_status`가 `withdrawn`이면 진행 중인 구현을 중단하며 더 이상 implementation transition을 진행하지 않는다.
- 구현 검증 상태와 결과는 같은 Micro-SPEC의 IQC artifact가 소유한다.

예시는 다음과 같다.

```yaml
---
id: ms-0001-001
parent_req: req-0001
criteria:
  - ac-0001
  - ac-0002
spec_status: approved
implementation_status: in_progress
---
```

### IQC result

각 Micro-SPEC은 다음 고정 경로의 IQC artifact를 하나 가진다.

```text
.proofline/lines/line-<NNNN>/micro-specs/iqc-<NNNN>-<SSS>.md
```

`result`는 다음 네 값만 허용한다.

```text
draft
passed
failed
blocked
```

- `draft`는 검증을 준비하거나 결과를 작성 중인 상태이다.
- `passed`는 필수 검사를 실행하고 판정 기준을 만족한 상태이다.
- `failed`는 검사를 실행했으나 하나 이상의 필수 판정이 실패한 상태이다.
- `blocked`는 환경이나 필수 입력 문제로 검증을 완료하지 못한 상태이다.
- IQC는 exact Micro-SPEC commit과 실제 검증한 implementation commit을 함께 bind해야 한다.
- `implementation_history: first_parent` Line에서는 `micro_spec_commit`, 별도 persisted `in_progress`, `implementation_commit`, 후속 `implemented`/IQC candidate가 strict first-parent 순서여야 한다. IQC 작성 전에 `proofline validate`로 이 chronology를 확인한다.
- `implementation_commit`은 `in_progress` transition commit이나 second parent에만 존재하는 commit이 아니어야 한다. Rework는 fresh `in_progress` transition부터 다시 시작한다.
- 재검증할 때 같은 IQC 파일을 갱신하고 과거 결과는 Git history로 보존한다. Attempt별 IQC 파일은 만들지 않는다.
- 대용량 원시 log나 binary evidence는 canonical tree에 복사하지 않고 안정적인 저장소 경로나 외부 참조를 IQC에 기록한다. 장기 보존이 필요한 evidence는 가능한 경우 digest를 함께 기록한다.
- `passed`는 해당 Micro-SPEC 구현의 검증 통과만 의미하며 main 통합, Line delivery 또는 release를 승인하지 않는다.

## Micro-SPEC 문서 schema

### Frontmatter

```yaml
---
id: ms-0001-001
parent_req: req-0001
criteria:
  - ac-0001
spec_status: draft
implementation_status: not_started
---
```

필수 field:

```text
id
parent_req
criteria
spec_status
implementation_status
```

- `criteria`에는 최소 하나의 AC가 있어야 한다.
- `criteria`의 모든 AC는 parent REQ의 `create`, `update`, `retire`, `satisfy` 합집합에 포함되어야 한다.
- `parent_req`는 같은 Line의 유일한 REQ를 가리켜야 한다.

### Markdown 본문

필수 본문 schema는 다음과 같다.

```markdown
# 제목

## Scope

## Implementation

## Verification
```

| Section | 소유하는 사실 |
| --- | --- |
| `Scope` | 이 Micro-SPEC이 담당하는 기술적 경계 |
| `Implementation` | 변경할 component와 구현 작업 |
| `Verification` | 실행할 test와 검사 계획 |

Micro-SPEC의 목표는 H1 제목, `parent_req` 및 `criteria`가 함께 나타내므로 별도 `Goal` section을 두지 않는다. 변경 대상은 `Implementation`에 포함하므로 별도 `Changes` section을 두지 않는다. 구현 완료는 `implementation_status`가 소유하므로 별도 `Completion Conditions` section을 두지 않는다. Micro-SPEC의 `Verification`은 검증 계획이며 실제 실행 결과는 대응하는 IQC가 소유한다.

## IQC 문서 schema

### Frontmatter

```yaml
---
id: iqc-0001-001
micro_spec: ms-0001-001
micro_spec_commit: "<git-commit>"
implementation_commit: "<git-commit>"
result: draft
---
```

필수 field:

```text
id
micro_spec
micro_spec_commit
implementation_commit
result
```

- `id`의 `NNNN-SSS`는 `micro_spec`과 일치해야 한다.
- `micro_spec_commit`은 검증 기준으로 사용한 exact Micro-SPEC commit이다.
- `implementation_commit`은 실제 검증한 implementation commit이다.
- 두 commit field는 해당 저장소에서 해석되는 exact Git commit이어야 한다.

### Markdown 본문

필수 본문 schema는 다음과 같다.

```markdown
# IQC: 제목

## Target

## Checks

## Criteria Results

## Result
```

| Section | 소유하는 사실 |
| --- | --- |
| `Target` | 검증 대상 Micro-SPEC과 implementation의 설명 |
| `Checks` | 실제 실행한 test·검사와 결과 및 evidence 참조 |
| `Criteria Results` | Micro-SPEC이 담당하는 AC별 검증 결과 |
| `Result` | IQC 전체 판정과 필요한 설명 |

원시 log나 binary evidence를 본문에 복제하지 않는다. `Checks`에는 검증에 사용한 명령, exit code, 결과 요약과 안정적인 evidence 경로 또는 참조를 기록한다.
