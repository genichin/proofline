# Project Design Document Maintenance Interface

## Purpose

`proofline-maintain-design-docs` agent skill이 approved product specification과 project-owned technical design 사이의 경계를 유지하면서 Interface Contract, Data Model과 Runtime Flow 문서를 명시적으로 생성·갱신하는 interface를 정의한다.

## Related Specification

- Discovery: `.proofline/lines/line-0034/dcy-0034.md`
- Requirement: `.proofline/lines/line-0034/req-0034.md`
- Criteria: `.proofline/criteria/ac-0025.md`, `.proofline/criteria/ac-0036.md`

## Boundary and Participants

- 호출자: Design-document 작성 또는 갱신을 명시적으로 요청하는 사용자
- 작성자: Repository-owned `proofline-maintain-design-docs` skill을 수행하는 agent
- 승인 authority: REQ·AC와 referenced design documents의 exact 내용을 검토하는 사용자
- 대상: 현재 Git repository root의 project-owned `docs/interfaces/`, `docs/data-model/`, `docs/runtime-flows/`
- 비대상: `.proofline/` canonical writer, `proofline project init`, live agent profile과 remote repository

## Inputs and Outputs

### Inputs

- 실제 project root
- Confirmed Discovery, draft REQ, `create`·`update`의 draft AC와 `retire`·`satisfy`의 active AC
- REQ의 `Normative Design Documents` exact path 목록
- Existing design documents
- 관련 source, test와 config evidence
- 사용자가 명시한 create/update 범위와 결정

### Outputs

- 영향 분류: `interface`, `data-model`, `runtime-flow`, `no-impact`
- Write 전에 제시하는 planned update: project root, exact path, `create` 또는 `update`, existing owner, 변경할 section과 의도된 의미
- Explicit scope 안에서 작성된 project design-document bytes
- 확인한 evidence, 남은 decision과 Specification approval handoff 요약
- `no-impact`이면 근거와 분류, empty planned/write set. Existing document와 REQ의 `Normative Design Documents` 목록은 변경하지 않는다.

## Preconditions and Postconditions

### Preconditions

- Project root와 target path는 repository-relative regular path여야 한다.
- Discovery는 `confirmed`, REQ와 `create`·`update` AC는 approval 전 `draft`여야 하며 `retire`·`satisfy` AC는 `active`와 current exact bytes를 유지해야 한다.
- Existing target의 file type와 owner가 명확해야 한다.
- Product·policy·compatibility·scope 결정은 evidence 또는 사용자 결정으로 해소되어야 한다.
- Planned update를 write 전에 사용자에게 제시해야 하며, 제시한 범위가 explicit 요청을 초과하면 write하지 않는다.

### Postconditions

- 같은 기술 사실은 한 design document owner가 소유한다.
- AC의 observable behavior를 design document에 source of truth로 복제하지 않고 related specification으로 참조한다.
- 실제 normative design document가 있는 경우에만 REQ가 baseline에 포함할 exact project-relative paths를 열거한다. `no-impact` 또는 design-document 비참조 REQ에서는 목록 부재가 실패가 아니다.
- Agent는 document 작성만으로 Discovery, REQ 또는 AC status를 변경하지 않는다.

## Errors and Compatibility

- Existing symlink, directory, 예상 밖 file type, unrelated user document 또는 불명확한 owner는 no-write blocker다.
- REQ/AC와 source가 충돌하면 source를 authoritative하게 채택하지 않고 governance decision으로 반환한다.
- 기존 project가 다른 design-doc topology를 사용하면 자동 migration하지 않는다.
- 첫 version은 agent skill contract이며 새 CLI interface, embedded LLM runtime 또는 semantic source-to-doc validator를 제공하지 않는다.
