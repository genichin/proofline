---
name: proofline-run-iqc
description: Use when verifying one implemented ProofLine Micro-SPEC and recording exact first-parent implementation evidence in its IQC artifact.
version: 1.0.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, iqc, implementation, verification, git-history]
    related_skills: [proofline-start-implementation, proofline-run-dqc]
---

# Run ProofLine IQC

## Overview

한 Micro-SPEC의 exact approved specification과 implementation commit을 검증하고 canonical IQC에 실제 결과를 기록한다. `implementation_history: first_parent` Line에서는 IQC admission 전에 persisted lifecycle chronology를 확인한다. Workflow는 test와 Git commit을 직접 실행하지만 ProofLine CLI가 lifecycle이나 Git state를 자동 변경하게 만들지 않는다.

## When to Use

- 별도 `in_progress` commit 뒤 implementation commit을 고정하고 focused 검증을 실행할 때
- 최초 implementation 또는 rework의 IQC를 갱신할 때
- Source와 installed artifact evidence를 exact commit에 bind할 때

다음 목적으로 사용하지 않는다.

- Implementation 이전에 결과를 미리 `passed`로 기록
- Second-parent-only 또는 unresolved commit을 evidence로 사용
- DQC, main integration, delivery 또는 release를 대신 승인

## Preconditions

다음을 artifact와 Git에서 직접 확인한다.

```text
Line.implementation_history = first_parent
Micro-SPEC.spec_status = approved
Micro-SPEC.implementation_status = in_progress
M = exact approved Micro-SPEC commit
P = latest persisted in_progress transition
I = exact implementation commit
B = Line policy adoption baseline
```

Exact candidate first-parent chain에서 `M < P < I`와 `B < I`여야 한다. `P = I`, `I < B`, second-parent-only `P`/`I`, lifecycle-only `I` 또는 shallow/missing history이면 IQC를 진행하지 않는다.

## Workflow

### 1. Exact binding 확인

```bash
git rev-parse HEAD
git rev-list --first-parent --reverse HEAD
git cat-file -e "$MICRO_SPEC_COMMIT^{commit}"
git cat-file -e "$IMPLEMENTATION_COMMIT^{commit}"
proofline validate
```

Validation이 history diagnostic을 반환하면 artifact나 Git history를 자동 repair하지 않는다. Approved Micro-SPEC, fresh `in_progress`, implementation 순서를 별도 first-parent commit으로 바로잡은 뒤 다시 시작한다.

### 2. Focused verification 실행

Micro-SPEC `Verification`과 담당 AC가 요구하는 source test, lint, build, package 또는 installed-artifact 검사를 `I` bytes에 대해 실행한다. 실제 command, exit code, 결과 요약과 stable evidence reference를 보존한다. 제품 path 존재만으로 semantic 구현 품질을 추론하지 않는다.

### 3. 후속 candidate Q 기록

같은 Micro-SPEC을 `implemented`로 전환하고 고정 IQC path를 작성 또는 갱신한다.

```yaml
micro_spec_commit: "<M full SHA>"
implementation_commit: "<I full SHA>"
result: passed | failed | blocked
```

Micro-SPEC transition과 IQC를 포함한 후속 commit `Q`를 만든다. `I < Q`여야 하며 `I = Q`는 허용하지 않는다. Rework에서는 이전 `implemented` 뒤 fresh `P`부터 전체 cycle을 반복한다.

### 4. Candidate 검증

```bash
test -z "$(git status --porcelain)"
proofline validate
git rev-list --first-parent --reverse HEAD
```

`proofline validate`가 `micro_spec_commit < P < I < Q`와 `B < I < Q`를 통과해야 IQC evidence가 admissible하다. 검증은 canonical bytes, index, worktree, HEAD, refs와 Git object database를 변경하지 않아야 한다.

## Common Pitfalls

1. **`in_progress`와 제품 변경을 같은 commit에 기록함.** 별도 `P`를 먼저 persist한다.
2. **제품 commit과 `implemented`/IQC를 같은 commit에 기록함.** Exact `I`를 먼저 고정하고 후속 `Q`에 lifecycle evidence를 기록한다.
3. **Rework에서 이전 P를 재사용함.** 이전 `implemented` 뒤 fresh transition이 필요하다.
4. **Merge reachability를 first-parent evidence로 오해함.** Exact candidate의 first-parent chain만 인정한다.
5. **History unavailable을 warning으로 무시함.** Missing object, shallow clone, malformed historical artifact와 unresolved SHA는 fail-closed blocker다.

## Verification Checklist

- [ ] Exact approved Micro-SPEC commit `M`을 기록했다.
- [ ] 별도 persisted `P`가 `M` 뒤에 있다.
- [ ] Exact implementation `I`가 `P`와 adoption baseline `B` 뒤에 있다.
- [ ] 실제 검사를 `I`에 대해 실행하고 결과를 기록했다.
- [ ] `implemented`와 IQC candidate `Q`가 `I` 뒤에 있다.
- [ ] Rework라면 fresh `P → I → Q` cycle을 사용했다.
- [ ] Source `proofline validate`가 통과하고 validation이 repository를 변경하지 않았다.
- [ ] IQC PASS가 DQC, integration 또는 delivery 승인으로 표현되지 않았다.
