---
name: proofline-start-line
description: Use when starting a new ProofLine Line and writing its evidence-grounded Discovery draft without granting the agent confirmation authority.
version: 1.3.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, discovery, governance, software-development]
    related_skills: []
---

# Start a ProofLine Line

## Overview

새 ProofLine Line을 안전하게 scaffold한 뒤 현재 project의 직접 evidence를 조사하여 Discovery draft를 작성한다. CLI는 결정론적 파일 생성만 담당하고 Hermes는 조사와 문서 작성을 담당한다. 이 skill은 Discovery를 `confirmed`로 전환하거나 사용자 대신 governance 결정을 내리지 않는다.

## When to Use

- 사용자가 새 ProofLine Line이나 Discovery를 시작하라고 요청할 때
- 기존 Line에 독립 Requirement를 추가하려 할 때 새 Line으로 분리해야 할 때
- 아직 `.proofline/lines/line-NNNN/` artifact가 없는 delivery를 조사할 때

다음에는 사용하지 않는다.

- 이미 존재하는 Discovery를 확인 없이 다시 생성할 때
- REQ·AC 승인이나 구현·검증·delivery를 자동화할 때
- ProofLine이 아닌 일반적인 아이디어 메모를 작성할 때

## Preconditions

1. 실제 project root에서 작업한다.
2. `proofline.yaml`이 schema version 1과 `.proofline` root를 선언하는지 확인한다.
3. Git working tree와 현재 branch를 확인한다.
4. 사용자에게 제목을 확인한다. Line ID는 canonical allocator가 자동 할당한다.
5. Credential, token, password, connection string은 evidence에 기록하지 않는다. 필요한 값은 `[REDACTED]`로 대체한다.

## Workflow

### 1. Dry-run으로 scaffold를 preflight한다

```bash
proofline line init --title "TITLE" --dry-run
```

예정된 두 path, ID history, 충돌, symlink와 template validation 결과를 확인한다. 실패하면 파일을 직접 만들어 우회하지 말고 원인을 해결하거나 사용자에게 blocker를 보고한다.

### 2. Canonical scaffold를 생성한다

```bash
proofline line init --title "TITLE"
```

다음 두 canonical artifact가 생성되고 identity reuse 방지를 위한
`.proofline/identities.json`만 함께 갱신돼야 한다.

```text
.proofline/lines/line-NNNN/line-NNNN.md
.proofline/lines/line-NNNN/dcy-NNNN.md
.proofline/identities.json  # next_line_number 전진
```

Line artifact는 stable `id`와 정보 표시용 `status: discovery`를 가지며 Discovery는 `status: draft`로 생성된다. CLI는 Discovery의 실질적 내용을 판단하지 않는다. 생성 직후 `proofline validate`를 실행한다.

### 3. 직접 evidence를 먼저 조사한다

내용을 쓰기 전에 가능한 원본 source를 직접 확인한다.

우선순위:

1. 사용자가 지정한 URL, issue, file, device 또는 live system
2. 현재 source, test, config, contract와 실행 결과
3. 현재 Git status, relevant history와 exact revision
4. 필요할 때 공식 외부 문서
5. 과거 대화는 보조 맥락으로만 사용하고 현재 source의 대체 근거로 사용하지 않는다.

Evidence가 부족하면 추측으로 채우지 않고 `{{NEEDS_EVIDENCE: ...}}` 또는 Open Question을 사용한다.

### 4. AC admission을 분류한다

REQ 작성 전에 변경을 `create`, `update`, `retire`, 기존 active AC를 변경하지 않는 `satisfy`, `release evidence`, 또는 Line 밖 `housekeeping`으로 분류한다. `create` 전에 가장 가까운 active AC, `update` 가능성, version-independent 동작인지, 독립 PASS/FAIL과 장기 active 가치가 있는지 확인한다.

Version·tag·checksum 같은 release-specific 표현은 review warning으로 표시하되 validator hard error로 단정하지 않는다. `create/update/satisfy`가 불명확하면 사용자가 결정할 Open Question으로 남긴다.

## Authored-content Language Guidance

본문은 원칙적으로 한국어로 작성한다. 영어를 단순히 단어만 한국어로 대체하는것이 아닌 자연스러운 한국어 표현으로 작성하여야한다. H1 제목·구조적 heading, ID, status, YAML key, CLI, 코드, path, 고유 기술 용어 및 영어가 의미를 더 정확하게 전달하는 내용은 영어로 유지할 수 있다. 이는 authoring guidance이며 artifact 언어를 validator·QC·CI의 PASS/FAIL 조건으로 만들지 않는다.

