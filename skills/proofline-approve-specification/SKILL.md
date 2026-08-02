---
name: proofline-approve-specification
description: Use when presenting, approving, or auditing a ProofLine REQ and newly created AC specification without turning optional transition evidence into an implementation gate.
version: 1.2.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, approval, governance, audit]
    related_skills: [proofline-start-implementation]
---

# Approve a ProofLine Specification

## Overview

사용자가 검토한 REQ·AC를 승인하고 exact specification baseline을 보존한다. Draft→status-only approval은 권장 감사 경로이지만 direct approval도 유효하다. Transition evidence가 `not recorded`여도 approval이나 implementation을 차단하지 않는다.

## When to Use

- Confirmed Discovery에서 신규 REQ와 `criteria.create` AC를 승인할 때
- `criteria.update`, `criteria.retire` 또는 `criteria.satisfy`가 포함된 REQ exact bytes를 승인할 때
- 승인 commit에 draft transition evidence가 있는지 선택적으로 확인할 때
- Exact approved commit을 implementation worktree에 전달하기 전에 specification 상태를 요약할 때

다음 목적으로 사용하지 않는다.

- 사용자를 대신한 approval 결정
- Prior draft history를 mandatory gate로 강제
- Active AC update·retirement lifecycle
- ProofLine CLI의 Git 작업 자동화

## Authority Boundary

Hermes는 REQ objective·scope·non-goals와 AC criterion·verification을 사용자에게 보여주고 명시적 approval을 기다린다. 사용자의 승인 없이 REQ를 `approved` 또는 AC를 `active`로 전환하지 않는다.

`criteria.satisfy` 대상은 승인된 active AC의 의미를 변경하지 않는 binding이다. Approval 전후에 대상 AC body와 status를 변경하지 않으며 의미 변경이 필요하면 REQ를 `criteria.update`로 고쳐 다시 승인한다.

Git commit, branch, worktree, merge와 push는 ProofLine CLI 책임이 아니다. Agent가 사용자의 결정 후 Git 명령을 실행하더라도 실제 상태와 exact commit을 보고한다.

## Approval Paths

### 권장: Draft transition 기록

1. REQ와 신규 AC를 `draft`로 작성·검증하고 commit한다.
2. 사용자에게 specification을 요약하고 approval을 받는다.
3. Approval 전에 의미가 바뀌면 draft 상태에서 먼저 별도 commit한다.
4. REQ `draft→approved`, 신규 AC `draft→active`만 변경한 status-only commit을 만든다.
5. 그 exact commit을 implementation baseline으로 사용한다.

이 경로의 optional audit 결과는 `transition: recorded`이다.

### 허용: Direct approval

사용자가 명시적으로 specification을 승인하면 prior draft commit 없이 REQ `approved`와 신규 AC `active`를 처음 기록한 exact commit도 유효한 baseline이다.

이 경로의 optional audit 결과는 `transition: not recorded`이다. 이는 audit evidence가 없다는 진단일 뿐 approval 실패가 아니며 implementation worktree 생성을 차단하지 않는다.

## Optional Read-Only Audit

Repository root에서 full approval SHA를 지정한다.

```bash
python3 ~/.proofline/skills/proofline-approve-specification/scripts/audit_transition.py \
  --repo "$PWD" \
  --line-id line-NNNN \
  --approval-commit "$APPROVAL_COMMIT"
```

Script는 지정 commit과 immediate parent의 REQ·`criteria.create` AC bytes만 읽는다.

```text
transition: recorded
transition: not recorded
```

두 결과 모두 정상 exit code `0`이다. 지정 commit이 없거나 REQ가 `approved`가 아니거나 신규 AC가 `active`가 아니면 audit target 자체가 유효하지 않으므로 non-zero diagnostic을 반환한다.

Audit는 no-mutation이다. Canonical artifact, working tree, index, ref, branch, worktree registration을 생성·수정·삭제하지 않는다.

## Implementation Handoff

Worktree preflight에는 full exact approval commit을 전달한다. Preflight는 current commit의 Discovery `confirmed`, REQ `approved`, Line `not_started`와 Git·filesystem collision을 확인한다. Draft transition history는 요구하지 않는다.

```text
recorded approval → implementation 허용
not recorded approval → implementation 허용
unapproved current state → implementation 중단
```

## Common Pitfalls

1. **Audit 결과를 approval authority로 해석함.** `recorded`는 사용자의 승인 자체를 증명하거나 대신하지 않는다.
2. **`not recorded`를 실패로 처리함.** 이 결과는 허용된 direct approval 경로이다.
3. **Approval commit에서 본문도 함께 수정함.** 권장 경로의 audit 결과가 `not recorded`가 될 수 있지만 implementation gate는 아니다.
4. **일반 validation에 Git history 검사를 추가함.** `proofline validate`는 current canonical tree만 검사한다.
5. **Agent가 자동 승인함.** Lifecycle 결정은 사용자 authority에 남는다.

## Verification Checklist

- [ ] Discovery가 confirmed이다.
- [ ] REQ와 대상 AC 내용을 사용자에게 제시했다.
- [ ] 사용자의 명시적 approval을 받았다.
- [ ] Exact approved commit을 기록했다.
- [ ] Optional audit 결과를 gate로 사용하지 않았다.
- [ ] Worktree preflight에 transition requirement를 추가하지 않았다.
- [ ] Audit 실행 전후 repository가 no-mutation이다.
