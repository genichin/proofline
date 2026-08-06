# Line 검증·통합·Delivery 계약

이 문서는 specification baseline과 branch lifecycle, DQC, Line execution 상태, main 통합 gate, delivery 판정 및 Line·DQC 문서 schema를 정의한다. 공통 문서 형식과 commit field는 [문서 형식과 완결성](document-format.md)을 따른다.

## Specification baseline과 branch lifecycle

ProofLine은 specification governance와 implementation을 branch 경계로 분리한다.

```text
main governance
→ Line 생성
→ Discovery 작성·확인
→ REQ와 대상 AC 작성·검토
→ REQ 승인
→ specification baseline commit 확정

implementation branch
→ 승인 commit에서 branch 생성
→ Micro-SPEC 작성·검토
→ 구현
→ 검증

main integration
→ 검증된 구현 통합
→ Line delivery 판정
```

규칙은 다음과 같다.

- Line 생성부터 Discovery confirm과 REQ approve까지는 main의 직렬화된 governance 흐름에서 수행한다.
- REQ approval commit은 해당 Line의 REQ와 대상 AC exact bytes를 고정하는 canonical specification baseline이다.
- Lead/orchestrator는 exact REQ approval baseline `A`의 direct first-parent child로 대상 Line 한 파일의 `not_started → in_progress`만 기록한 status-only handoff `H`를 main에 만든다. Implementation branch와 linked worktree는 exact `H`에서 생성해야 한다.
- Micro-SPEC, 구현 및 구현 검증은 implementation branch에서 수행한다.
- Micro-SPEC과 구현은 branch base에 고정된 승인 REQ 및 AC baseline을 따라야 한다.
- 검증을 통과한 implementation 결과만 main 통합 대상으로 삼는다.
- REQ approval, 그에 따른 AC lifecycle transition 및 implementation branch 생성은 구현·검증 또는 delivery 완료를 의미하지 않는다.

### Implementation linked worktree

Main repository checkout은 `main`의 직렬화된 governance workspace로 유지한다. Implementation branch는 같은 Line의 exact status-only handoff commit `H`에서 다음 deterministic repository-local path에 Git linked worktree로 생성한다.

```text
.worktrees/line-NNNN/
```

Worktree 생성 전에 main checkout cleanliness, exact approval baseline `A`의 canonical Line·Discovery·REQ·AC 상태, `H`의 direct-child/status-only diff, target path, branch 및 기존 worktree registration 충돌을 모두 확인한다. Preflight 실패는 Git ref, worktree registration과 filesystem target을 변경하지 않는 no-mutation failure여야 한다. 생성 후에는 linked worktree의 HEAD와 branch base가 exact `H`인지, main checkout이 attached `main` at `H`이고 clean state인지 검증한다.

`H`가 persisted된 뒤 worktree 생성이나 read-back이 실패해도 main history를 reset/rewrite하거나 Line을 `not_started`로 되돌리지 않는다. Line은 `in_progress`와 exact `H`를 유지한다. 원인을 제거한 뒤 branch·path·registration의 expected state만 허용하는 idempotent preflight로 같은 exact `H`에서 재시도한다. 이미 exact expected branch/worktree가 clean `H`에 수렴한 retry는 성공으로 판정한다. 다른 identity 또는 partial collision은 자동 삭제·강제 복구하지 않는다. 복구 불가능한 종료는 정상 `in_progress → cancelled` transition을 사용한다.

Main과 모든 Line worktree의 ProofLine governance command는 user-level `uv tool`에 설치된 공용 `proofline` executable을 사용한다. Line worktree에는 ProofLine 전용 `.venv`를 생성하지 않는다. 구현 대상 project가 source build·test에 사용하는 environment는 해당 project의 개발 계약이 소유하며 ProofLine governance environment가 아니다.

Micro-SPEC, implementation source, test와 IQC artifact는 해당 linked worktree에서 작성하고 commit한다. Worktree 사용은 REQ approval, DQC, fast-forward main integration 또는 delivery authority를 변경하지 않는다. 생성·검증·사용·정리는 repository-owned Hermes workflow가 담당하며 ProofLine CLI는 Git branch, worktree, commit, merge, push 또는 lifecycle transition을 수행하지 않는다.

