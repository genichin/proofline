---
name: proofline-start-requirement
description: Use when creating a draft ProofLine Requirement and AC scaffolds from a confirmed Discovery without granting approval authority.
version: 1.0.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, requirement, acceptance-criteria, governance]
    related_skills: [proofline-start-line]
---

# Start a ProofLine Requirement

## Preconditions

- Matching `line-NNNN`은 id-only Line이어야 한다.
- Matching Discovery는 사용자가 확인한 `confirmed` 상태여야 한다.
- 같은 Line의 `req-NNNN.md`는 없어야 한다.
- 이 skill은 REQ나 AC를 승인하지 않는다.

## Admission manifest

UTF-8 YAML 또는 JSON regular non-symlink file에 exact 네 key를 작성한다.

```yaml
create:
  - 새 criterion 제목
update:
  - ac-0001
retire: []
satisfy: []
```

`create`는 중복 없는 한 줄 제목 list이고 나머지는 existing active AC ID list다. 같은 ID를 list 안이나 여러 list에 중복하지 않는다.

## Workflow

```bash
proofline requirement init line-NNNN --manifest admission.yaml --dry-run
proofline requirement init line-NNNN --manifest admission.yaml
proofline validate
```

생성된 AC의 `Criterion`과 `Verification`, REQ의 본문을 Discovery 범위에 맞게 작성하되 status는 모두 `draft`로 유지한다. 사용자의 명시적 승인 없이 Discovery confirmation, REQ `approved` 또는 AC `active` 전환을 수행하지 않는다.
