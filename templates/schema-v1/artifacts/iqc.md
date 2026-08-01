---
id: "{{IQC_ID}}"
micro_spec: "{{MICRO_SPEC_ID}}"
micro_spec_commit: "{{MICRO_SPEC_COMMIT}}"
implementation_commit: "{{IMPLEMENTATION_COMMIT}}"
result: draft
---

# IQC: {{TITLE}}

## Target

{{TODO: 검증 대상 Micro-SPEC과 구현 동작 및 검증 경계를 설명한다}}

## Checks

| 검사 | 실행 명령 또는 방법 | Exit code·판정 | 결과 요약 | Evidence |
| --- | --- | --- | --- | --- |
| {{TODO: 검사 이름}} | {{TODO: 실제 실행한 명령 또는 검사 방법}} | {{TODO: exit code 또는 PASS/FAIL}} | {{TODO: 관찰 결과}} | {{NEEDS_EVIDENCE: 안정적인 저장소 경로 또는 외부 참조}} |

## Criteria Results

| AC | 판정 | 근거 |
| --- | --- | --- |
| `{{AC_ID}}` | {{TODO: passed, failed 또는 blocked}} | {{NEEDS_EVIDENCE: 해당 AC 판정의 직접 근거}} |

## Result

- 전체 판정: {{TODO: frontmatter의 result와 일치하는 판정을 작성한다}}
- 요약: {{TODO: 판정 이유와 남은 blocker 또는 필요한 재작업을 작성한다}}