Delivery 후 worktree는 clean state를 확인한 뒤 명시적으로 제거한다. Dirty 또는 untracked file이 있거나 registration이 예상과 다르면 자동으로 강제 삭제하지 않는다.

구현 중 승인된 AC의 의미를 변경할 필요가 발견되면 implementation branch에서 그 변경을 승인하거나 구현과 함께 main에 통합하지 않는다. 다음 순서를 따른다.

```text
implementation branch에서 변경 필요 발견
→ 영향받는 구현 중단
→ main governance로 반환
→ REQ와 AC 변경
→ 변경 영향 검토
→ REQ 재승인과 대상 AC lifecycle 확정
→ 새로운 specification baseline commit 확정
→ implementation branch에 새 baseline 반영
→ 영향받는 Micro-SPEC 갱신
→ 구현 재개
```

- 구현자나 Micro-SPEC은 승인된 AC를 임의로 확대, 축소 또는 변경할 수 없다.
- 같은 구현의 PASS/FAIL 결과를 바꿀 수 있는 AC 수정은 specification 변경이다.
- specification 변경이 발생하면 기존 승인 baseline에 대한 영향받는 구현·검증 완료 주장을 중단한다.
- 변경된 REQ가 main governance에서 재승인되고 대상 AC lifecycle이 확정되기 전에는 영향받는 구현을 재개하지 않는다.
- 독립적인 새 승인 또는 delivery 경계가 필요한 변경은 기존 Line을 확대하지 않고 새 Line으로 분리한다.

사양 상태와 실행 상태는 서로 다른 사실이므로 별도로 관리한다.

```text
Specification state
→ REQ와 AC가 구현 기준으로 승인됐는가

Execution state
→ Micro-SPEC 작성, 구현, 검증 및 delivery가 어디까지 진행됐는가
```

따라서 다음 상태는 유효하다.

```text
REQ specification: approved
Target AC lifecycle: active
Implementation: not started 또는 in progress
Verification: not completed
Delivery: not completed
```

REQ 승인과 그에 따른 AC lifecycle 확정은 구현·검증·delivery 완료를 의미하지 않으며, 구현·검증 결과가 존재한다는 사실도 승인되지 않은 사양을 canonical specification baseline으로 만들지 않는다. Main 통합 gate와 Line delivery 판정은 이 문서의 Line execution contract를 따르며 specification state와 execution state를 하나의 status 의미로 합치지 않는다.

## DQC result

`verifying` 단계의 Line은 다음 고정 경로의 DQC artifact를 하나 가진다.

```text
.proofline/lines/line-<NNNN>/dqc-<NNNN>.md
```

DQC는 모든 Micro-SPEC이 합쳐진 exact integration candidate commit을 Line 전체 관점에서 검증한다. `result`는 IQC와 같은 네 값을 사용한다.

```text
draft
passed
failed
blocked
```

- DQC를 시작하기 전에 모든 non-withdrawn Micro-SPEC의 구현과 IQC를 포함한 `candidate_commit`을 고정한다.
- DQC는 모든 대상 AC의 Micro-SPEC 배정, 모든 필수 IQC PASS, Micro-SPEC 간 충돌·회귀, Line 전체 test와 REQ 범위를 검증한다.
- `passed`는 해당 `candidate_commit`이 main 통합 gate를 요청할 수 있음을 의미하며 아직 main 통합이나 delivery를 의미하지 않는다.
- 재검증할 때 같은 DQC 파일을 갱신하고 과거 결과는 Git history로 보존한다.

### DQC 항상 필수 검사

DQC는 다음 Line-level 신호를 candidate마다 확인한다.

1. 모든 non-withdrawn Micro-SPEC과 IQC의 coverage·exact commit binding
2. Exact candidate의 전체 regression test
3. Candidate canonical `proofline validate`
4. Micro-SPEC 간 충돌, integration risk와 Line 전체 REQ 범위
5. 통합 대상 main의 ancestor·fast-forward 가능성
6. DQC candidate 이후 제품 source 불변

7. mandatory hosted candidate gate의 exact candidate SHA, terminal run attempt, required job 결과, 동일 artifact·provenance·wheel SHA-256 read-back

이 검사는 여러 구현 단위가 합쳐진 candidate와 main integration readiness를 판정하므로 IQC evidence만으로 생략할 수 없다.

