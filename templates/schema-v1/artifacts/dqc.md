---
id: "{{DQC_ID}}"
line: "{{LINE_ID}}"
candidate_commit: "{{CANDIDATE_COMMIT}}"
result: draft
---

# DQC: {{TITLE}}

## Target

{{TODO: 검증 대상 Line, exact integration candidate 및 전체 검증 경계를 설명한다}}

## IQC Results

| Micro-SPEC | IQC | 판정 | Micro-SPEC commit | Implementation commit | Exact IQC binding |
| --- | --- | --- | --- | --- | --- |
| `{{MICRO_SPEC_ID}}` | `{{IQC_ID}}` | {{TODO: passed, failed 또는 blocked}} | `{{MICRO_SPEC_COMMIT}}` | `{{IMPLEMENTATION_COMMIT}}` | {{TODO: candidate ancestry와 exact binding 확인 결과}} |

## Checks

### Mandatory Line-Level Checks

| Check ID | 실행 명령 또는 방법 | Exit code·판정 | 결과 요약 | Evidence |
| --- | --- | --- | --- | --- |
| `iqc_coverage_binding` | {{TODO: REQ→AC→Micro-SPEC→IQC coverage와 binding 검사}} | {{TODO: exit code 또는 PASS/FAIL}} | {{TODO: coverage와 exact binding 결과}} | {{NEEDS_EVIDENCE: canonical artifact 또는 command output}} |
| `full_regression` | {{TODO: candidate 전체 regression 명령}} | {{TODO: exit code 또는 PASS/FAIL}} | {{TODO: 전체 test 결과}} | {{NEEDS_EVIDENCE: test output 또는 stable report}} |
| `canonical_validation` | `proofline validate` | {{TODO: exit code 또는 PASS/FAIL}} | {{TODO: canonical validation 결과}} | {{NEEDS_EVIDENCE: validator output}} |
| `cross_spec_integration_scope` | {{TODO: Micro-SPEC 충돌·결합 위험·REQ 범위 검사}} | {{TODO: PASS/FAIL}} | {{TODO: integration·scope 결과}} | {{NEEDS_EVIDENCE: diff, test 또는 review evidence}} |
| `main_fast_forward` | {{TODO: main ancestor와 fast-forward 검사}} | {{TODO: exit code 또는 PASS/FAIL}} | {{TODO: integration readiness}} | {{NEEDS_EVIDENCE: exact main과 candidate commit}} |
| `post_candidate_source_immutability` | {{TODO: candidate 이후 제품 source diff 검사}} | {{TODO: exit code 또는 PASS/FAIL}} | {{TODO: DQC 기록 외 제품 source 불변 결과}} | {{NEEDS_EVIDENCE: candidate 대비 diff}} |

### Mandatory Hosted Candidate Gate

| 항목 | 기록 |
| --- | --- |
| Candidate V | {{NEEDS_EVIDENCE: exact candidate SHA}} |
| Run ID / attempt | {{NEEDS_EVIDENCE: terminal hosted run identity}} |
| Required jobs | `build-candidate`, `ubuntu-python311`, `windows-python311` — {{TODO: conclusion}} |
| Artifact ID / name / expiry | {{NEEDS_EVIDENCE: attempt-qualified artifact read-back}} |
| Wheel filename / SHA-256 | {{NEEDS_EVIDENCE: independent wheel digest}} |
| Provenance / checksum | `CANDIDATE_PROVENANCE.json`, `SHA256SUMS` |
| Evidence helper | `.github/scripts/verify-candidate-evidence.py` |
| DQC admission | {{TODO: non-PASS·누락·stale이면 block_dqc; same-`V` retry forbidden}} |

### Conditional Component Checks

| Trigger | Observed | Decision | 검사·결과 | Exact IQC binding | Skip 또는 실행 rationale |
| --- | --- | --- | --- | --- | --- |
| `source_after_iqc` | {{TODO: yes/no}} | {{TODO: reuse/rerun/blocked}} | {{TODO: 실행 검사와 결과 또는 not applicable}} | {{TODO: 관련 IQC·implementation commit}} | {{TODO: source diff와 decision 근거}} |
| `uncovered_integration_risk` | {{TODO: yes/no}} | {{TODO: reuse/rerun/blocked}} | {{TODO: risk-specific 검사와 결과 또는 not applicable}} | {{TODO: 관련 IQC binding}} | {{TODO: 결합 위험 검토 근거}} |
| `invalid_iqc_evidence` | {{TODO: yes/no}} | {{TODO: reuse/rerun/blocked}} | {{TODO: IQC 보완 결과 또는 not applicable}} | {{TODO: IQC 상태·commit binding}} | {{TODO: evidence 유효성 근거}} |
| `explicit_line_level_requirement` | {{TODO: yes/no}} | {{TODO: reuse/rerun/blocked}} | {{TODO: 명시 검사와 결과 또는 not applicable}} | {{TODO: 관련 IQC binding}} | {{TODO: REQ·AC verification 근거}} |

Conditional trigger가 없고 passed IQC가 exact-bound이면 component-specific 검사는 반복하지 않아도 된다. 이 경우 decision은 `reuse`, 검사·결과는 `not applicable`, 마지막 두 열에는 재사용한 binding과 skip rationale을 기록한다.

## Criteria Results

| AC | 판정 | 근거 |
| --- | --- | --- |
| `{{AC_ID}}` | {{TODO: passed, failed 또는 blocked}} | {{NEEDS_EVIDENCE: 해당 AC의 종합 판정 근거}} |

## Result

- 전체 판정: {{TODO: frontmatter의 result와 일치하는 판정을 작성한다}}
- 요약: {{TODO: main 통합 gate 요청 가능 여부, blocker 및 필요한 재작업을 작성한다}}
