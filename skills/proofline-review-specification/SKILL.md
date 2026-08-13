---
name: proofline-review-specification
description: Use when reviewing a draft ProofLine REQ and all affected ACs before explicit user approval without changing specification or Git state.
version: 1.0.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, review, specification, req, ac, governance]
    related_skills: [proofline-start-requirement, proofline-approve-specification]
---

# Review a ProofLine Specification

## Overview

상태가 `confirmed`인 Discovery를 바탕으로 작성된 `draft REQ`와 모든 대상 AC를 사용자 승인 전에 읽기 전용으로 검토한다. 검토자는 승인 전에 반드시 고쳐야 하는 문제를 찾고 `Verdict: PASS` 또는 `Verdict: BLOCK`으로 보고한다.

이 스킬은 문서 작성이나 승인을 담당하지 않는다. `PASS`는 사용자가 검토할 준비가 되었다는 뜻일 뿐이며 사용자 승인을 대신하지 않는다. 승인과 상태 전환은 별도의 `proofline-approve-specification` 절차가 담당한다.

## Authored-content Language Guidance

사람이 작성하는 본문은 원칙적으로 한국어로 작성한다. H1 제목·구조적 heading, ID, status, YAML key, CLI, 코드, path, 고유 기술 용어 및 영어가 의미를 더 정확하게 전달하는 내용은 영어로 유지할 수 있다. 이는 authoring guidance이며 artifact 언어를 validator·QC·CI의 PASS/FAIL 조건으로 만들지 않는다.

## Preconditions

1. 실제 저장소 root, 현재 branch와 Git status를 확인한다.
2. `proofline validate`가 구조 검사를 통과하는지 확인한다. 구조 검사의 PASS는 의미 검토의 PASS를 대신하지 않는다.
3. Discovery는 `confirmed`, REQ는 `draft`여야 한다.
4. `create`와 `update` AC는 `draft`, `retire`와 `satisfy` AC는 `active`여야 한다.
5. 검토자는 검토 전후 문서와 Git 상태를 변경하지 않는다.

## 검토 대상 문서 집합

다음 문서를 일부도 빠뜨리지 않고 모두 읽는다.

- 상태가 `confirmed`인 Discovery
- 상태가 `draft`인 REQ
- REQ의 `create`, `update`, `retire`, `satisfy`에 포함된 모든 AC
- REQ에 적힌 모든 `Normative Design Documents`

`Normative Design Documents` 목록이 없는 REQ도 정상적으로 검토할 수 있다. 일부 AC나 문서를 빠뜨린 채 전체 specification을 `PASS`로 판정하지 않는다.

참조된 설계 문서는 repository 안의 existing regular Markdown file이어야 한다. symlink, repository escape, missing path 또는 unexpected file type이면 `BLOCK`이다. 문서 전체 내용을 REQ와 모든 AC의 의미에 대조한다.

## Discovery와 REQ 검토

다음을 확인한다.

- Discovery의 문제, 근거, 범위와 제외 범위가 REQ에 빠짐없이 반영되었는가
- REQ가 Discovery에서 확인한 범위를 넘어가지 않는지
- Objective, Scope, Constraints와 Non-Goals가 서로 모순되지 않는가
- 구현자가 추가 제품 결정을 내려야 할 모호성이 남아 있지 않은가
- 자리표시자나 승인을 막는 해결되지 않은 질문이 남아 있지 않은가

## REQ와 AC 검토

다음을 확인한다.

- REQ가 참조한 모든 AC가 존재하고 분류별 status가 올바른가
- `create`, `update`, `retire`, `satisfy` 분류가 AC의 실제 의미 변화에 맞는가
- 각 AC가 하나의 지속적인 요구사항을 독립적으로 표현하는가
- 각 AC가 버전과 구현 방법에 종속되지 않는가
- 각 Criterion을 관찰 가능한 결과로 PASS 또는 FAIL 판정할 수 있는가
- Verification이 Criterion 전체를 검사하는가
- 필요한 정상, 실패, 경계 및 변경 없음 조건을 포함하는가
- 전체 AC 집합이 REQ 범위를 빠짐없이 덮는가
- AC 사이에 누락, 중복, 모순, 고립 또는 범위 초과가 없는가
- 기존 active AC와 충돌하거나 불필요하게 중복되지 않는가

## Findings

승인 차단 문제와 `non-blocking notes`를 분리한다.

승인 차단 문제에는 반드시 다음을 함께 기록한다.

1. 판단 근거가 되는 승인된 요구사항 또는 저장소 규칙
2. 문제가 되는 구체적인 문구나 동작
3. 문제를 해결하는 데 필요한 최소 수정

근거 조항이 없는 선택적 개선, 새로운 정책 또는 optional hardening은 승인 차단 문제로 승격하지 않는다. 유용한 제안이면 non-blocking note로 기록한다.

## Report Contract

다음 형식을 사용한다.

```text
Verdict: PASS | BLOCK
Mutation performed: false

검토한 문서의 경로와 식별정보:
- <path>: <SHA-256 또는 Git blob/commit identity>

요구사항 반영표:
- <Discovery 근거> → <REQ 범위> → <AC 판정 조건>

승인 차단 문제:
- 없음
  또는
- 근거 → 구체적인 문제 → 최소 수정

승인 차단이 아닌 참고 사항:
- <non-blocking note 또는 없음>

무변경 확인:
- 문서 내용과 lifecycle status를 변경하지 않음
- Git HEAD, index와 worktree를 변경하지 않음
```

승인 차단 문제가 하나라도 있으면 `Verdict: BLOCK`이다. 문제가 없을 때만 `Verdict: PASS`를 사용한다. 민감정보, 인증정보, token 또는 connection string은 출력하지 않고 `[REDACTED]`로 가린다.

## 변경과 재검토

검토한 문서의 내용이나 구성 문서가 바뀌면 이전 결과는 더 이상 적용되지 않는다. 수정된 전체 문서 집합을 다시 읽고 검토하며 이전 PASS를 재사용하지 않는다. 검토 중 내용이 바뀌면 즉시 `BLOCK`으로 보고하고 현재 검토를 종료한다.

## Authority Boundary

검토자는 다음을 수행하지 않는다.

- Discovery, REQ, AC 또는 설계 문서 작성·교정
- lifecycle status 변경
- Git add, commit, branch, worktree, push 또는 publication
- Discovery confirmation
- REQ·AC approval
- 사용자를 대신한 approval prompt 또는 승인 결정

검토 결과가 `PASS`이면 실제로 검토한 문서 전체와 결과를 `proofline-approve-specification`에 전달한다. 사용자만 명세를 승인하며, 승인 뒤 상태 전환은 approval skill이 수행한다.

## Verification Checklist

- [ ] 저장소 root, branch와 Git status를 확인했다.
- [ ] `proofline validate` 구조 검사를 확인했다.
- [ ] confirmed Discovery, draft REQ와 분류별 모든 AC를 읽었다.
- [ ] 모든 Normative Design Documents를 전체 내용으로 검토했다.
- [ ] Discovery→REQ→AC 요구사항 반영표를 작성했다.
- [ ] 각 blocker에 판단 근거, 구체적인 문제와 최소 수정이 있다.
- [ ] Optional hardening을 blocker로 승격하지 않았다.
- [ ] `Mutation performed: false`와 실제 무변경을 확인했다.
- [ ] PASS가 사용자 승인을 대신하지 않는다고 명시했다.
