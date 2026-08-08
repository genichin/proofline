---
name: proofline-create-worktree
description: Use when optionally creating one scoped linked worktree after a ProofLine REQ and its target ACs have been explicitly approved.
version: 1.0.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, git, worktree, implementation]
    related_skills: [proofline-approve-specification]
---

# Create a ProofLine Implementation Worktree

## Overview

승인된 REQ와 대상 AC를 구현할 때 linked worktree creation을 **optional next action**으로 제공한다. Worktree를 만들지 않고 main에서 직접 구현하는 경로도 유효하며 validator·QC·CI의 필수 gate가 아니다.

이 skill은 read-only advisory helper와 한 번의 create-only 절차만 소유한다. Implementation lifecycle, cleanup, retry, rollback, repair, merge 또는 release authority는 소유하지 않는다.

## Inputs

- Repository root의 resolved absolute path
- `line-NNNN` 형식의 Line ID
- 사용자가 승인한 REQ와 대상 AC
- `proofline-tool-environment`가 제공하는 공용 `proofline` executable

## Inspect Readiness

Managed skill root에서 다음 documented invocation을 실행한다.

```bash
"$PYTHON" "$MANAGED_SKILL_ROOT/scripts/inspect_worktree_readiness.py" \
  --repository "$REPOSITORY_ROOT" \
  --line "$LINE_ID"
```

Helper는 read-only다. Canonical artifact, Git ref·index·worktree registration 또는 target path를 생성·변경·삭제하지 않는다. Stdout에는 `advisory`, `recommendation`, `observations`, `reasons`를 가진 JSON object 하나만 출력한다.

- `recommendation: create`: 모든 readiness observation이 충족된 advisory 결과다.
- `recommendation: review`: 정상 inspection 결과이며 permission denial이나 실패가 아니다. Reasons의 순서는 contract가 아니다.
- Non-zero: invalid invocation 또는 필수 observation을 얻거나 해석하지 못한 경우다. 이 경우 success JSON은 없다.

JSON과 reasons를 사용자에게 제시한 뒤 다음 선택을 기다린다.

1. Worktree를 생성하지 않고 main에서 직접 구현한다.
2. Worktree를 생성하고 현재 agent가 그 worktree에서 계속한다.
3. Worktree를 생성하고 다른 agent·subagent에게 인계한다.

어느 선택도 자동 선택하거나 필수로 만들지 않는다.

## Collision Boundary

Target local branch, target filesystem path 또는 target branch/path registration collision이 있으면 mutation command를 실행하지 않고 충돌을 보고한다. `recommendation: review`의 다른 reason은 먼저 사용자에게 제시하고, **사용자가 명시적으로 생성을 선택**한 경우에만 다음 절차를 진행할 수 있다.

다음을 사용하지 않는다.

- Force option
- Automatic suffix 또는 alternate branch/path
- Existing branch reset·delete
- Existing path removal
- Automatic retry·repair·cleanup·rollback

## Optional Creation

Helper JSON에서 다음 값을 읽는다.

```text
PRIMARY_WORKTREE = observations.primary_worktree.path
TARGET_REF = observations.target.ref
TARGET_PATH = observations.target.path
```

Helper가 관찰한 HEAD는 참고 정보이며 생성 허가나 중단 조건으로 사용하지 않는다. Collision이 없으면 exact branch와 path를 사용하고, 생성 명령 시점의 `HEAD`를 Git에 직접 전달한다. 아래 명령은 `git worktree add -b`의 exact create-only 경로다.

```bash
git -C "$PRIMARY_WORKTREE" worktree add -b "line/$LINE_ID-implementation" \
  "$TARGET_PATH" HEAD
```

Creation 뒤 다음 값을 read-back한다.

```bash
git -C "$TARGET_PATH" rev-parse --show-toplevel
git -C "$TARGET_PATH" branch --show-current
git -C "$TARGET_PATH" rev-parse HEAD
git -C "$TARGET_PATH" status --porcelain --untracked-files=all
```

Read-back 결과는 exact resolved path, attached `line/line-NNNN-implementation` branch, 생성된 worktree의 HEAD commit과 clean status여야 한다. 이 HEAD commit을 실제 starting commit으로 보고한다.

## Continue or Handoff

### Current agent continues

현재 agent가 생성된 worktree에서 계속하기로 선택하면 working directory만 exact worktree path로 전환한다. 별도 handoff context나 canonical state를 만들지 않는다.

### Another agent or subagent

다른 agent·subagent에게 인계하기로 선택한 경우에만 session context에 다음을 포함한다.

- Resolved absolute worktree path
- Attached branch
- 생성된 worktree에서 read-back한 HEAD commit
- Approved REQ ID와 canonical path
- 모든 대상 AC ID와 canonical path
- Main이 아니라 생성된 worktree만 implementation working directory로 사용한다는 경계
- Approved specification의 의미를 변경하지 않는다는 경계

이는 session context이며 structured canonical payload나 persistent handoff record가 아니다.

Main에서는 lead 또는 governance agent가 별도의 신규 Line·Discovery 작업을 계속할 수 있다. Linked worktree의 implementation branch와 main의 후속 governance 작업은 자동으로 통합하지 않는다.

## Verification Checklist

- [ ] Helper invocation이 exit `0`의 `create|review` advisory 또는 명확한 non-zero observation error를 반환했다.
- [ ] Inspection 전후 canonical bytes, refs, index, worktree registrations와 target path가 바뀌지 않았다.
- [ ] 사용자가 worktree 미생성·현재 agent 계속·다른 agent 인계 중 하나를 선택했다.
- [ ] Exact branch·path와 생성 명령 시점의 `HEAD`로 한 번만 생성하고 path·branch·starting commit·status를 read-back했다.
- [ ] Handoff를 선택한 경우에만 완전한 session context를 제공했다.
