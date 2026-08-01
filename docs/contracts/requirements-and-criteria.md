# REQ와 AC 계약

이 문서는 REQ가 소유하는 AC 변경 집합, REQ와 AC 및 Micro-SPEC의 binding, REQ·AC lifecycle과 문서 schema를 정의한다. 경로와 identity는 [산출물 디렉터리 구조](../artifact-layout.md), 공통 문서 형식은 [문서 형식과 완결성](document-format.md)을 따른다.

## REQ와 AC 변경 집합

REQ는 해당 Line의 전체 변경 범위를 AC ID로 선언한다.

```yaml
---
id: req-0001
status: draft
discovery: dcy-0001
criteria:
  create:
    - ac-0001
  update:
    - ac-0003
  retire: []
---
```

규칙은 다음과 같다.

- `create`는 이 Line에서 새로 도입하는 AC이다.
- `update`는 같은 stable AC 파일의 현재 내용을 변경하는 AC이다.
- `retire`는 더 이상 현재 사양으로 적용하지 않을 AC이다.
- 같은 AC를 하나의 REQ 안에서 둘 이상의 변경 종류에 동시에 기록하지 않는다.
- REQ에는 AC 본문을 복제하지 않는다.
- REQ의 AC 변경 집합은 해당 Line의 모든 Micro-SPEC이 따라야 하는 승인 범위의 source of truth이다.
- AC 상세 내용은 각 `.proofline/criteria/ac-<NNNN>.md` 파일이 소유한다.

## AC 파일

AC는 프로젝트 전체에서 안정적인 ID와 고정 경로를 갖는 최소 canonical 사양이다.

```yaml
---
id: ac-0003
status: draft
---
```

```markdown
# 로그인 세션 유지 시간

## Criterion

로그인 세션은 마지막 사용자 활동 후 15분 동안 유지되어야 한다.

## Verification

- 15분 이전에는 세션이 유효해야 한다.
- 15분 이후에는 인증이 거부되어야 한다.
```

- AC 하나는 독립적으로 변경하고 검증할 수 있는 하나의 criterion을 표현한다.
- 새로운 독립 조건은 기존 AC에 누적하지 않고 새 AC identity로 분리한다.
- 같은 사양 축의 값이나 조건을 변경하는 경우 stable AC 파일을 같은 경로에서 수정한다.
- 승인 전의 AC 내용은 candidate 사양이며 구현 기준인 canonical specification baseline이 아니다.
- REQ 승인 Git revision의 AC 내용이 해당 Line의 canonical specification baseline이 된다.
- 승인된 AC는 구현 완료 여부와 관계없이 구현이 따라야 할 사양으로 효력을 갖는다.
- 과거 AC 내용과 이전 승인 baseline은 Git history에서 확인한다.

## Micro-SPEC과 REQ binding

Micro-SPEC의 직접적인 source of truth는 같은 Line의 REQ이다.

```yaml
---
id: ms-0001-001
spec_status: draft
implementation_status: not_started
parent_req: req-0001
criteria:
  - ac-0001
  - ac-0002
---
```

AC는 사양의 atomic unit이고 Micro-SPEC은 기술적 구현 unit이므로 두 분해 경계는 반드시 일치하지 않는다. AC와 Micro-SPEC은 N:M 관계를 사용한다.

```text
하나의 AC         → 하나 이상의 Micro-SPEC이 공동으로 구현 가능
하나의 Micro-SPEC → 하나 이상의 AC를 함께 구현 가능
```

규칙은 다음과 같다.

