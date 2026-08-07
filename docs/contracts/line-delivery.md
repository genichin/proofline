# Line lifecycle 계약

이 문서는 Line artifact의 lifecycle과 문서 schema만 정의한다. Discovery는 [Discovery 계약](discovery.md), REQ와 AC는 [REQ와 AC 계약](requirements-and-criteria.md)을 따른다.

## Line lifecycle

각 Line은 다음 고정 경로의 artifact 하나를 가진다.

```text
.proofline/lines/line-<NNNN>/line-<NNNN>.md
```

`execution_status`는 다음 값만 사용한다.

```text
not_started
in_progress
verifying
delivered
cancelled
```

| Status | 의미 |
| --- | --- |
| `not_started` | Line이 생성됐지만 작업을 시작하지 않음 |
| `in_progress` | 승인된 REQ 범위의 작업을 진행 중 |
| `verifying` | 결과가 REQ와 AC를 만족하는지 확인 중 |
| `delivered` | Line의 작업이 완료됨 |
| `cancelled` | 완료 전에 Line을 중단함 |

기본 transition은 다음과 같다.

```text
not_started → in_progress → verifying → delivered
                 ↑            │
                 └────────────┘

not_started → cancelled
in_progress → cancelled
verifying   → cancelled
```

- 새 Line은 `not_started`로 시작한다.
- 작업을 시작하면 `in_progress`, 확인 단계에 들어가면 `verifying`으로 전환한다.
- 재작업이 필요하면 `verifying → in_progress`로 전환할 수 있다.
- Discovery 또는 REQ가 `withdrawn`이면 완료되지 않은 Line을 `cancelled`로 전환한다.
- `delivered`와 `cancelled`는 terminal status이다.
- 상태 전환 권한과 실제 작업·확인 절차는 적용 프로젝트가 소유한다. ProofLine은 이를 대신 수행하거나 승인하지 않는다.

## Validator 경계

ProofLine validator는 현재 canonical Line, Discovery, REQ와 AC의 구조·reference·상태 일관성을 검사한다. Git commit 순서, branch·merge 방식, implementation 또는 delivery chronology를 보증하지 않으며 특정 구현이나 배포가 실제로 완료됐음을 인증하지 않는다.

Line status는 프로젝트가 기록한 lifecycle 선언이다. `proofline validate` 성공을 구현 품질, 테스트 통과, 통합 안전성 또는 배포 완료의 증거로 사용해서는 안 된다.

## Line 문서 schema

```yaml
---
id: line-0001
execution_status: not_started
---
```

필수 field는 `id`와 `execution_status`이다. `id`는 파일명과 상위 Line directory 번호에 일치해야 한다. Line artifact는 frontmatter-only이며 닫는 delimiter 뒤에는 공백과 마지막 newline 외의 내용을 기록하지 않는다.

## Opaque retained data

과거 `.proofline/`에 남아 있는 현재 범위 밖 artifact와 이전 Line metadata는 opaque retained data로 보존할 수 있다. 기존 Line의 `implementation_history` field는 deprecated optional metadata로 읽되 값을 해석하지 않는다. 현재 contract는 이를 생성·변환하지 않으며 Line transition의 조건이나 validator 보증으로 사용하지 않는다.
