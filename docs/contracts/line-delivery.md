# Line identity 계약

이 문서는 Line artifact의 identity와 문서 schema를 정의한다. Discovery는 [Discovery 계약](discovery.md), REQ와 AC는 [REQ와 AC 계약](requirements-and-criteria.md)을 따른다.

## Line identity

Line은 하나의 변경 논의에서 Discovery와 REQ를 묶는 안정적인 identity다. 각 Line은 다음 고정 경로의 artifact 하나를 가진다.

```text
.proofline/lines/line-<NNNN>/line-<NNNN>.md
```

Line은 작업, 검증, 통합 또는 delivery 상태를 보증하지 않는다. 선택 `status`와 활동 본문은 사람이 현재 진행 단계와 최근 활동을 빠르게 확인하기 위한 정보 표시이며 완료 증거 또는 gate가 아니다.

## Line 문서 schema

```yaml
---
id: line-0001
status: discovery
---
```

필수 field는 `id` 하나이며 `status`는 선택 field이다. `status`의 권장 값은 `discovery`, `specification`, `implementation`, `verification`, `release`, `retired`이고 일반적인 표시 순서는 이 목록의 순서다. Validator는 값이나 transition을 검사하지 않으며 field가 없으면 validation을 실패시키지 않는 `line.status.missing` warning만 출력한다. `id`는 파일명과 상위 Line directory 번호에 일치해야 한다.

Line은 활동 로그가 생성된 경우 최근 활동 요약과 `evidence/activity-log.md` 상대 링크를 담은 선택 Markdown 본문을 가질 수 있다. 본문과 링크의 존재·최신성·내용은 validator warning 또는 error의 입력이 아니다.

## Opaque retained metadata

기존 Line의 `execution_status`와 `implementation_history` field는 deprecated optional metadata로 읽되 값이나 transition을 해석하지 않는다. 현재 writer와 template은 두 field를 생성하지 않으며 validator는 이를 작업 완료, 검증 또는 delivery의 증거로 사용하지 않는다. 기존 bytes를 소급 변환하거나 삭제할 필요는 없다.
