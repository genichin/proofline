---
name: proofline-maintain-design-docs
description: Use when creating or updating project-owned Interface Contract, Data Model, and Runtime Flow documents from a confirmed ProofLine Discovery and draft specification without granting approval authority.
version: 1.0.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, design-docs, interface, data-model, runtime-flow]
    related_skills: [proofline-start-requirement, proofline-approve-specification]
---

# Maintain ProofLine Project Design Documents

## Overview

confirmed Discovery와 draft REQ/AC를 implementation-facing technical design으로 연결한다. 현재 project의 existing design documents와 실제 source·test·config evidence를 조사한 뒤 Interface Contract, Data Model, Runtime Flow 영향을 분류하고 project-owned `docs/`를 명시적인 사용자 범위 안에서만 작성한다.

Design documents는 `.proofline/` canonical artifact가 아니며 별도 lifecycle status나 approval metadata를 갖지 않는다. REQ가 `Normative Design Documents`에 exact project-relative path를 열거한 경우 exact document contents는 REQ와 전체 criteria AC와 함께 Specification approval 대상이 된다. **사용자만** approval authority를 가진다.

## When to Use

- confirmed Discovery에서 draft REQ/AC와 구현 기술 설계를 함께 작성할 때
- Approved specification 변경으로 existing interface, data structure 또는 runtime interaction을 갱신할 때
- 구현 agent가 반복 추론하지 않도록 exact boundary, structure와 flow를 handoff할 때

다음 목적으로 사용하지 않는다.

- Discovery confirmation, Specification self-approval 또는 REQ/AC status 전환
- AC의 observable product behavior를 design document에 복제
- `proofline project init`, 기존 문서 자동 migration 또는 범용 문서 생성
- ADR, user guide, runbook, test plan, release 또는 deployment record 관리

## Authored-content Language Guidance

사람이 작성하는 본문은 원칙적으로 한국어로 작성한다. H1 제목·구조적 heading, ID, status, YAML key, CLI, 코드, path, 고유 기술 용어 및 영어가 의미를 더 정확하게 전달하는 내용은 영어로 유지할 수 있다. 이는 authoring guidance이며 artifact 언어를 validator·QC·CI의 PASS/FAIL 조건으로 만들지 않는다.

## Preconditions

1. 실제 repository root와 Git status를 확인한다.
2. Discovery는 `confirmed`, REQ는 `draft`여야 한다.
3. `criteria.create`·`criteria.update` AC는 `draft`, `criteria.retire`·`criteria.satisfy` AC는 `active`여야 한다. `satisfy` AC는 exact bytes를 변경하지 않는다.
4. REQ의 current `Normative Design Documents` 목록과 existing project design documents를 읽는다.
5. 사용자의 explicit create/update 요청 범위를 확인한다.

승인된 design-document 의미를 변경하는 경우 먼저 관련 REQ와 영향받는 AC를 `draft`로 되돌려야 한다. 이 상태 변경은 이 skill이 승인 없이 수행하지 않는다.

## Evidence Collection

다음 owner를 직접 조사한다.

- confirmed Discovery의 Findings, Decisions, Scope, Out of Scope와 Risks
- Draft REQ의 Objective, Scope, Constraints, Non-Goals와 전체 criteria admission
- 전체 create/update/retire/satisfy AC의 Criterion과 Verification
- Existing `docs/interfaces/`, `docs/data-model/`, `docs/runtime-flows/`
- 관련 source·test·config와 target-firmware/schema/API 같은 실제 definition

현재 source는 existing-state evidence다. Approved REQ/AC 또는 사용자 결정과 충돌하는 source behavior를 normative design으로 승격하지 않는다. Evidence로 확정할 수 없는 product·policy·compatibility·scope 결정은 사용자에게 하나씩 질문하고 write를 보류한다.

## Impact Classification

각 변경을 다음 중 하나 이상으로 분류한다.

- `interface`: component boundary, caller/callee, input/output, precondition/postcondition, error 또는 compatibility가 변한다.
- `data-model`: structure, field/type, invariant, ownership/lifetime, serialization, ABI, migration 또는 producer/consumer가 변한다.
- `runtime-flow`: trigger, participant order, state transition, timeout/retry/cancellation, failure propagation, side effect 또는 recovery가 변한다.
- `no-impact`: 세 기술 계약에 의미 변화가 없다.

### No-impact Branch

`no-impact`이면 확인한 evidence와 분류 근거를 보고하고 planned/write set을 비운다. Existing design documents와 REQ의 `Normative Design Documents` 목록을 변경하지 않는다. Design-document 목록이 없는 REQ도 Specification approval 대상이 될 수 있으며 그 부재만으로 실패 처리하지 않는다.

## Owner and Path Selection

영향이 있으면 같은 기술 사실을 이미 소유하는 existing owner를 먼저 갱신한다. 독립 owner가 필요한 경우에만 다음 template과 topology로 새 문서를 계획한다.

