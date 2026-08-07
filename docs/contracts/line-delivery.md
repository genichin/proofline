# Line identity 계약

이 문서는 Line artifact의 identity와 문서 schema를 정의한다. Discovery는 [Discovery 계약](discovery.md), REQ와 AC는 [REQ와 AC 계약](requirements-and-criteria.md)을 따른다.

## Line identity

Line은 하나의 변경 논의에서 Discovery와 REQ를 묶는 안정적인 identity다. 각 Line은 다음 고정 경로의 artifact 하나를 가진다.

```text
.proofline/lines/line-<NNNN>/line-<NNNN>.md
```

Line은 작업, 검증, 통합 또는 delivery 상태를 소유하지 않는다. 이러한 운영 상태와 절차는 적용 프로젝트가 필요에 따라 별도 시스템에서 관리하며 ProofLine validator의 보증 범위가 아니다.

## Line 문서 schema

```yaml
---
id: line-0001
---
```

필수 field는 `id` 하나이다. `id`는 파일명과 상위 Line directory 번호에 일치해야 한다. Line artifact는 frontmatter-only이며 닫는 delimiter 뒤에는 공백과 마지막 newline 외의 내용을 기록하지 않는다.

## Opaque retained metadata

기존 Line의 `execution_status`와 `implementation_history` field는 deprecated optional metadata로 읽되 값이나 transition을 해석하지 않는다. 현재 writer와 template은 두 field를 생성하지 않으며 validator는 이를 작업 완료, 검증 또는 delivery의 증거로 사용하지 않는다. 기존 bytes를 소급 변환하거나 삭제할 필요는 없다.
