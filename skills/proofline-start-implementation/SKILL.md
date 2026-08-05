---
name: proofline-start-implementation
description: Use when starting implementation for an approved ProofLine Line in an exact-baseline Git linked worktree while preserving main as the governance checkout.
version: 1.4.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, git-worktree, implementation, governance]
    related_skills: []
---

# Start ProofLine Implementation

## Overview

승인된 Line의 implementation을 main-owned status-only handoff commit exact `H`에서 `.worktrees/line-NNNN/` linked worktree로 시작한다. `H`는 exact REQ·AC approval baseline `A`의 direct first-parent child다. Main checkout은 `main`의 clean governance workspace로 유지한다. 이 workflow는 Git을 명시적으로 운용하지만 ProofLine CLI가 Git branch, worktree, commit, merge 또는 lifecycle transition을 수행하도록 확장하지 않는다.

Main과 모든 Line worktree의 governance 명령은 user-level `uv tool`에 설치된 공용 `proofline` executable을 사용한다. Worktree에는 ProofLine 전용 `.venv`를 생성하지 않는다. 구현 대상 project의 source build·test environment는 그 project의 계약을 따른다.

## When to Use

- Confirmed Discovery와 approved REQ가 있는 Line의 implementation을 시작할 때
- Main checkout을 governance 전용으로 유지하면서 별도 linked worktree가 필요할 때
- 기존 worktree의 exact `H`, branch, path와 shared tool 경계를 검증하거나 failed creation을 같은 `H`에서 idempotent recovery할 때

다음에는 사용하지 않는다.

- Discovery confirmation이나 REQ approval 전
- DQC 없이 main integration 또는 delivery를 수행할 때
- 일반적인 Git worktree 생성이나 ProofLine과 무관한 branch 작업

## Preconditions

다음 값을 승인 artifact와 Git history에서 직접 확보한다.

```text
LINE_ID=line-NNNN
REQ_ID=req-NNNN
APPROVAL_COMMIT=<exact REQ approval commit>
HANDOFF_COMMIT=<exact status-only direct child H; current main HEAD>
BRANCH=<명시적으로 선택한 implementation branch>
WORKTREE_PATH=.worktrees/line-NNNN
```

ID나 approval commit을 추측하지 않는다. 현재 source, canonical artifact와 Git history를 직접 확인한다. Credential이나 token이 보이면 값을 기록하지 않고 `[REDACTED]`로 표시한다.

## Workflow

### 1. Preflight

모든 검사를 `git worktree add` 전에 끝낸다. 하나라도 실패하면 Git ref, worktree registration과 target path를 변경하지 않는 no-mutation failure로 종료한다.

Main checkout에서 다음을 확인한다.

```bash
test "$(git branch --show-current)" = main
test -z "$(git status --porcelain)"
git cat-file -e "$APPROVAL_COMMIT^{commit}"
git show "$APPROVAL_COMMIT:.proofline/lines/line-NNNN/dcy-NNNN.md"
git show "$APPROVAL_COMMIT:.proofline/lines/line-NNNN/req-NNNN.md"
git show "$APPROVAL_COMMIT:.proofline/lines/line-NNNN/line-NNNN.md"
```

직접 읽은 exact approval commit `A`의 artifact에서 다음 상태를 확인한다.

```text
Discovery.status = confirmed
REQ.status = approved
Line.execution_status = not_started
Line.implementation_history = first_parent (새 policy-bearing Line; 기존 fieldless도 허용)
REQ identity와 Line identity의 NNNN 일치
REQ criteria의 대상 AC가 A에서 승인 상태
```

현재 attached main HEAD를 `H`로 고정하고 `H.parent=A`, changed path가 대상 `line-NNNN.md` 하나뿐이며 exact bytes 차이가 `execution_status: not_started → in_progress` 한 줄뿐인지 검사한다. 이것이 exact A→H status-only handoff다. Script는 handoff commit이나 lifecycle transition을 만들지 않고 이미 기록된 `H`를 검증한다.

그 다음 path 충돌, branch 충돌과 기존 worktree registration을 확인한다.