Hosted candidate gate는 `.github/workflows/candidate-verification.yml`의 `candidate/**` push만 대상으로 하며, `build-candidate`, `ubuntu-python311`, `windows-python311`의 terminal success와 attempt-qualified artifact를 `.github/scripts/verify-candidate-evidence.py`로 검증한다. Evidence가 누락·실패·identity drift·artifact ambiguity·stale이면 DQC PASS를 금지한다. 이 판단과 remote identity는 DQC Checks에 기록하고 main integration이나 release authority를 부여하지 않는다. same-`V` retry는 허용하지 않는다.

### IQC evidence 재사용과 조건부 재검사

IQC는 exact Micro-SPEC·implementation commit에서 focused behavior, component-specific safety, package·wheel, skill 형식, compile, lock과 설치 검사를 소유한다. Passed IQC의 exact binding이 candidate ancestry에 포함되고 아래 trigger가 없으면 DQC는 그 evidence를 재사용하며 동일 검사를 기본적으로 반복하지 않는다.

대표 trigger는 source-after-IQC, uncovered integration risk, invalid IQC evidence와 explicit Line-level requirement이다. DQC는 재사용 또는 실행 결정을 exact binding과 skip rationale로 설명한다.

| Trigger ID | 조건 | DQC action |
| --- | --- | --- |
| `source_after_iqc` | IQC implementation commit 이후 candidate에서 관련 component source가 변경됨 | 영향받은 component 검사 재실행 |
| `uncovered_integration_risk` | 여러 Micro-SPEC 결합이 focused IQC가 다루지 않은 integration risk를 만듦 | 위험별 integration 검사 실행 |
| `invalid_iqc_evidence` | IQC가 누락·stale·failed·blocked이거나 exact binding이 불명확함 | 유효 IQC 전까지 DQC passed 차단 |
| `explicit_line_level_requirement` | REQ·AC가 특정 검사를 Line-level verification으로 요구함 | 명시된 검사 실행 |

Trigger가 없으면 component-specific 검사의 not applicable은 실패가 아니다. DQC artifact에는 재사용한 Exact IQC binding과 Skip 또는 실행 rationale을 기록한다. Trigger가 있으면 필요한 검사 결과 없이 DQC를 `passed`로 판정할 수 없다.

이 책임 분리는 canonical DQC artifact를 작성하는 workflow 규칙이다. DQC command list나 transition history를 검사하도록 `proofline validate`의 validation scope를 확대하지 않는다.

## Line execution artifact와 status

각 Line은 다음 canonical artifact를 정확히 하나 소유한다.

```text
.proofline/lines/line-<NNNN>/line-<NNNN>.md
```

Line artifact는 Line identity와 전체 execution 상태를 소유한다. 새 Line의 최소 형태는 다음과 같다.

```yaml
---
id: line-0001
execution_status: not_started
implementation_history: first_parent
---
```

`implementation_history: first_parent`는 implementation lifecycle chronology를 exact candidate의 Git first-parent chain에서 판정하는 Line policy이다. 새 Line은 이 field를 기록한다. Policy 도입 전에 생성된 historical Line의 fieldless shape는 parser·schema 호환성을 위해 그대로 읽으며 writer가 소급 수정하지 않는다.

### First-parent history policy

