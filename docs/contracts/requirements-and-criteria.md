# REQ와 AC 계약

이 문서는 REQ가 소유하는 AC 변경 집합, REQ·AC lifecycle과 문서 schema를 정의한다. 경로와 identity는 [산출물 디렉터리 구조](../artifact-layout.md), 공통 형식은 [문서 형식과 완결성](document-format.md)을 따른다.

## REQ와 AC 변경 집합

REQ는 해당 Line의 전체 변경 범위를 AC ID로 선언한다.

Confirmed Discovery에서 draft scaffold를 시작할 때는 exact `create`, `update`, `retire`, `satisfy` key를 가진 UTF-8 YAML/JSON manifest를 사용한다. `create`에는 중복 없는 한 줄 제목을, 나머지 list에는 중복·교차 overlap 없는 기존 active `ac-NNNN`을 기록한다.

```bash
proofline requirement init line-0001 --manifest admission.yaml --dry-run
proofline requirement init line-0001 --manifest admission.yaml
```

명령은 신규 AC ID, 모든 AC draft, REQ draft와 allocator 전진을 하나의 repository-local transaction으로 처리한다.

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
  satisfy:
    - ac-0004
---
```

- `create`는 이 Line에서 새로 도입하는 AC이다.
- `update`는 같은 stable AC 파일의 의미를 변경한다.
- `retire`는 더 이상 현재 사양으로 적용하지 않을 AC이다.
- `satisfy`는 의미를 변경하지 않고 충족할 기존 `active` AC이다. 해당 AC의 본문과 status는 변경하지 않는다.
- 같은 AC를 둘 이상의 변경 종류에 동시에 기록하지 않는다.
- REQ에는 AC 본문을 복제하지 않는다.
- AC 상세 내용은 `.proofline/criteria/ac-<NNNN>.md`가 소유한다.

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

- AC 하나는 독립적으로 판정할 수 있는 하나의 criterion을 표현한다.
- 새로운 독립 조건은 기존 AC에 누적하지 않고 새 identity로 분리한다.
- 같은 사양 축의 값을 변경하는 경우 stable AC 파일을 같은 경로에서 수정한다.
- `active` AC는 프로젝트가 지속적으로 만족해야 하는 version-independent product behavior 또는 constraint를 표현한다.
- 특정 release version, tag, commit, filename, checksum과 publication transaction은 AC가 아니다. 해당 프로젝트의 release 기록에 남긴다.
- 승인 전 AC는 candidate 사양이며 구현 기준인 canonical specification baseline이 아니다.
- Current canonical `approved` REQ와 그 대상 AC의 계약상 status·내용이 해당 Line의 specification baseline을 형성한다.
- Git history는 승인된 specification revision을 보존할 수 있지만 approval authority 또는 approval transition validation 입력이 아니다. 아래 `criteria.satisfy`의 historical active binding 검증은 별도 lifecycle 규칙으로 유지한다.

## REQ specification status

REQ의 `status`는 specification governance만 표현한다.

```text
draft
approved
withdrawn
```

| Status | 의미 |
| --- | --- |
| `draft` | REQ와 대상 AC를 작성·검토하는 중이며 승인 baseline이 아님 |
| `approved` | REQ와 대상 AC가 승인되어 specification baseline을 형성함 |
| `withdrawn` | 해당 변경 계약을 더 이상 진행하지 않음 |

```text
draft ───────→ approved
  │                │
  └──→ withdrawn   ├──→ withdrawn
                   └──→ draft → approved
```

- `confirmed` Discovery만 같은 Line의 REQ 승인 근거가 될 수 있다.
- 사용자만 REQ와 대상 AC의 의미를 승인할 수 있다.
- 사용자의 명시적 approval 뒤 REQ와 대상 AC를 계약상 status로 전환하고 current canonical tree를 검증한다.
- Approved REQ 또는 대상 AC의 의미를 바꾸려면 REQ를 `draft`로 전환해 함께 재검토하고 재승인한다.
- `withdrawn`은 terminal status이다. 같은 변경을 다시 진행하려면 새 Line과 Discovery/REQ를 만든다.
- 작업·검증·배포 상태는 REQ `status`에 기록하지 않는다.

## AC lifecycle status

AC의 `status`는 다음 세 값만 사용한다.

```text
draft
active
retired
```

| Status | 의미 |
| --- | --- |
| `draft` | 생성 또는 수정 대상으로 검토 중인 candidate AC |
| `active` | 승인된 REQ baseline에 포함된 현재 canonical 사양 |
| `retired` | 승인된 REQ에 의해 폐기된 사양 |

```text
새 AC    draft ─────────→ active
기존 AC  active → draft → active
기존 AC  active ────────→ retired
```

- `create` 또는 `update` 대상 draft AC는 REQ 승인과 함께 `active`가 된다.
- 기존 active AC의 의미를 변경하려면 변경 REQ와 함께 `draft`로 전환한다.
- `retire` 대상 active AC는 REQ 승인과 함께 `retired`가 된다.
- `satisfy` 대상은 해당 REQ 승인 시점에 `active`와 exact Criterion·Verification bytes를 유지한다. 이후 별도 approved REQ가 AC를 `retired`로 전환해도, main history에서 당시 active AC와 현재 approved REQ의 exact bytes를 함께 입증할 수 있는 과거 binding은 유효하다.
- 승인 전에 신규 AC 도입을 철회하면 승인된 적 없는 draft AC를 제거할 수 있다.
- 기존 AC 수정이 철회되면 마지막 active revision을 복원한다.
- `retired`는 terminal status이며 같은 사양이 다시 필요하면 새 AC identity를 만든다.

## REQ 문서 schema

```yaml
---
id: req-0001
status: draft
discovery: dcy-0001
criteria:
  create: []
  update: []
  retire: []
  satisfy: []
---
```

필수 field는 `id`, `status`, `discovery`, `criteria`와 네 criteria list이다. 각 list는 비어 있을 수 있지만 합집합에는 최소 하나의 AC가 있어야 하며 중복 ID는 허용하지 않는다. `discovery`는 같은 Line의 유일한 Discovery를 가리킨다. Schema version 1의 `criteria.satisfy`가 없는 과거 REQ는 호환성을 위해 그대로 읽을 수 있으며 소급 변환하지 않는다.

필수 본문 schema는 다음과 같다.

```markdown
# 제목

## Objective

## Scope

## Non-Goals
```

`Constraints`는 필요할 때 `Scope`와 `Non-Goals` 사이에 추가할 수 있는 유일한 선택 H2이다. REQ 본문은 AC의 Criterion 또는 Verification을 복제하지 않는다.

## AC 문서 schema

```yaml
---
id: ac-0001
status: draft
---
```

필수 field는 `id`와 `status`이다. 변경 관계와 과거 revision은 REQ의 AC 변경 집합과 Git history에 남으며 reverse-reference metadata를 추가하지 않는다.

```markdown
# 제목

## Criterion

## Verification
```

`Criterion`은 독립적으로 PASS/FAIL 가능한 normative specification, `Verification`은 이를 판정할 관찰 또는 검사 방법을 소유한다. 실행 결과는 AC에 저장하지 않는다.