```bash
test ! -e "$WORKTREE_PATH"
! git show-ref --verify --quiet "refs/heads/$BRANCH"
git worktree list --porcelain
```

`git worktree list --porcelain`에 같은 path 또는 branch가 있으면 기본적으로 중단한다. 단, exact expected branch·path·registration이 모두 존재하고 clean HEAD가 같은 exact `H`인 retry는 idempotent success다. 일부만 존재하거나 다른 identity이면 partial collision으로 중단한다. 실패를 수동 삭제, 다른 ID 또는 `--force`로 우회하지 않는다.

### 2. Worktree 생성

Main checkout에서 repository-owned supporting script를 실행한다. Script는 위 preflight를 다시 수행한 뒤에만 내부적으로 `git worktree add`를 호출한다.

```bash
python ~/.proofline/skills/proofline-start-implementation/scripts/create_worktree.py \
  --repo "$PWD" \
  --line-id "$LINE_ID" \
  --branch "$BRANCH" \
  --approval-commit "$APPROVAL_COMMIT"
```

Canonical target은 `.worktrees/line-NNNN/`이다. Clone을 만들거나 main checkout을 implementation branch로 전환하지 않는다.

### 3. 생성 후 검증

```bash
test "$(git -C "$WORKTREE_PATH" rev-parse HEAD)" = "$HANDOFF_COMMIT"
test "$(git -C "$WORKTREE_PATH" branch --show-current)" = "$BRANCH"
test "$(git branch --show-current)" = main
test -z "$(git status --porcelain)"
test ! -e "$WORKTREE_PATH/.venv"
git worktree list --porcelain
```

Worktree에서 공용 `proofline` executable을 사용해 canonical tree를 확인한다.

```bash
(cd "$WORKTREE_PATH" && proofline validate)
```

검증 실패 시 worktree를 자동으로 강제 삭제하지 않는다. Main history도 reset/rewrite하지 않는다. Persisted `H`와 Line `in_progress`를 유지하고 실제 branch, HEAD, registration, path와 main status를 보고한다. 원인을 제거한 뒤 같은 exact `H`에서 script를 재실행한다. Recovery가 불가능한 종료만 정상 `in_progress → cancelled` transition을 사용한다.

### 4. Implementation handoff

- Line 0020 bootstrap은 user-approved combined specification `S=A < H < P < I < Q`를 사용하고 post-H `S0/S`를 만들지 않는다.
- Updated workflow의 후속 Line에서는 구현 에이전트가 clean exact Micro-SPEC draft `S0`를 만든 뒤 mutation을 멈춘다. 독립 specification reviewer는 read-only PASS/correction만 제공하고, **사용자만** exact reviewed bytes를 승인한다. Governance lead는 clean exact `S0` 위 status-only `S`를 기록하는 recorder이며 `S.parent=S0`와 substantive bytes 불변을 read-back한 뒤 exact `S`를 구현 에이전트에게 handback한다. 따라서 `A < H < S0 < S < P < I < Q`다.
- Self-approval, reviewer mutation, stale review, governance lead 단독 approval, approval과 substantive body 변경 혼합은 허용하지 않는다.
- 새 policy-bearing Line은 approval baseline `B` 뒤 별도 lifecycle-only `in_progress` commit `P`를 persist하여 `B < P < I < Q`로 진행한다.
- 기존 fieldless non-terminal Line은 fieldless lifecycle-only `in_progress`만 포함한 별도 commit `P`를 먼저 만들고, 그 다음 `implementation_history: first_parent`만 추가한 별도 commit `B`를 만든다. 따라서 `P < B < I < Q`이며 두 commit을 합치지 않는다.
- 엄격한 validator가 `P`와 `B` 사이에 보고하는 `history.line.policy.missing`이 유일한 history-policy error(sole history-policy error)이고 모든 non-history validation이 clean일 때만 이 narrow transitional gate를 통과한다. 다른 history 또는 schema·artifact·ledger 오류는 무시하지 않는다. `B` 이후 full `proofline validate`가 PASS해야 한다.
- 제품 변경 전에 Line과 대상 Micro-SPEC의 `in_progress` transition을 별도 commit `P`로 persist한다. Existing fieldless Line에서 `P`에는 policy field를 넣지 않는다.
- `P`는 approved Micro-SPEC commit보다 뒤여야 한다. 제품 변경을 `P`와 같은 commit에 섞거나 `P`를 `implementation_commit`으로 bind하지 않는다.
- Implementation commit `I`를 고정한 뒤에만 Micro-SPEC을 `implemented`로 전환하고 IQC를 작성한다. `implemented`/IQC candidate `Q`는 `I`의 strict first-parent descendant여야 한다.
- Rework는 이전 `implemented` 뒤 fresh `in_progress` commit부터 같은 순서를 반복한다. Merge의 second parent에만 존재하는 marker나 implementation은 evidence가 아니다.
- 승인된 AC만 Micro-SPEC에 배정한다.
- `criteria.satisfy` 대상은 exact approval commit에서 `active`여야 하며 implementation 중 body와 status를 변경하지 않는다.
- Micro-SPEC, implementation source, test와 IQC는 Line worktree에서 작성하고 commit한다.
- ProofLine governance command는 공용 `proofline`을 사용한다.
- 구현 대상 project의 build·test command는 해당 project 계약을 따른다.
- Main의 governance 변경이 필요하면 implementation을 멈추고 main에서 REQ를 재승인한 뒤 새 baseline을 반영한다.
- 모든 IQC와 DQC가 통과하기 전에는 fast-forward main integration 또는 delivery를 주장하지 않는다.

