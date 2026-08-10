# Project Design Document Maintenance Flow

## Purpose

Confirmed Discovery에서 design-document impact를 조사하고 draft REQ/AC와 exact technical baseline을 함께 작성·승인하며, 이후 의미 변경을 다시 governance로 돌려보내는 runtime flow를 정의한다.

## Related Specification

- Discovery: `.proofline/lines/line-0034/dcy-0034.md`
- Requirement: `.proofline/lines/line-0034/req-0034.md`
- Criteria: `.proofline/criteria/ac-0025.md`, `.proofline/criteria/ac-0036.md`

## Trigger and Participants

- Trigger: 사용자가 confirmed Discovery 범위의 REQ/AC 또는 approved baseline 변경을 위해 design docs 작성·갱신을 요청한다.
- 사용자: scope·policy·compatibility 결정과 Specification approval authority
- Maintenance agent: evidence 조사, 영향 분류와 bounded document write
- Approval agent: exact REQ·AC·Normative Design Documents 제시와 승인 후 status 전환
- Implementation agent: approved baseline 소비자

## Normal Flow

1. 실제 project root, Git 상태, confirmed Discovery, draft REQ, `create`·`update`의 draft AC와 `retire`·`satisfy`의 active AC를 확인한다.
2. REQ/AC, existing docs, source, tests와 config를 직접 조사한다.
3. 변경을 `interface`, `data-model`, `runtime-flow` 또는 `no-impact`로 분류한다.
4. `no-impact`이면 근거와 분류를 보고하고 planned/write set을 비우며 existing docs와 `Normative Design Documents` 목록을 변경하지 않은 채 design-document maintenance를 종료한다. 목록이 없는 REQ도 approval 대상이 될 수 있다.
5. 영향이 있으면 각 기술 사실의 existing document owner를 찾고 update를 우선하며 독립 owner가 필요할 때만 새 path를 계획한다.
6. Target path type, repository boundary, collision과 user-owned content를 확인한다.
7. Write 전에 project root, exact path, `create`/`update`, existing owner, 변경할 section과 의도된 의미를 planned update로 사용자에게 제시한다. 그 뒤 제시한 explicit 범위에만 write한다.
8. Explicit 요청 범위의 design docs를 작성하고 REQ `Normative Design Documents`에 exact path를 열거한다.
9. Structure, related-specification reference, duplicate ownership과 current source evidence를 검토한다.
10. Approval agent가 exact REQ, 전체 `create`·`update`·`retire`·`satisfy` criteria AC와 referenced design-document contents를 함께 사용자에게 제시한다.
11. 사용자 approval 뒤에만 `create`·`update` AC는 `draft → active`, `retire` AC는 `active → retired`로 전환하며 `satisfy` AC는 `active` status와 exact bytes를 유지한다. REQ는 `draft → approved`로 전환한 뒤 `proofline validate`를 실행하고 design docs에는 status transition을 기록하지 않는다.
12. Approved specification과 design documents를 implementation baseline으로 handoff한다.

## State Transitions

```text
Discovery confirmed
→ REQ/AC draft + design-doc candidate
→ user reviews exact combined baseline
→ REQ draft → approved
  + create/update AC draft → active
  + retire AC active → retired
  + satisfy AC active/bytes unchanged
  + design docs normative
```

Design documents 자체는 lifecycle status를 갖지 않는다. Approval 의미는 이를 exact path로 참조하는 approved REQ와 사용자 결정이 소유한다.

## Failure Flow

- Evidence로 해소할 수 없는 product·policy·compatibility·scope 결정은 사용자 decision으로 반환하고 approval을 진행하지 않는다.
- Evidence 조사 command가 timeout이면 실패로 전달하고 자동 retry하지 않는다. 사용자가 현재 상태를 확인한 뒤 재실행을 요청할 수 있다.
- Write 시작 전 cancellation은 zero project-document mutation이어야 한다. Write 시작 뒤 failure·timeout·cancellation은 다음 target 전에 중단하고 changed·unchanged·unattempted exact path와 변경 전 관찰한 bytes/digest를 보고하며 자동 retry·rollback하지 않는다. Partial candidate에서는 Specification approval을 진행하지 않고, drift를 재확인한 복구나 rollback은 별도 explicit 사용자 요청으로만 수행한다.
- Existing symlink, unexpected type, repository escape, unrelated user document 또는 owner ambiguity가 있으면 대상 write를 수행하지 않는다.
- Source가 approved REQ/AC와 충돌하면 existing source를 design truth로 채택하지 않고 specification correction 여부를 결정받는다.
- Required design-document path가 없거나 exact 내용을 제시할 수 없으면 Specification approval을 진행하지 않는다.
- `proofline validate`가 실패하면 REQ/AC status 전환 결과를 성공으로 보고하지 않는다.

## Side Effects and Recovery

- Initial workflow의 side effect는 explicit project design-document write와 canonical draft authoring으로 제한한다.
- Project initializer, live agent profile, Git commit/ref, remote repository와 release/deployment target을 변경하지 않는다.
- 승인된 design 의미를 바꿔야 하면 관련 REQ와 영향받는 AC를 draft로 되돌리고 design docs와 함께 재검토·재승인한다.
- Existing document conflict를 자동 overwrite·rename·archive하여 복구하지 않으며 사용자 결정 전 current bytes를 보존한다.