- 해당 Line에서 policy가 처음 persisted된 commit `B`가 adoption baseline이다. 도입 뒤 field 제거·변경은 `history.line.policy.changed`로 실패한다.
- Repository first-parent history에서 어떤 Line이든 policy가 처음 persisted된 earliest commit `A`가 fieldless legacy cutoff이다. Fieldless terminal(`delivered` 또는 `cancelled`) Line은 terminal transition `T`가 없거나 `T >= A`이면 `history.line.legacy.invalid`이며, `A`가 없는 pre-adoption repository 또는 `T < A`만 legacy로 인정한다.
- Fieldless non-terminal Line은 `history.line.policy.missing`이다. 새 `not_started` Line은 writer가 field를 기록하며, 기존 non-terminal Line은 implementation/IQC 전에 명시적으로 adoption한다.
- Strict cycle은 approved `micro_spec_commit < P < I < Q`와 `B < I < Q`를 모두 만족해야 한다. `P`는 별도 persisted `in_progress`, `I`는 IQC의 exact implementation commit, `Q`는 후속 `implemented` transition과 IQC candidate이다. 따라서 `P < B < I < Q`와 `B < P < I < Q`를 모두 허용한다.
- Rework마다 이전 `implemented` 뒤 fresh `in_progress → implementation → implemented/IQC` cycle이 필요하다. Direct transition, `P = I`, `I = Q`, `I < B`, second-parent-only evidence와 lifecycle-only implementation binding은 거부한다.
- Policy activation `A0` 전에 complete `S < P < I < Q` cycle을 가진 fieldless `in_progress|verifying` Line만 `.proofline/lines/line-NNNN/legacy-migration-NNNN.md` authority로 one-time migration할 수 있다. Artifact는 `id`, `line`, repository-native lowercase `pre_migration_parent`, lexicographically sorted exhaustive `evidence` entries(`path`, regular-blob `blob_oid`)만 가진 frontmatter-only 문서다.
- Migration containing commit `B`는 target Line의 `implementation_history: first_parent` 추가와 matching migration artifact, 정확히 두 path만 변경한다. `pre_migration_parent == B^1`이고 evidence는 `B^1`의 target-Line Micro-SPEC/IQC/DQC 전부와 exact OID가 일치해야 한다. Migration은 pre-`B` complete cycle의 `I < B`만 예외로 하며 다른 ordering·binding·quality 규칙을 면제하지 않는다.
- Persisted migration artifact와 target policy bytes는 변경·삭제·재도입·재적용할 수 없다. `B` 직후 기존 non-terminal state/result를 보존하고, 후속 delivery에는 fresh `B < P₂ < I₂ < Q₂ < V₂ < DQC`를 요구한다. 작성·교정 절차는 [legacy non-terminal migration 운영 절차](../operations/legacy-nonterminal-history-migration.md)를 따른다.
- History 판정은 commit timestamp나 all-parent reachability가 아니라 exact candidate first-parent 순서만 사용한다. Missing object, shallow repository, malformed historical artifact, unresolved binding 또는 Git read 실패는 `history.unavailable`로 fail-closed한다.
- History Git read는 각 subprocess에 finite timeout(5초), captured output 상한(8 MiB), `GIT_NO_LAZY_FETCH=1`, `GIT_NO_REPLACE_OBJECTS=1`, `GIT_OPTIONAL_LOCKS=0`, `GIT_TERMINAL_PROMPT=0`을 적용하며, timeout·출력 초과·실패는 `history.unavailable`로 처리한다. 반복되는 commit/tree/blob read는 validation session 안에서 cache한다.
- Validator는 history를 읽기만 하며 artifact, index, worktree, HEAD, refs와 object database를 변경하지 않는다. Path 종류로 implementation의 semantic 품질을 승인하지 않으며 실제 검사 판정은 IQC가 소유한다.

`execution_status`는 다음 다섯 값만 허용한다.

```text
not_started
in_progress
verifying
delivered
cancelled
```

| Status | 의미 |
| --- | --- |
| `not_started` | Line은 존재하지만 implementation branch를 아직 시작하지 않음 |
| `in_progress` | Micro-SPEC 작성 또는 구현을 진행 중 |
| `verifying` | 구현을 마치고 Line 전체 검증을 진행 중 |
| `delivered` | 검증을 통과한 결과가 main에 통합됨 |
| `cancelled` | Discovery 또는 REQ 철회로 Line 실행을 중단함 |

기본 transition은 다음과 같다.

```text
not_started → in_progress → verifying → delivered
                 ↑            │
                 └────────────┘
                   검증 실패

not_started ─→ cancelled
in_progress ─→ cancelled
verifying   ─→ cancelled
```

규칙은 다음과 같다.

- Line을 main governance에서 생성할 때 `execution_status`는 `not_started`이다.
- 승인된 REQ baseline에서 implementation branch를 생성할 때 `not_started → in_progress`로 전환한다.
- 같은 Line의 모든 non-withdrawn Micro-SPEC이 `spec_status: approved`, `implementation_status: implemented`이고 대응하는 IQC가 `result: passed`이면 `in_progress → verifying`로 전환할 수 있다.
- 검증이 실패하거나 재작업이 필요하면 `verifying → in_progress`로 전환한다.
- Discovery 또는 REQ가 `withdrawn`이면 완료되지 않은 Line을 `cancelled`로 전환한다.
- `delivered`와 `cancelled`는 terminal status이다.

