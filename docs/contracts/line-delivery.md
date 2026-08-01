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
- implementation branch는 REQ approval commit에서 생성해야 한다.
- Micro-SPEC, 구현 및 구현 검증은 implementation branch에서 수행한다.
- Micro-SPEC과 구현은 branch base에 고정된 승인 REQ 및 AC baseline을 따라야 한다.
- 검증을 통과한 implementation 결과만 main 통합 대상으로 삼는다.
- REQ approval, 그에 따른 AC lifecycle transition 및 implementation branch 생성은 구현·검증 또는 delivery 완료를 의미하지 않는다.

### Implementation linked worktree

Main repository checkout은 `main`의 직렬화된 governance workspace로 유지한다. Implementation branch는 같은 Line의 exact REQ approval commit에서 다음 deterministic repository-local path에 Git linked worktree로 생성한다.

```text
.worktrees/line-NNNN/
```

Worktree 생성 전에 main checkout cleanliness, approval commit과 canonical Line·Discovery·REQ 상태, target path, branch 및 기존 worktree registration 충돌을 모두 확인한다. Preflight 실패는 Git ref, worktree registration과 filesystem target을 변경하지 않는 no-mutation failure여야 한다. 생성 후에는 linked worktree의 HEAD와 branch base가 exact REQ approval commit인지, main checkout이 `main`과 clean state를 유지하는지 검증한다.

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

## Line execution artifact와 status

각 Line은 다음 canonical artifact를 정확히 하나 소유한다.

```text
.proofline/lines/line-<NNNN>/line-<NNNN>.md
```

Line artifact는 Line identity와 전체 execution 상태를 소유한다. 최소 형태는 다음과 같다.

```yaml
---
id: line-0001
execution_status: not_started
---
```

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
- Main 통합은 commit identity를 바꾸지 않는 fast-forward 방식만 허용한다. Main이 진행되어 fast-forward할 수 없으면 implementation branch에 새 main을 반영하고 candidate를 다시 고정한 뒤 DQC를 다시 수행한다.
- Squash, cherry-pick 또는 commit을 다시 작성하는 통합은 기존 IQC와 DQC binding을 무효화하므로 허용하지 않는다.

### Line delivery 판정

Main 통합과 Line delivery는 다음 순서로 수행한다.

```text
DQC passed
→ main 통합 gate 확인
→ implementation branch를 main에 fast-forward
→ 통합된 exact commit과 canonical artifact를 확인
→ main에서 Line.execution_status를 delivered로 전환
```

`verifying → delivered` transition은 다음 조건을 모두 만족해야 한다.

- DQC가 `result: passed`이고 그 `candidate_commit`이 main history에 exact commit으로 존재해야 한다.
- Main에 통합된 branch head는 DQC PASS 이후 제품 source, test, build 또는 runtime configuration을 변경하지 않아야 한다.
- Main에서 canonical artifact validation이 통과해야 한다.
- Delivery transition commit은 main의 직렬화된 governance 흐름에서 작성하며, 그 직전 parent는 통합된 implementation branch head여야 한다.
- 위 조건을 만족하기 전에는 Line을 `delivered`로 기록할 수 없다.

통합 확인에 실패하면 Line은 `verifying`에 남는다. 구현 변경이 필요하면 `verifying → in_progress`로 되돌리고 영향받는 IQC와 DQC를 다시 수행한다.

## Line 문서 schema

### Frontmatter

```yaml
---
id: line-0001
execution_status: not_started
---
```

필수 field:

```text
id
execution_status
```

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
