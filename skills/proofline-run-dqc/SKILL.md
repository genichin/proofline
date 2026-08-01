---
name: proofline-run-dqc
description: Use when verifying a ProofLine integration candidate at DQC without repeating exact-bound component IQC checks unless a documented trigger requires them.
version: 1.0.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, dqc, verification, governance]
    related_skills: [proofline-start-implementation]
---

# Run ProofLine DQC

## Overview

DQC를 exact integration candidate의 Line-level verification으로 수행한다. Passed IQC의 exact-bound component evidence는 재사용하고, candidate가 evidence를 stale하게 만들거나 별도 integration risk가 있을 때만 관련 검사를 다시 실행한다.

## When to Use

- 모든 non-withdrawn Micro-SPEC 구현과 IQC가 끝나 Line을 `verifying`으로 전환할 때
- DQC candidate의 coverage, binding, 전체 회귀와 main integration readiness를 판정할 때
- IQC 검사 재사용 또는 조건부 재실행 이유를 DQC artifact에 기록할 때

다음 목적으로 사용하지 않는다.

- 사용자를 대신한 DQC 또는 delivery 승인
- Failed·blocked·stale IQC를 passed로 간주
- 모든 component-specific 검사를 기본적으로 반복
- ProofLine CLI로 test, Git 또는 lifecycle transition 자동화

## Authority Boundary

DQC `result`와 main 통합은 사용자나 지정 governance authority가 결정한다. Hermes는 실제 candidate, command output, canonical artifact와 Git state를 조사해 판정 근거를 작성하지만 evidence가 없는 결과를 만들지 않는다.

ProofLine CLI는 artifact validation만 수행한다. Git branch, commit, merge 또는 push, test 실행과 lifecycle transition은 CLI 책임이 아니다.

## Policy

아래 block은 workflow test가 읽는 DQC decision policy다.

```yaml dqc-policy
policy_version: 1
required_line_checks:
  - iqc_coverage_binding
  - full_regression
  - canonical_validation
  - cross_spec_integration_scope
  - main_fast_forward
  - post_candidate_source_immutability
no_trigger:
  action: reuse_exact_bound_passed_iqc
  required_record:
    - exact_iqc_binding
    - skip_rationale
conditional_triggers:
  source_after_iqc:
    action: rerun_affected_component_checks
    result_required_before_pass: true
  uncovered_integration_risk:
    action: run_risk_specific_checks
    result_required_before_pass: true
  invalid_iqc_evidence:
    action: block_until_valid_iqc
    result_required_before_pass: true
  explicit_line_level_requirement:
    action: run_explicit_line_checks
    result_required_before_pass: true
```

`compileall`, lock, wheel, package, skill metadata와 설치 검사는 `required_line_checks`가 아니다. 해당 검사가 IQC에서 exact-bound passed evidence로 남고 trigger가 없으면 재사용한다.

## Workflow

### 1. Exact candidate 고정

모든 Micro-SPEC 구현, corresponding IQC와 `execution_status: verifying`를 포함한 clean commit을 candidate로 고정한다. Full SHA를 기록하고 현재 HEAD가 정확히 일치하는지 확인한다.

### 2. Coverage와 IQC binding 확인

- REQ의 create·update·retire AC가 non-withdrawn Micro-SPEC에 배정됐는지 확인한다.
- 모든 대상 Micro-SPEC이 `approved`·`implemented`인지 확인한다.
- 각 IQC가 올바른 Micro-SPEC을 가리키고 `passed`인지 확인한다.
- `micro_spec_commit`과 `implementation_commit`이 실제 commit이며 candidate ancestry에 포함되는지 확인한다.

하나라도 누락·stale·failed·blocked이거나 exact binding이 불명확하면 `invalid_iqc_evidence`다. 유효한 IQC를 얻기 전에는 DQC를 passed로 판정하지 않는다.

### 3. Mandatory Line-Level Checks 실행

항상 다음을 확인한다.

1. `iqc_coverage_binding`: 위 coverage와 exact binding
2. `full_regression`: Candidate에서 project 전체 regression
3. `canonical_validation`: Candidate에서 `proofline validate`
4. `cross_spec_integration_scope`: Micro-SPEC 간 충돌, 결합 위험, 전체 REQ 범위
5. `main_fast_forward`: 통합 대상 main이 candidate ancestor이고 fast-forward 가능한지
6. `post_candidate_source_immutability`: DQC 기록 이후 candidate 대비 제품 source가 바뀌지 않았는지

### 4. Conditional Component Checks 결정

| Trigger | 판단 | Action |
| --- | --- | --- |
| `source_after_iqc` | IQC implementation commit 이후 candidate에서 관련 component source 변경 | 영향받은 component 검사 재실행 |
| `uncovered_integration_risk` | 여러 Micro-SPEC 결합이 focused IQC가 다루지 않은 위험 생성 | 위험별 integration 검사 실행 |
| `invalid_iqc_evidence` | IQC 누락·stale·non-passed 또는 binding 불명 | 유효 IQC 전까지 blocked |
| `explicit_line_level_requirement` | REQ·AC가 특정 Line-level verification 명시 | 명시 검사 실행 |

어떤 trigger도 없으면 exact-bound passed IQC를 재사용한다. Component-specific check가 not applicable은 실패가 아니다. DQC에는 Exact IQC binding과 Skip 또는 실행 rationale을 기록한다.

### 5. DQC artifact 기록

Canonical template를 사용해 다음을 남긴다.

- Candidate full SHA와 Line 범위
- 각 Micro-SPEC·IQC result 및 exact commit binding
- Mandatory Line-Level Checks의 실제 command·판정·evidence
- 네 conditional trigger의 observed 여부, reuse·rerun·blocked decision과 rationale
- 모든 대상 AC의 종합 판정

실행하지 않은 검사를 PASS라고 쓰지 않는다. `reuse` 또는 `not applicable`과 근거를 쓴다.

### 6. Main integration handoff

DQC passed 후에도 자동 merge하지 않는다. Main checkout의 branch·cleanliness, candidate ancestry와 fast-forward를 다시 확인한다. DQC artifact commit 이후 candidate 대비 변경은 canonical DQC 기록만 허용하며 candidate 이후 제품 source 불변을 확인한다.

## Failure Handling

- Mandatory check 실패: DQC `failed` 또는 실제 blocker에 따라 `blocked`
- Required conditional result 부재: `passed` 금지
- Candidate 이후 제품 source 변경: 새 candidate 고정 후 영향받은 IQC·DQC 재검증
- Main diverged: merge하지 않고 governance workspace에서 재계획

## Verification Checklist

- [ ] Exact candidate full SHA를 고정했다.
- [ ] 모든 Micro-SPEC·IQC coverage와 binding을 확인했다.
- [ ] Mandatory Line-Level Checks 여섯 개를 실행했다.
- [ ] 네 conditional trigger를 각각 판정했다.
- [ ] No-trigger reuse에는 IQC binding과 skip rationale이 있다.
- [ ] Required conditional result 없이 passed로 판정하지 않았다.
- [ ] DQC 이후 candidate 대비 제품 source 불변을 확인했다.
- [ ] Main fast-forward 가능성을 확인했다.
- [ ] 사용자의 통합 authority를 보존했다.