### Main 통합 gate

#### Main-first integration candidate와 manifest

Line 0020의 Line spine은 bootstrap `S=A < H < P < I < Q`이며 post-H `S0/S`를 만들지 않는다. Updated workflow를 사용하는 후속 Line은 `A < H < S0 < S < P < I < Q`다. `Q`는 모든 대상 implementation, passed IQC와 Line `verifying` transition을 포함한 clean exact Line head다. Main은 `H` 이후 다른 governance를 진행할 수 있으며 Line worktree는 main을 merge/rebase하지 않는다.

DQC lead는 latest main `M`에서 collision 없는 별도 integration candidate branch/worktree를 만들고 exact `Q`를 `--no-ff`로 merge하여 candidate `V`를 만든다. `V`는 exactly two parents이고 `V.parent[0]=M`, `V.parent[1]=Q`여야 한다. Parent reversal, octopus, multi-Line/arbitrary second parent, unresolved conflict는 admission 실패다. `V`의 merge tree는 deterministic merge result와 아래 manifest addition만 포함하며 manifest 외 merge-only source·test·build/runtime 수정이나 conflict resolution이 필요하면 owner Line으로 반환한다.

`V`가 새로 포함하는 canonical path는 다음 하나다.

```text
.proofline/lines/line-<NNNN>/integration-<NNNN>.md
```

Manifest는 frontmatter-only이며 schema는 다음과 같다.

```yaml
---
id: integration-0001
line_id: line-0001
main_parent: "<exact M>"
line_head: "<exact Q>"
---
```

필수 field는 `id`, `line_id`, `main_parent`, `line_head`이고 다른 field와 본문은 금지한다. `candidate_commit` self-reference는 기록하지 않는다. Containing commit 자체가 `V`이며 path·identity, `main_parent == V.parent[0] == M`, `line_head == V.parent[1] == Q`가 exact target Line 하나에 결속돼야 한다. DQC는 후속 artifact의 `candidate_commit: V`로 이를 bind한다.

#### Pre-admission mutable gate와 post-integration immutable chronology

Candidate 생성부터 hosted evidence admission, DQC PASS와 main fast-forward 직전까지의 **pre-integration** operational gate는 mutable refs를 직접 확인한다. Exact current `refs/heads/main == M`, canonical clean Line ref/head `== Q`, clean/collision-safe candidate worktree와 exact two-parent/manifest binding이 필요하다. Main이 old `V`를 포함하지 않은 채 진행하면 old `V`는 stale이다. 같은 exact `Q`를 rewrite하지 않고 fresh latest main에서 fresh `V`, manifest, hosted evidence와 DQC를 만든다. `Q`가 진행한 경우에도 fresh verification head와 fresh `V`가 필요하다.

Main이 DQC PASS descendant로 fast-forward된 뒤의 **post-integration** historical validation은 current refs equality를 요구하지 않는다. Immutable Git object인 `V`, actual parents, contained manifest, DQC `candidate_commit: V`, main first-parent의 연속된 `M → V → DQC PASS → delivery`와 designated Line first-parent spine을 검증한다. Clean worktree cleanup이나 후속 unrelated main commit 뒤에도 이 chronology는 유효하다.

Line implementation branch는 다음 조건을 모두 만족해야 main에 통합할 수 있다.

```text
Discovery.status = confirmed
REQ.status = approved
Line.execution_status = verifying
모든 non-withdrawn Micro-SPEC.spec_status = approved
모든 non-withdrawn Micro-SPEC.implementation_status = implemented
모든 대응 IQC.result = passed
DQC.result = passed
canonical artifact validation = passed
통합 대상 canonical artifact의 governance placeholder = 0개
Line verification 또는 delivery까지 해소하기로 한 deferred Open Question = 0개
```

추가 binding 규칙은 다음과 같다.