## Discovery 작성

### `Problem`

현재 관찰되는 문제와 변경 필요성을 쓴다. 제안한 solution을 문제처럼 쓰지 않는다.

### `Evidence`

확인한 file, command, test 결과, URL 또는 exact revision을 기록한다. “코드가 없다” 같은 부정적 결론은 검색 범위와 함께 기록한다.

### `Scope`

이번 Line이 바꿀 관찰 가능한 behavior와 governance boundary를 쓴다. 구현 편의보다 승인할 제품 결과를 우선한다.

### `Out of Scope`

인접하지만 독립 Requirement인 항목, 자동화하지 않을 authority와 후속 Line으로 미룰 항목을 명시한다.

### `Risks and Unknowns`

실제 risk나 미확정 사항이 있을 때만 추가한다. 단순한 TODO 목록으로 사용하지 않는다.

## Open Question Gate

직접 evidence만으로 해소할 수 없고 scope, policy, compatibility, risk acceptance 또는 PASS/FAIL 의미를 바꿀 수 있는 질문은 다음 형식으로 기록한다.

```markdown
- `OQ-001`
  - Type: `DECIDE`
  - Status: {{TODO: 사용자 결정 필요}}
  - Question: 결정해야 할 구체적인 질문
  - Owner: 결정 권한을 가진 사용자 또는 역할
  - Exit Condition: 어느 답이 canonical section에 반영되면 해소되는지
```

해소된 답과 근거는 `Evidence`, `Scope`, `Out of Scope` 또는 다른 canonical owner section에 반영한 뒤 current Open Question entry를 제거한다. Git history가 과거 질문을 보존한다.

다음 행동은 금지한다.

- 근거 없이 Open Question을 임의로 답함
- 사용자 대신 product, policy, priority 또는 scope를 결정함
- unresolved blocking question이 있는데 confirmation을 권고함
- Discovery를 `confirmed`로 전환하지 않는다

## Validation and Handoff

1. 생성된 Discovery에 정확히 하나의 H1과 필수 H2가 있는지 확인한다.
2. Frontmatter placeholder가 없는지 확인한다.
3. 직접 evidence와 주장 사이의 연결을 검토한다.
4. `proofline validate`를 실행한다.
5. Discovery는 `status: draft`로 유지한다.
6. 사용자에게 scope, evidence, Open Question과 confirmation gate를 보고한다.
7. 사용자의 명시적인 confirmation 없이는 Discovery를 `confirmed`로 전환하거나 REQ 승인으로 진행하지 않는다.

이 workflow는 repository-owned source만 작성하며 live Hermes profile을 변경하지 않는다. 설치와 registry publish는 별도 rollout이다.

## Common Pitfalls

1. **CLI가 Discovery 내용도 생성한다고 가정함.** CLI는 scaffold와 validation만 담당한다. 의미 있는 내용은 Hermes가 직접 evidence를 조사해 작성한다.
2. **과거 대화를 현재 source보다 우선함.** 과거 대화는 맥락이며 현재 file, URL 또는 live system을 먼저 확인한다.
3. **ID를 직접 선택함.** Stable identity는 allocator가 lock 안에서 자동 선택한다.
4. **Draft를 곧바로 confirmed로 바꿈.** Skill에는 confirmation authority가 없다.
5. **Secret을 evidence로 복사함.** Credential 값은 보존하지 않고 `[REDACTED]`로 대체한다.
6. **CLI 실패를 수동 file write로 우회함.** 충돌과 history 검사는 governance guard이므로 우회하지 않는다.

## Verification Checklist

- [ ] 사용자가 제목을 명시했다.
- [ ] Dry-run이 통과했다.
- [ ] 두 canonical artifact가 생성되고 identity allocator만 함께 갱신됐다.
- [ ] Line artifact는 `id`와 정보 표시용 `status: discovery`를 가진다.
- [ ] 직접 evidence를 확인한 뒤 Discovery를 작성했다.
- [ ] create/update/retire/satisfy/release evidence/housekeeping 분류와 가장 가까운 active AC를 검토했다.
- [ ] 필수 section이 실질적 내용을 갖거나 허용된 draft placeholder를 사용한다.
- [ ] Blocking decision은 Owner와 Exit Condition을 가진 Open Question이다.
- [ ] Credential 값이 없으며 필요한 값은 `[REDACTED]`다.
- [ ] `proofline validate`가 통과한다.
- [ ] Discovery는 `draft`이며 사용자의 confirmation을 기다린다.