- `parent_req`는 같은 Line의 유일한 REQ여야 한다.
- Micro-SPEC은 parent REQ가 선언한 AC 중 자신이 담당하는 하나 이상의 AC를 `criteria`에 명시해야 한다.
- Micro-SPEC의 모든 AC는 parent REQ의 `create`, `update`, `retire` 합집합에 포함되어야 한다.
- Micro-SPEC은 REQ에 없는 AC나 제품 동작을 임의로 추가할 수 없다.
- 하나의 AC를 여러 Micro-SPEC이 공동으로 담당할 수 있다.
- 하나의 Micro-SPEC이 서로 관련된 여러 AC를 함께 담당할 수 있다.
- parent REQ가 선언한 모든 대상 AC는 최소 하나의 Micro-SPEC에 배정되어야 한다.
- 같은 AC를 여러 Micro-SPEC이 참조하더라도 AC 내용의 source of truth는 `.proofline/criteria/`의 단일 AC 파일이다.
- 독립적인 구현·검증 경계를 가진 AC를 하나의 Micro-SPEC에 불필요하게 묶지 않는다.
- 새로운 사양이 구현 중 발견되면 Micro-SPEC에 바로 추가하지 않고 AC와 REQ 범위를 먼저 갱신한다.

집합 불변식은 다음과 같다.

```text
각 Micro-SPEC의 criteria
⊆ parent REQ의 (create ∪ update ∪ retire)

해당 REQ에 속한 모든 Micro-SPEC criteria의 합집합
= parent REQ의 (create ∪ update ∪ retire)
```

관계 예시는 다음과 같다.

```text
req-0001
├── ac-0001 ──→ ms-0001-001
├── ac-0002 ──→ ms-0001-001
└── ac-0003 ──→ ms-0001-001
             └→ ms-0001-002
```

## REQ specification status

REQ의 `status`는 specification governance만 표현하며 다음 세 값만 허용한다.

```text
draft
approved
withdrawn
```

각 상태의 의미는 다음과 같다.

| Status | 의미 | 구현 시작 |
| --- | --- | --- |
| `draft` | REQ와 대상 AC를 작성·검토·수정하는 중이며 승인 baseline이 아님 | 금지 |
| `approved` | REQ와 대상 AC exact bytes가 승인되어 specification baseline을 형성함 | approval commit에서 허용 |
| `withdrawn` | 해당 REQ 변경 계약을 더 이상 진행하지 않음 | 금지 또는 중단 |

허용 transition은 다음과 같다.

```text
draft ───────→ approved
  │                │
  └──→ withdrawn   ├──→ withdrawn
                   │
                   └──→ draft ──→ approved
                        의미 변경    재승인
```

규칙은 다음과 같다.

- `draft → approved` transition을 기록한 main governance commit이 specification baseline을 생성한다.
- `approved` REQ의 의미 또는 대상 AC의 PASS/FAIL 결과를 바꾸려면 영향받는 구현을 중단하고 REQ를 먼저 `draft`로 전환해야 한다.
- 변경된 REQ와 `draft` AC는 함께 검토한다. REQ가 `draft → approved` transition을 거치면 대상 AC는 변경 종류에 따라 `active` 또는 `retired`로 확정되고, 해당 commit이 새로운 specification baseline이 된다.
- 재승인 전까지 이전 `approved` Git revision은 마지막 승인 baseline으로 보존되지만, 변경 중인 `draft` revision을 구현 기준으로 사용할 수 없다.
- `draft` 또는 `approved` REQ는 `withdrawn`으로 전환할 수 있다.
- `withdrawn`은 terminal status이다. 같은 변경을 다시 진행하려면 독립적인 새 Line과 새 Discovery/REQ를 만든다.
- `approved` 상태를 유지한 채 REQ 또는 대상 AC의 의미를 변경하는 것은 허용하지 않는다.
- implementation branch는 `approved` transition을 기록한 exact main commit에서만 생성할 수 있다.
- 구현·검증·delivery 진행 상태는 REQ `status`에 기록하지 않는다.

다음 값은 REQ `status`가 아니다.

```text
implementing
tested
verified
delivered
```

이 값들이 필요하다면 별도의 execution state vocabulary에서 정의한다.

## AC lifecycle status