- 각 IQC의 `micro_spec_commit`과 `implementation_commit`은 실제 검증한 exact commit이어야 한다.
- DQC의 `candidate_commit`은 모든 대상 Micro-SPEC 구현, IQC PASS 및 `execution_status: verifying`를 포함한 exact commit이어야 한다.
- REQ의 대상 AC 집합과 각 AC lifecycle이 승인 baseline에 일치해야 한다.
- `Exit Condition`이 Line verification 또는 delivery를 가리키는 deferred Open Question은 DQC PASS 전에 해소하고 답을 canonical owner section에 반영해야 한다.
- DQC PASS를 기록한 뒤 main 통합 전까지 제품 source, test, build 또는 runtime configuration을 변경할 수 없다. 변경하면 새 `candidate_commit`을 고정하고 영향받는 IQC와 DQC를 다시 수행한다.
- Main 통합은 commit identity를 바꾸지 않고 exact `V`의 DQC PASS descendant로 fast-forward하는 방식만 허용한다. Main이 `M`에서 진행해 old candidate로 fast-forward할 수 없으면 Line branch나 `Q`를 rewrite·merge·rebase하지 않고 fresh latest main에서 같은 exact `Q`를 designated second parent로 하는 fresh `V`, hosted evidence와 DQC를 다시 수행한다.
- Squash, cherry-pick 또는 commit을 다시 작성하는 통합은 기존 IQC와 DQC binding을 무효화하므로 허용하지 않는다.

### Line delivery 판정

Main 통합과 Line delivery는 다음 순서로 수행한다.

```text
DQC passed
→ main 통합 gate 확인
→ DQC PASS descendant integration branch를 main에 fast-forward
→ 통합된 exact commit과 canonical artifact를 확인
→ main에서 Line.execution_status를 delivered로 전환
```

`verifying → delivered` transition은 다음 조건을 모두 만족해야 한다.

- DQC가 `result: passed`이고 그 `candidate_commit`이 main history에 exact commit으로 존재해야 한다.
- Main에 통합된 DQC PASS descendant는 candidate `V` 이후 제품 source, test, build 또는 runtime configuration을 변경하지 않아야 한다.
- Main에서 canonical artifact validation이 통과해야 한다.
- Delivery transition commit은 main의 직렬화된 governance 흐름에서 작성하며, 그 직전 parent는 통합된 DQC PASS descendant head여야 한다.
- 위 조건을 만족하기 전에는 Line을 `delivered`로 기록할 수 없다.

통합 확인에 실패하면 Line은 `verifying`에 남는다. 구현 변경이 필요하면 `verifying → in_progress`로 되돌리고 영향받는 IQC와 DQC를 다시 수행한다.

## Line 문서 schema

### Frontmatter

```yaml
---
id: line-0001
execution_status: not_started
implementation_history: first_parent
---
```

새 Line의 필수 field:

```text
id
execution_status
implementation_history
```

- `implementation_history` 값은 `first_parent`만 허용한다.
- Policy 지원 전에 생성된 historical Line은 field가 없어도 구조적으로 읽을 수 있으며 bytes를 소급 변경하지 않는다.

### Markdown 본문

Line artifact는 execution manifest이므로 Markdown 본문을 갖지 않는다. 닫는 YAML frontmatter delimiter 뒤에는 공백과 마지막 newline 외의 내용을 기록하지 않는다.

## DQC 문서 schema

### Frontmatter

```yaml
---
id: dqc-0001
line: line-0001
candidate_commit: "<git-commit>"
result: draft
---
```

필수 field:

```text
id
line
candidate_commit
result
```

- `id`의 `NNNN`은 `line`과 상위 Line directory 번호에 일치해야 한다.
- `candidate_commit`은 DQC가 실제 검증한 exact Line integration candidate commit이다.
- `candidate_commit`은 해당 저장소에서 해석되는 exact Git commit이어야 한다.

### Markdown 본문

필수 본문 schema는 다음과 같다.

```markdown
# DQC: 제목

## Target

## IQC Results

## Checks

## Criteria Results

## Result
```

| Section | 소유하는 사실 |
| --- | --- |
| `Target` | 검증 대상 Line과 candidate commit의 설명 |
| `IQC Results` | 모든 대상 Micro-SPEC과 대응 IQC PASS의 종합 |
| `Checks` | Line 전체 test, 회귀·충돌 및 REQ 범위 검사 결과 |
| `Criteria Results` | REQ 대상 AC 전체의 종합 판정 |
| `Result` | DQC 전체 판정과 필요한 설명 |
