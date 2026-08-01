---
id: "{{DQC_ID}}"
line: "{{LINE_ID}}"
candidate_commit: "{{CANDIDATE_COMMIT}}"
result: draft
---

# DQC: {{TITLE}}

## Target

{{TODO: 검증 대상 Line, integration candidate 및 전체 검증 경계를 설명한다}}

## IQC Results

| Micro-SPEC | IQC | 판정 | Binding 확인 |
| --- | --- | --- | --- |
| `{{MICRO_SPEC_ID}}` | `{{IQC_ID}}` | {{TODO: passed, failed 또는 blocked}} | {{TODO: exact commit binding 확인 결과}} |

## Checks

| 검사 | 실행 명령 또는 방법 | Exit code·판정 | 결과 요약 | Evidence |
| --- | --- | --- | --- | --- |
| {{TODO: Line 전체 검사 이름}} | {{TODO: 실제 실행한 명령 또는 검사 방법}} | {{TODO: exit code 또는 PASS/FAIL}} | {{TODO: 회귀·충돌·범위 검사 결과}} | {{NEEDS_EVIDENCE: 안정적인 저장소 경로 또는 외부 참조}} |

## Criteria Results

| AC | 판정 | 근거 |
| --- | --- | --- |
| `{{AC_ID}}` | {{TODO: passed, failed 또는 blocked}} | {{NEEDS_EVIDENCE: 해당 AC의 종합 판정 근거}} |

## Result

- 전체 판정: {{TODO: frontmatter의 result와 일치하는 판정을 작성한다}}
- 요약: {{TODO: main 통합 gate 요청 가능 여부, blocker 및 필요한 재작업을 작성한다}}
