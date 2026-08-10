# Project Design Document Structure

## Purpose

ProofLine이 제공하는 세 project design-document template의 path, section과 ownership model을 정의한다. 이 문서는 template source와 적용 프로젝트 문서가 표현해야 할 공통 기술 계약이며 `.proofline/` artifact schema가 아니다.

## Related Specification

- Discovery: `.proofline/lines/line-0034/dcy-0034.md`
- Requirement: `.proofline/lines/line-0034/req-0034.md`
- Criteria: `.proofline/criteria/ac-0025.md`, `.proofline/criteria/ac-0036.md`

## Document Families

### Interface Contract

- Path: `docs/interfaces/<name>.md`
- Required sections: `Purpose`, `Related Specification`, `Boundary and Participants`, `Inputs and Outputs`, `Preconditions and Postconditions`, `Errors and Compatibility`
- Owner: component 사이의 callable 또는 message boundary

### Data Model

- Path: `docs/data-model/<name>.md`
- Required sections: `Purpose`, `Related Specification`, `Structures`, `Invariants`, `Ownership and Lifetime`, `Serialization and Compatibility`, `Producers and Consumers`
- Owner: structure, field/type와 data invariant

### Runtime Flow

- Path: `docs/runtime-flows/<name>.md`
- Required sections: `Purpose`, `Related Specification`, `Trigger and Participants`, `Normal Flow`, `State Transitions`, `Failure Flow`, `Side Effects and Recovery`
- Owner: component interaction order와 runtime failure boundary. `Failure Flow`는 timeout, retry, cancellation과 failure propagation을, `Side Effects and Recovery`는 partial write를 포함한 side effect와 recovery를 소유한다.

## Structures

- Document identity는 repository-relative path와 H1 title로 표현하며 별도 ProofLine ID를 할당하지 않는다.
- `Related Specification`은 관련 Discovery, REQ와 AC의 canonical project-relative path를 기록하되 해당 artifact body를 복제하지 않는다.
- REQ의 `Constraints` 아래 `Normative Design Documents` 목록은 승인 대상 design-document path 집합을 소유한다.
- Git은 document revision history를 보존하지만 사용자 approval authority를 대체하지 않는다.

## Invariants

- 같은 interface, data structure 또는 runtime flow의 current normative meaning은 하나의 project document owner만 가진다.
- Design document는 implementation-facing detail을 소유하고 AC는 version-independent observable behavior를 소유한다.
- Design-document content에는 `draft`, `approved` 같은 별도 lifecycle status나 approval record를 두지 않는다.
- REQ가 열거한 path는 repository 안의 regular Markdown file이어야 하며 symlink나 repository 밖 path를 허용하지 않는다.

## Ownership and Lifetime

- Template bytes는 ProofLine package가 소유한다.
- 적용 프로젝트에 작성된 design-document bytes와 naming은 해당 project가 소유한다.
- Skill은 explicit user request의 작성자이며 product·policy·scope approval owner가 아니다.
- Design document는 관련 기술 계약이 존재하는 동안 current path에서 유지하며 자동 archive, rename 또는 migration하지 않는다.

## Serialization and Compatibility

- UTF-8 Markdown을 사용하고 exactly one H1과 family별 required H2를 필수 구조로 유지한다.
- Framework-specific schema, code block, table와 H3 이하는 owning H2 안에 추가할 수 있다.
- Existing project의 다른 문서 체계는 자동 변환하지 않으며 first delivery에서 validator hard gate로 만들지 않는다.

## Producers and Consumers

- Producer: `proofline-maintain-design-docs` skill과 명시적인 사용자 편집
- Review consumer: `proofline-approve-specification` skill과 사용자 approval authority
- Implementation consumer: 구현 agent와 reviewer
- Non-consumer: `proofline validate`, identity allocator와 project initializer
