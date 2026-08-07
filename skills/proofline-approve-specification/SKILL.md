---
name: proofline-approve-specification
description: Use when presenting, approving, or optionally auditing a ProofLine REQ and its AC specification.
version: 2.0.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, approval, governance, req, ac]
    related_skills: []
---

# Approve a ProofLine Specification

## Overview

사용자가 검토한 REQ와 대상 AC를 승인하고 exact specification baseline을 기록한다. **사용자만** approval authority를 가지며 agent는 내용을 제시하고 명시적인 결정을 기다린다. Draft에서 status만 바꾸는 approval commit은 권장 감사 경로지만 prior draft transition이 없는 direct approval도 유효하다.

## When to Use

- Confirmed Discovery에 연결된 REQ와 `criteria.create` AC를 승인할 때
- `criteria.update`, `criteria.retire` 또는 `criteria.satisfy` 집합을 승인할 때
- Approval commit에 optional draft transition evidence가 있는지 확인할 때

다음 목적으로 사용하지 않는다.

- 사용자를 대신한 approval 결정
- Line 이외의 작업·검증·배포 artifact 승인
- Git implementation 또는 delivery chronology 검사
- ProofLine CLI의 commit, branch, merge나 push 자동화

## Authority Boundary

REQ의 Objective·Scope·Non-Goals와 대상 AC의 Criterion·Verification을 사용자에게 보여주고 명시적인 approval을 기다린다. 승인 없이 REQ를 `approved`, AC를 `active` 또는 `retired`로 전환하지 않는다.

`criteria.satisfy` 대상은 기존 active AC의 의미를 변경하지 않는 binding이다. Approval 전후에 AC 본문과 status를 변경하지 않는다. 의미 변경이 필요하면 `criteria.update`로 고쳐 다시 검토한다.

## Approval Workflow

1. Confirmed Discovery, draft REQ와 대상 AC를 `proofline validate`로 확인한다.
2. REQ의 AC 변경 집합과 각 대상 AC의 exact 내용을 사용자에게 제시한다.
3. 사용자의 명시적인 approval 또는 correction을 기다린다.
4. 권장 경로에서는 REQ `draft → approved`와 대상 AC의 계약상 status만 별도 commit에 기록한다.
5. Direct approval을 선택한 경우 prior draft commit 없이 approved REQ와 대상 AC를 처음 기록할 수 있다.
6. Approval 뒤 current canonical tree를 다시 검증하고 exact commit을 보고한다.

Approval commit에 본문 변경을 섞지 않는 것이 권장되지만 ProofLine validator는 Git chronology나 사람의 identity를 인증하지 않는다.

## Optional Read-Only Audit

`audit_transition.py`는 지정한 approval commit과 immediate parent에서 REQ와 `criteria.create` AC의 status-only transition이 기록됐는지만 읽는다.

```bash
python3 ~/.proofline/skills/proofline-approve-specification/scripts/audit_transition.py \
  --repo "$PWD" \
  --line-id line-NNNN \
  --approval-commit "$APPROVAL_COMMIT"
```

```text
transition: recorded
transition: not recorded
```

두 결과 모두 유효하다. 이 helper는 REQ와 신규 AC만 다루며 approval authority, implementation gate 또는 chronology validator가 아니다. Canonical artifact, worktree, index와 ref를 변경하지 않는다. Update·retire·satisfy approval은 helper 결과가 아니라 사용자의 결정과 current canonical validation으로 확인한다.

## Common Pitfalls

1. **Audit를 사용자 approval로 해석함.** `recorded`는 status transition 모양만 설명한다.
2. **`not recorded`를 실패로 처리함.** Direct approval은 허용된다.
3. **Agent가 자동 승인함.** Specification 결정은 사용자 authority다.
4. **REQ에 AC 내용을 복제함.** AC 상세 내용은 각 AC 파일이 소유한다.
5. **Validator가 Git chronology를 보증한다고 가정함.** `proofline validate`는 현재 canonical tree를 검사한다.

## Verification Checklist

- [ ] Discovery가 confirmed이다.
- [ ] REQ와 대상 AC exact 내용을 사용자에게 제시했다.
- [ ] 사용자의 명시적 approval을 받았다.
- [ ] REQ와 AC의 계약상 status만 전환했다.
- [ ] Current canonical validation이 통과한다.
- [ ] Exact approval commit을 보고했다.
- [ ] Optional audit 결과를 approval 또는 작업 gate로 사용하지 않았다.