| Family | Template | Project path |
|---|---|---|
| Interface Contract | `templates/interface-contract.md` | `docs/interfaces/<name>.md` |
| Data Model | `templates/data-model.md` | `docs/data-model/<name>.md` |
| Runtime Flow | `templates/runtime-flow.md` | `docs/runtime-flows/<name>.md` |

Target은 repository 안의 project-relative regular Markdown path여야 한다. Existing symlink, directory, unexpected file type, repository escape, unrelated user document 또는 owner ambiguity는 no-write blocker다. 다른 topology를 자동 rename, archive 또는 migrate하지 않는다.

## Planned Update Before Write

**Write 전에** 다음 planned update를 사용자에게 제시한다.

- Resolved project root
- Exact project-relative path
- `create` 또는 `update`
- Existing owner와 owner 선택 근거
- 변경할 section
- 의도된 기술 의미와 evidence

Planned update가 사용자의 explicit 요청 범위를 초과하면 write하지 않는다. 제시한 planned update 뒤에도 exact planned path와 의미만 작성하며 새 policy나 scope를 추론하여 추가하지 않는다.

## Writing Contract

- Exactly one H1과 선택한 family template의 required H2를 유지한다.
- `Related Specification`에 관련 Discovery, REQ와 AC의 exact project-relative path를 기록한다.
- Interface Contract는 callable/message boundary만, Data Model은 structure/invariant만, Runtime Flow는 interaction order와 runtime failure boundary만 소유한다.
- AC prose를 복제하지 않고 related specification으로 참조한다.
- Framework-specific table, code block와 H3은 owning H2 아래에만 추가한다.
- 실제 normative design document가 있으면 REQ `Constraints` 아래 `### Normative Design Documents` 목록에 exact path를 열거한다.
- Design document에 `draft`, `approved`, approval timestamp, approver 또는 별도 ProofLine ID를 추가하지 않는다.

## Failure, Cancellation, and Recovery

- Evidence command timeout은 실패로 전달하고 자동 retry하지 않는다. 사용자가 상태를 확인한 뒤 재실행을 요청할 수 있다.
- Write 전 cancellation은 zero project-document mutation이어야 한다.
- Write 시작 전에 각 existing target의 bytes 또는 digest를 관찰하고 전체 planned path 순서를 기록한다.
- Write 뒤 failure·timeout·cancellation이면 다음 target 전에 중단하고 `changed·unchanged·unattempted` exact path와 변경 전 관찰한 bytes/digest를 보고한다.
- Partial candidate에서는 Specification approval을 진행하지 않는다.
- 자동 retry·rollback하지 않는다. Drift를 재확인한 recovery 또는 rollback은 별도 explicit 사용자 요청으로만 수행한다.

## Approval Handoff

작성 완료 후 다음 exact 집합을 `proofline-approve-specification` workflow에 넘긴다.

- confirmed Discovery path
- Draft REQ path와 exact contents
- 전체 create/update/retire/satisfy AC path, status와 exact contents
- REQ가 열거한 모든 Normative Design Documents path와 exact document contents
- Current `proofline validate` 결과
- 남아 있는 unresolved decision 또는 partial-write 여부

Unresolved decision이나 partial candidate가 있으면 approval을 요청하지 않는다. 이 skill은 document 작성만으로 Discovery, REQ 또는 AC status를 변경하지 않는다.

## Common Pitfalls

1. **모든 Line에 세 문서를 강제함.** `no-impact`는 valid하며 목록이 없는 REQ도 승인할 수 있다.
2. **Source를 사양으로 승격함.** Source는 evidence일 뿐 approved behavior와 충돌할 때 authority가 아니다.
3. **새 문서를 먼저 만듦.** Existing owner가 있으면 update를 우선한다.
4. **Planned update 없이 write함.** Exact path와 의미를 먼저 제시해야 한다.
5. **AC를 design doc에 복제함.** Observable product behavior는 AC가 계속 소유한다.
6. **Agent가 approval status를 바꿈.** 사용자 explicit approval과 approval skill 경계 밖이다.
7. **부분 write를 성공으로 보고함.** Changed/unchanged/unattempted를 보고하고 approval을 차단한다.

## Verification Checklist

- [ ] confirmed Discovery, draft REQ와 분류별 AC status를 확인했다.
- [ ] Existing docs와 직접 source·test·config evidence를 조사했다.
- [ ] Interface/Data Model/Runtime Flow/no-impact를 분류했다.
- [ ] Existing owner를 우선하고 target path safety를 확인했다.
- [ ] Planned update를 write 전에 사용자에게 제시했다.
- [ ] Required H1/H2, Related Specification과 ownership boundary를 유지했다.
- [ ] 실제 document path를 REQ `Normative Design Documents`에 정확히 열거했다.
- [ ] Failure/cancellation 뒤 partial candidate 여부를 보고했다.
- [ ] 사용자 approval 없이 lifecycle status를 변경하지 않았다.
