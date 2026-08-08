---
name: proofline-approve-specification
description: Use when presenting and approving a ProofLine REQ and its AC specification through explicit user authority and current canonical validation.
version: 3.2.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, approval, governance, req, ac]
    related_skills: []
---

# Approve a ProofLine Specification

## Overview

사용자가 검토한 REQ와 대상 AC를 승인하고 current canonical specification baseline을 형성한다. **사용자만** approval authority를 가지며 agent는 exact 내용을 제시하고 명시적인 결정을 기다린다.

## When to Use

- Confirmed Discovery에 연결된 REQ와 `criteria.create` AC를 승인할 때
- `criteria.update`, `criteria.retire` 또는 `criteria.satisfy` 집합을 승인할 때

다음 목적으로 사용하지 않는다.

- 사용자를 대신한 approval 결정
- Line 이외의 작업·검증·배포 artifact 승인
- Git chronology 검사
- ProofLine CLI의 commit, branch, merge나 push 자동화

## Authority Boundary

REQ의 Objective·Scope·Non-Goals와 대상 AC의 Criterion·Verification을 사용자에게 보여주고 명시적인 approval을 기다린다. 승인 없이 REQ를 `approved`, AC를 `active` 또는 `retired`로 전환하지 않는다.

`criteria.satisfy` 대상은 기존 active AC의 의미를 변경하지 않는 binding이다. Approval 전후에 AC 본문과 status를 변경하지 않는다. 의미 변경이 필요하면 `criteria.update`로 고쳐 다시 검토한다.

## Authored-content Language Guidance

사람이 작성하는 본문은 원칙적으로 한국어로 작성한다. H1 제목·구조적 heading, ID, status, YAML key, CLI, 코드, path, 고유 기술 용어 및 영어가 의미를 더 정확하게 전달하는 내용은 영어로 유지할 수 있다. 이는 authoring guidance이며 artifact 언어를 validator·QC·CI의 PASS/FAIL 조건으로 만들지 않는다.

## Approval Workflow

1. Confirmed Discovery, draft REQ와 대상 AC를 `proofline validate`로 확인한다.
2. REQ의 AC 변경 집합과 각 대상 AC의 exact 내용을 사용자에게 제시한다.
3. 사용자의 명시적인 approval 또는 correction을 기다린다.
4. Approval 뒤 REQ를 `approved`, `criteria.create`·`criteria.update` AC를 `active`, `criteria.retire` AC를 `retired`로 전환한다. `criteria.satisfy` 대상의 본문과 status는 변경하지 않는다.
5. `proofline validate`로 current canonical tree를 검증한다.
6. 승인된 REQ·AC path와 status 및 validation 결과를 사용자에게 보고한다.
7. 구현을 계속할 경우 `proofline-create-worktree`를 **optional next action**으로 제시한다. 사용자는 worktree 생성, main에서 직접 구현 또는 여기서 중단을 선택할 수 있으며 worktree를 approval·validator·QC·CI의 필수 gate로 만들지 않는다.

Git commit은 승인된 specification revision을 일반 history로 보존할 수 있지만 approval authority 또는 approval transition validation 입력이 아니다. `criteria.satisfy`의 historical active binding은 contract의 별도 lifecycle 규칙을 따른다.

## Common Pitfalls

1. **Agent가 자동 승인함.** Specification 결정은 사용자 authority다.
2. **REQ에 AC 내용을 복제함.** AC 상세 내용은 각 AC 파일이 소유한다.
3. **`criteria.satisfy` AC를 변경함.** Satisfy는 기존 active AC의 exact 의미를 유지한다.
4. **Git history를 approval authority로 해석함.** `proofline validate`는 current canonical tree를 검사한다.

## Verification Checklist

- [ ] Discovery가 confirmed이다.
- [ ] REQ와 대상 AC exact 내용을 사용자에게 제시했다.
- [ ] 사용자의 명시적 approval을 받았다.
- [ ] REQ와 대상 AC를 계약상 status로 전환했다.
- [ ] Current canonical validation이 통과한다.
- [ ] 승인된 path·status와 validation 결과를 보고했다.