AC의 `status`는 해당 AC revision의 canonical specification lifecycle을 표현하며 다음 세 값만 허용한다.

```text
draft
active
retired
```

| Status | 의미 |
| --- | --- |
| `draft` | REQ에서 생성 또는 수정 대상으로 검토 중인 candidate AC |
| `active` | 승인된 REQ baseline에 포함된 현재 canonical 사양 |
| `retired` | 승인된 REQ에 의해 폐기되어 더 이상 적용되지 않는 사양 |

허용 transition은 다음과 같다.

```text
새 AC       draft ─────────→ active
기존 AC     active → draft → active
기존 AC     active ────────→ retired
```

규칙은 다음과 같다.

- REQ의 `create` 또는 `update` 대상인 `draft` AC는 해당 REQ가 승인될 때 `active`가 된다.
- 기존 `active` AC의 의미를 변경하려면 변경 REQ와 함께 AC를 `draft`로 전환하고 재검토해야 한다.
- 변경 중인 AC가 `draft`인 동안 이전 `active` Git revision이 마지막 승인 baseline으로 유지된다.
- REQ의 `retire` 대상인 `active` AC는 해당 REQ가 승인될 때 `retired`가 된다.
- 승인 전에 새 AC 도입을 철회하면 아직 승인된 적 없는 `draft` AC 파일을 제거한다.
- 기존 AC 수정이 철회되면 candidate `draft` revision을 버리고 마지막 `active` revision을 복원한다.
- `retired`는 terminal status이다. 같은 사양이 다시 필요하면 새 AC identity를 만든다.
- `implemented`, `tested`, `passed`, `failed`는 AC `status`가 아니다.

## REQ 문서 schema

### Frontmatter

```yaml
---
id: req-0001
status: draft
discovery: dcy-0001
criteria:
  create: []
  update: []
  retire: []
---
```

필수 field:

```text
id
status
discovery
criteria
criteria.create
criteria.update
criteria.retire
```

- `criteria.create`, `criteria.update`, `criteria.retire`는 각각 빈 list일 수 있지만 세 field를 모두 명시해야 한다.
- 세 list의 합집합에는 최소 하나의 AC가 있어야 한다.
- 같은 AC를 둘 이상의 list에 중복해서 기록할 수 없다.
- `discovery`는 같은 Line의 유일한 Discovery를 가리켜야 한다.

### Markdown 본문

필수 본문 schema는 다음과 같다.

```markdown
# 제목

## Objective

## Scope

## Non-Goals
```

각 section의 canonical 의미는 다음과 같다.

| Section | 소유하는 사실 |
| --- | --- |
| `Objective` | 이번 Line에서 전달할 결과 |
| `Scope` | 승인되는 Line-level 구현 범위 |
| `Non-Goals` | 이번 delivery에서 구현하지 않을 내용 |

다음 H2는 필요할 때만 `Scope`와 `Non-Goals` 사이에 추가할 수 있는 유일한 선택 section이다.

```markdown
## Constraints
```

REQ 본문은 AC의 Criterion 또는 Verification 내용을 복제하지 않는다.

## AC 문서 schema

### Frontmatter

```yaml
---
id: ac-0001
status: draft
---
```

필수 field:

```text
id
status
```

AC의 변경 이력을 나타내는 `introduced_by`, `governing_req`, `last_updated_by` 같은 reverse reference는 최소 필수 field가 아니다. REQ의 AC 변경 집합과 Git history가 해당 관계와 이력을 소유한다.

### Markdown 본문

필수 본문 schema는 다음과 같다.

```markdown
# 제목

## Criterion

## Verification
```

| Section | 소유하는 사실 |
| --- | --- |
| `Criterion` | 독립적으로 PASS/FAIL 가능한 하나의 normative specification |
| `Verification` | Criterion을 판정할 관찰 또는 검사 방법 |

AC의 `Verification`은 판정 방법을 정의하며 실행 결과를 저장하지 않는다.