## DQC, Integration and Cleanup

DQC는 exact candidate commit을 검증한다. Main 통합은 기존 계약의 fast-forward gate를 유지한다. ProofLine CLI가 Git branch, worktree, commit, merge나 lifecycle transition을 대신 수행하지 않는다.

Delivery가 확인된 뒤 clean worktree만 명시적으로 정리한다.

```bash
git -C "$WORKTREE_PATH" status --porcelain
git worktree remove "$WORKTREE_PATH"
git worktree list --porcelain
```

Dirty 또는 untracked file이 있으면 중단한다. `--force`를 자동 사용하거나 branch를 자동 삭제하지 않는다.

## Common Pitfalls

1. **Main checkout에서 implementation branch로 전환함.** Main은 직렬화된 governance workspace다.
2. **Approval `A`에서 직접 branch를 만듦.** Base는 main-owned exact status-only handoff `H`다.
3. **Worktree마다 ProofLine `.venv`를 만듦.** 모든 governance workspace는 공용 `proofline`을 사용한다.
4. **Project build environment와 ProofLine tool environment를 혼동함.** 전자는 project-owned, 후자는 user-level shared tool이다.
5. **충돌을 `--force`로 우회함.** Preflight 실패는 no-mutation으로 끝낸다.
6. **DQC 전에 merge함.** IQC·DQC와 fast-forward gate를 유지한다.
7. **Lifecycle과 제품 변경을 한 commit에 섞음.** `micro_spec_commit < P < I < Q`와 adoption baseline `B < I < Q`를 유지한다. Fieldless adoption은 `P < B < I < Q`와 transitional gate를 유지한다.

## Verification Checklist

- [ ] Main branch가 `main`이고 clean하다.
- [ ] Exact approval commit `A`의 Discovery·REQ·AC·Line 상태와 exact A→H status-only diff를 직접 확인했다.
- [ ] Path, branch와 worktree registration 충돌이 없다.
- [ ] Linked worktree HEAD가 exact `H`다.
- [ ] Main checkout branch와 status가 변하지 않았다.
- [ ] Worktree에 ProofLine 전용 `.venv`가 없다.
- [ ] Worktree에서 공용 `proofline validate`가 통과한다.
- [ ] Implementation과 IQC는 worktree에 기록한다.
- [ ] Approved Micro-SPEC 뒤 별도 `in_progress` commit이 제품 implementation보다 먼저 persisted됐다.
- [ ] `proofline validate`가 exact first-parent chronology와 policy baseline을 통과한다.
- [ ] DQC와 fast-forward gate 전에는 main에 통합하지 않는다.
