---
name: proofline-start-implementation
description: Use when starting implementation for an approved ProofLine Line in an exact-baseline Git linked worktree while preserving main as the governance checkout.
version: 1.1.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, git-worktree, implementation, governance]
    related_skills: []
---

# Start ProofLine Implementation

## Overview

승인된 Line의 implementation을 exact REQ approval commit에서 `.worktrees/line-NNNN/` linked worktree로 시작한다. Main checkout은 `main`의 clean governance workspace로 유지한다. 이 workflow는 Git을 명시적으로 운용하지만 ProofLine CLI가 Git branch, worktree, commit, merge 또는 lifecycle transition을 수행하도록 확장하지 않는다.

Main과 모든 Line worktree의 governance 명령은 user-level `uv tool`에 설치된 공용 `proofline` executable을 사용한다. Worktree에는 ProofLine 전용 `.venv`를 생성하지 않는다. 구현 대상 project의 source build·test environment는 그 project의 계약을 따른다.

## When to Use

- Confirmed Discovery와 approved REQ가 있는 Line의 implementation을 시작할 때
- Main checkout을 governance 전용으로 유지하면서 별도 linked worktree가 필요할 때
- 기존 worktree의 exact approval base, branch, path와 shared tool 경계를 검증할 때

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

직접 읽은 exact approval commit의 artifact에서 다음 상태를 확인한다.

```text
Discovery.status = confirmed
REQ.status = approved
Line.execution_status = not_started
REQ identity와 Line identity의 NNNN 일치
```

그 다음 path 충돌, branch 충돌과 기존 worktree registration을 확인한다.

```bash
test ! -e "$WORKTREE_PATH"
! git show-ref --verify --quiet "refs/heads/$BRANCH"
git worktree list --porcelain
```

`git worktree list --porcelain`에 같은 path 또는 branch가 있으면 중단한다. 실패를 수동 삭제, 다른 ID 또는 `--force`로 우회하지 않는다.

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
test "$(git -C "$WORKTREE_PATH" rev-parse HEAD)" = "$APPROVAL_COMMIT"
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

검증 실패 시 worktree를 자동으로 강제 삭제하지 않는다. 실제 branch, HEAD, registration, path와 main status를 보고하고 수동 recovery 결정을 받는다.

### 4. Implementation handoff

- 승인된 AC만 Micro-SPEC에 배정한다.
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
2. **Latest main에서 branch를 만듦.** Base는 같은 Line의 exact REQ approval commit이다.
3. **Worktree마다 ProofLine `.venv`를 만듦.** 모든 governance workspace는 공용 `proofline`을 사용한다.
4. **Project build environment와 ProofLine tool environment를 혼동함.** 전자는 project-owned, 후자는 user-level shared tool이다.
5. **충돌을 `--force`로 우회함.** Preflight 실패는 no-mutation으로 끝낸다.
6. **DQC 전에 merge함.** IQC·DQC와 fast-forward gate를 유지한다.

## Verification Checklist

- [ ] Main branch가 `main`이고 clean하다.
- [ ] Exact approval commit의 Discovery·REQ·Line 상태를 직접 확인했다.
- [ ] Path, branch와 worktree registration 충돌이 없다.
- [ ] Linked worktree HEAD가 exact approval commit이다.
- [ ] Main checkout branch와 status가 변하지 않았다.
- [ ] Worktree에 ProofLine 전용 `.venv`가 없다.
- [ ] Worktree에서 공용 `proofline validate`가 통과한다.
- [ ] Implementation과 IQC는 worktree에 기록한다.
- [ ] DQC와 fast-forward gate 전에는 main에 통합하지 않는다.
