---
name: proofline-run-dqc
description: Use when verifying a ProofLine integration candidate at DQC without repeating exact-bound component IQC checks unless a documented trigger requires them.
version: 1.5.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, dqc, verification, governance]
    related_skills: [proofline-start-implementation]
---

# Run ProofLine DQC

## Overview

DQC를 main-first exact integration candidate `V`의 Line-level verification으로 수행한다. `V.parent[0]=M` latest main, `V.parent[1]=Q` exact Line head와 canonical manifest를 DQC 전에 read-only로 pre-admit한다. Passed IQC의 exact-bound component evidence는 재사용하고, candidate가 evidence를 stale하게 만들거나 별도 integration risk가 있을 때만 관련 검사를 다시 실행한다.

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

GitHub Actions를 포함한 외부 CI는 각 repository가 선택해 운영하는 project-local 검증이다. 외부 CI 결과는 ProofLine DQC PASS를 생성하거나 여섯 필수 신호와 네 conditional trigger를 대체하거나 승격할 수 없으며, main 통합 authority도 부여하지 않는다.

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

### 1. Main-first exact candidate와 pre-admission

모든 Micro-SPEC 구현, corresponding IQC와 `execution_status: verifying`를 포함한 clean Line head를 `Q`로 고정한다. Latest main을 `M`으로 고정하고 별도 collision-safe candidate branch/worktree에서 `Q`를 `--no-ff` merge해 exactly two-parent `V`를 만든다. Parent order는 `V.parent[0]=M`, `V.parent[1]=Q`다. `V`는 `.proofline/lines/line-NNNN/integration-NNNN.md` 하나를 새로 포함하며 manifest의 `line_id`, `main_parent`, `line_head`가 target Line, `M`, `Q`와 exact하게 일치해야 한다. Manifest 외 merge-only 제품 변경이나 conflict resolution은 candidate admission 실패다.

Pre-integration에는 mutable ref equality가 gate다. Candidate 생성·DQC PASS·main fast-forward 직전까지 current main ref가 exact `M`, canonical clean Line ref/head가 exact `Q`여야 한다. 다음 read-only helper를 clean candidate `V` worktree에서 실행한다.

```bash
python3 ~/.proofline/skills/proofline-run-dqc/scripts/preflight_integration_candidate.py \
  --repo "$PWD" --line-id "$LINE_ID" \
  --main-ref refs/heads/main --line-ref "$LINE_REF" \
  --main-parent "$M" --line-head "$Q" --candidate "$V"
```

Helper는 exact refs, clean/collision-safe state, `HEAD=V`, ordered exactly two parents와 frontmatter-only manifest binding뿐 아니라 designated `Q` 자체를 읽기만 한다. `Q`는 canonical target Line의 exact `in_progress → verifying` first-parent quality transition이어야 하고 그 commit의 변경은 target Line과 허용된 target Micro-SPEC/IQC path로 제한된다. 모든 non-withdrawn Micro-SPEC은 `approved`·`implemented`이고 canonical passed IQC가 exact approved specification, fresh persisted `in_progress`, non-governance implementation과 quality boundary를 bind해야 한다. Missing·failed·stale·identity-mismatched IQC, unrelated/multi-Line path 또는 arbitrary self-consistent second parent는 pre-admission에서 실패한다. Helper는 `V`를 생성하거나 file/index/ref/object/worktree를 변경하지 않으며 approve, merge, push, publish도 하지 않는다. Stale `M`이면 same exact `Q`를 유지한 fresh `V`와 fresh DQC evidence를 만들고 old `V`를 merge/rebase하지 않는다. Stale `Q`도 fresh verification head와 fresh `V`가 필요하다.

### 2. Coverage와 IQC binding 확인

- REQ의 create·update·retire·satisfy AC가 non-withdrawn Micro-SPEC에 배정됐는지 확인한다.
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
5. `main_fast_forward`: `V.parent[0]=M`, `V.parent[1]=Q`, pre-integration mutable ref equality와 main fast-forward 가능성
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

Main이 DQC PASS descendant로 fast-forward된 post-integration 단계에서는 current main/Line ref equality를 historical invariant로 사용하지 않는다. Immutable `V`, actual parents, contained manifest, DQC `candidate_commit: V`, main first-parent의 `M → V → DQC PASS → delivery`와 designated Line chronology를 검사한다. Line branch/worktree cleanup이나 후속 unrelated main commit은 이 binding을 무효화하지 않는다.

## Failure Handling

- Mandatory check 실패: DQC `failed` 또는 실제 blocker에 따라 `blocked`
- Required conditional result 부재: `passed` 금지
- Candidate 이후 제품 source 변경: 새 candidate 고정 후 영향받은 IQC·DQC 재검증
- Pre-integration main drift: same exact `Q`를 designated second parent로 하는 fresh main-first `V`와 DQC 수행
- Pre-integration Line drift: fresh `Q` verification head와 fresh `V` 수행

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
- [ ] 외부 CI 결과를 ProofLine DQC PASS 또는 통합 authority로 대체·승격하지 않았다.
