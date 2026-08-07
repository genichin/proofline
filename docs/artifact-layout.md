# ProofLine 산출물 디렉터리 구조

이 문서는 ProofLine을 적용한 프로젝트의 canonical artifact 위치, identity와 source of truth를 정의하는 contract 진입점이다.

## 기본 원칙

- 모든 경로는 적용 대상 Git 저장소 root를 기준으로 한다.
- 프로젝트 설정은 `proofline.yaml`, 영속 project artifact는 `.proofline/`에 둔다.
- 현재 canonical artifact 범위는 Line, Discovery, REQ와 AC뿐이다.
- Canonical artifact는 Git으로 추적하되 ProofLine validator의 보증 범위는 검증 대상 tree의 구조와 내용이다.
- User-level `~/.proofline/`은 manifest, contracts, operations, templates와 skills를 보관하며 project artifact topology에 포함되지 않는다.
- 임시 파일, 실행 log와 cache를 canonical tree에 저장하지 않는다.
- Symbolic link, 절대 경로와 `..`을 이용한 저장소 경계 이탈은 허용하지 않는다.

## Artifact 역할

| Artifact | 역할 | 소유하는 canonical 사실 |
| --- | --- | --- |
| Line identity ledger | 프로젝트 전역 Line allocation 기록 | canonical main에 예약된 `line-NNNN` 집합 |
| Line | 독립적인 변경 단위 | Discovery와 REQ의 공통 stable identity |
| Discovery | 변경이 필요한 이유와 범위 탐색 | 문제, 근거, 범위와 위험 |
| REQ | 해당 Line에서 승인한 변경 계약 | 생성·수정·폐기·충족할 AC 집합과 Line-level scope |
| AC | 독립적으로 검증 가능한 최소 사양 | 시스템이 만족해야 하는 하나의 criterion |

```text
Discovery = 왜 변경하는가
REQ       = 이번 Line에서 무엇을 변경하는가
AC        = 시스템이 만족해야 하는 최소 canonical 사양
Line      = 위 artifact를 묶는 identity
```

REQ는 AC 상세 문장을 복제하지 않는다. AC 내용의 source of truth는 `.proofline/criteria/ac-<NNNN>.md`이며 REQ는 해당 Line의 AC 변경 집합만 소유한다.

## Identity와 경로

하나의 Line은 `0001`부터 시작하는 4자리 decimal identity `NNNN`을 사용한다.

```text
Line directory  .proofline/lines/line-<NNNN>/
Line artifact   .proofline/lines/line-<NNNN>/line-<NNNN>.md
Discovery       .proofline/lines/line-<NNNN>/dcy-<NNNN>.md
REQ             .proofline/lines/line-<NNNN>/req-<NNNN>.md
AC              .proofline/criteria/ac-<NNNN>.md
```

- 하나의 Line directory에는 Line, Discovery와 REQ identity가 각각 하나만 존재한다.
- Line directory, Line artifact, Discovery와 REQ의 `NNNN`은 일치해야 한다.
- AC identity는 Line identity와 독립적이며 프로젝트 전체에서 안정적으로 유지한다.
- 독립적인 승인 경계를 갖는 새 요구사항은 새 Line과 새 Discovery/REQ를 만든다.

## Canonical directory

```text
<project-root>/
├── AGENTS.md
├── proofline.yaml
└── .proofline/
    ├── line-identities.json
    ├── lines/
    │   ├── .gitkeep
    │   └── line-0001/
    │       ├── line-0001.md
    │       ├── dcy-0001.md
    │       └── req-0001.md
    └── criteria/
        ├── .gitkeep
        ├── ac-0001.md
        └── ac-0002.md
```

AC는 특정 Line directory에 복사하거나 소유시키지 않는다. `.proofline/lines/.gitkeep`과 `.proofline/criteria/.gitkeep`은 empty directory 보존용 zero-byte support marker이며 lifecycle artifact가 아니다.

## Opaque retained data compatibility

이전 ProofLine version이 `.proofline/`에 기록한 현재 범위 밖 artifact와 directory는 **opaque retained data**이다.

- 기존 bytes와 Git history를 보존할 수 있다.
- 현재 contract, writer와 skill은 이를 canonical artifact로 해석하거나 새로 만들지 않는다.
- 현재 lifecycle과 validation의 입력, gate 또는 source of truth로 사용하지 않는다.
- 호환성을 위해 소급 변환, 이동, 이름 변경 또는 삭제를 요구하지 않는다.
- 해당 데이터가 있다는 이유만으로 현재 canonical artifact 의미를 확장하지 않는다.

이는 migration 절차가 없는 compatibility 경계다. 보안·법적 삭제가 필요한 경우에만 프로젝트의 incident 절차를 우선한다.

## Contract 탐색

| 작업 | 반드시 읽을 문서 |
| --- | --- |
| Discovery 작성·확인 | [Discovery 계약](contracts/discovery.md), [문서 형식과 완결성](contracts/document-format.md) |
| REQ·AC 작성·승인 | [REQ와 AC 계약](contracts/requirements-and-criteria.md), [문서 형식과 완결성](contracts/document-format.md) |
| Line identity | [Line identity 계약](contracts/line-delivery.md) |
| 파생 문서·보존·프로젝트 설정 | [저장·파생 문서·보존 계약](contracts/storage-and-retention.md) |

경로, ID와 cardinality는 이 문서가 소유한다. Artifact별 schema와 status-bearing artifact의 lifecycle은 위 표의 소유 문서가, 공통 frontmatter·Markdown과 placeholder 규칙은 `contracts/document-format.md`가 소유한다.
