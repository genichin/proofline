# ProofLine 산출물 디렉터리 구조

> 상태: 초안

이 문서는 ProofLine을 적용한 프로젝트에서 ProofLine이 생성하고 관리하는 영속 산출물의 저장 위치, identity, source of truth 및 canonical directory 구조를 정의한다.

## 1. 기본 원칙

- 모든 경로는 적용 대상 프로젝트의 저장소 root를 기준으로 하는 상대 경로이다.
- 프로젝트별 ProofLine 설정은 프로젝트 root의 `proofline.yaml`에 기록한다.
- ProofLine의 영속 산출물 root는 `.proofline/`이다.
- canonical 산출물은 Git으로 추적하여 현재 내용과 변경 이력을 함께 관리한다.
- 고정 파일명의 과거 내용은 별도 revision 파일이 아니라 Git history로 보존한다.
- 임시 파일, 실행 log, 다운로드 cache 등 재생성 가능한 실행 부산물은 canonical 산출물과 섞지 않는다.
- symbolic link, 절대 경로 및 `..`을 이용한 저장소 상위 directory 접근은 허용하지 않는다.
- 하나의 사실을 여러 파일에 중복해서 canonical하게 기록하지 않는다.

## 2. Artifact 역할과 source of truth

ProofLine artifact의 역할은 다음과 같이 구분한다.

| Artifact | 역할 | 소유하는 canonical 사실 |
| --- | --- | --- |
| Line | 독립적인 변경 및 delivery 단위 | Discovery, REQ 및 Micro-SPEC의 공통 identity와 Line execution 상태 |
| Discovery | 변경이 필요한 이유와 범위 탐색 | 문제, 배경, 위험, 변경 의도 |
| REQ | 해당 Line에서 승인한 전체 변경 계약 | 생성·수정·폐기할 AC 집합과 Line-level scope |
| AC | 독립적으로 검증 가능한 최소 사양 | 시스템이 현재 만족해야 하는 하나의 criterion |
| Micro-SPEC | REQ를 구현 가능한 단위로 분해 | 담당 AC 부분집합, 구현 범위와 검증 방법 |
| IQC | Micro-SPEC 구현의 검증 결과 | exact Micro-SPEC과 implementation commit에 대한 검사, AC별 결과 및 전체 판정 |
| DQC | Line 통합 후보의 전체 검증 결과 | 모든 IQC를 종합한 exact candidate commit의 전체 판정 |

핵심 source-of-truth 규칙은 다음과 같다.

```text
Discovery  = 왜 변경하는가
REQ        = 이번 Line에서 무엇을 변경하고 전달하는가
AC         = 시스템이 만족해야 하는 최소 canonical 사양
Micro-SPEC = 승인된 REQ의 일부를 어떻게 구현하고 검증하는가
IQC        = exact Micro-SPEC과 구현 commit의 검증 결과는 무엇인가
DQC        = exact Line integration candidate의 전체 검증 결과는 무엇인가
Git        = 고정 파일명의 개정 이력
```

REQ는 AC의 상세 문장을 복제하지 않는다. AC 내용의 source of truth는 항상 `.proofline/criteria/ac-<NNNN>.md`이며, REQ는 해당 Line에서 생성·수정·폐기할 AC ID 집합만 소유한다.

REQ가 승인되면 해당 승인 Git revision의 REQ와 그 REQ가 참조하는 AC 집합이 그 Line의 canonical specification baseline이 된다. 이 baseline은 구현해야 할 사양을 확정하지만, 구현·검증 또는 delivery 완료를 의미하지 않는다.

## 3. Line identity와 cardinality

하나의 Line은 하나의 4자리 숫자 identity `NNNN`을 사용하며 다음 identity는 같은 숫자를 공유해야 한다.

```text
Line directory  line-NNNN
Line artifact   line-NNNN.md
Discovery       dcy-NNNN
REQ             req-NNNN
Micro-SPEC      ms-NNNN-SSS
IQC             iqc-NNNN-SSS
DQC             dqc-NNNN
```

- `NNNN`은 `0001`부터 시작하는 4자리 decimal Line ID이다.
- `SSS`는 같은 Line 안에서 `001`부터 시작하는 3자리 Micro-SPEC ID이다.
- 하나의 Line에는 정확히 하나의 Line artifact, 하나의 Discovery identity, 하나의 REQ identity와 하나의 DQC identity만 존재한다.
- Line directory, Line artifact, Discovery, REQ, Micro-SPEC, IQC 및 DQC의 `NNNN`은 반드시 일치해야 한다.
- IQC의 `NNNN-SSS`는 검증 대상 Micro-SPEC의 `NNNN-SSS`와 일치해야 한다.
- 하나의 REQ는 여러 Micro-SPEC으로 분해할 수 있다.
- 독립적인 승인 또는 delivery 경계를 갖는 새 요구사항은 새 Line과 새 Discovery/REQ를 만든다.
- AC identity는 Line identity와 독립적이며 프로젝트 전체에서 안정적으로 유지한다.

첫 번째 Line의 관계는 다음과 같다.

```text
line-0001/
├── line-0001.md
├── dcy-0001.md
├── req-0001.md
├── dqc-0001.md
└── micro-specs/
    ├── ms-0001-001.md
    ├── iqc-0001-001.md
    ├── ms-0001-002.md
    └── iqc-0001-002.md
```

## 4. Canonical directory 구조

```text
<project-root>/
├── AGENTS.md
├── proofline.yaml
├── .proofline/
│   ├── lines/
│   │   ├── line-0001/
│   │   │   ├── line-0001.md
│   │   │   ├── dcy-0001.md
│   │   │   ├── req-0001.md
│   │   │   ├── dqc-0001.md
│   │   │   └── micro-specs/
│   │   │       ├── ms-0001-001.md
│   │   │       ├── iqc-0001-001.md
│   │   │       ├── ms-0001-002.md
│   │   │       └── iqc-0001-002.md
│   │   ├── line-0002/
│   │   │   └── ...
│   │   └── ...
│   └── criteria/
│       ├── ac-0001.md
│       ├── ac-0002.md
│       └── ac-0003.md
└── ...
```

일반 path 문법은 다음과 같다.

```text
Line directory  .proofline/lines/line-<NNNN>/
Line artifact   .proofline/lines/line-<NNNN>/line-<NNNN>.md
Discovery       .proofline/lines/line-<NNNN>/dcy-<NNNN>.md
REQ             .proofline/lines/line-<NNNN>/req-<NNNN>.md
Micro-SPEC      .proofline/lines/line-<NNNN>/micro-specs/ms-<NNNN>-<SSS>.md
IQC             .proofline/lines/line-<NNNN>/micro-specs/iqc-<NNNN>-<SSS>.md
DQC             .proofline/lines/line-<NNNN>/dqc-<NNNN>.md
AC              .proofline/criteria/ac-<NNNN>.md
```

다음 path는 canonical하지 않다.

```text
.proofline/lines/0001/
.proofline/line-0001/
.proofline/lineages/lineage-0001/
.proofline/lines/line-0001/criteria/ac-0001.md
```

AC는 특정 Line에 복사하거나 소유시키지 않고 프로젝트 전역의 `.proofline/criteria/`에 한 번만 저장한다.

현재 canonical topology에는 프로젝트 전역 policy directory를 두지 않는다. `.proofline/policies/`는 canonical path가 아니며 ProofLine writer와 validator는 이를 생성하거나 policy source로 해석하지 않는다.

프로젝트 공통 정보의 source of truth는 역할에 따라 다음과 같이 유지한다.

- 기계 판독 가능한 프로젝트별 ProofLine 설정은 `proofline.yaml`이 소유한다.
- AI agent의 작업 지침은 `AGENTS.md`가 소유한다.
- 제품 정책은 적용 대상 프로젝트의 기존 문서가 소유한다.
- 특정 Line의 요구사항과 검증 계약은 REQ, AC, Micro-SPEC, IQC 및 DQC가 소유한다.

향후 여러 Line에 공통 적용되는 ProofLine 전용 정책을 validator가 기계적으로 해석해야 하는 구체적인 필요가 생기면 schema, precedence 및 lifecycle을 먼저 정의한 뒤 별도 문서 개정으로 policy directory 도입 여부를 다시 결정한다.

## 5. Contract 문서 탐색

이 문서는 ProofLine contract의 진입점이며 canonical topology, path와 identity를 소유한다. 나머지 계약은 작업별 문서가 소유한다.

| 작업 | 반드시 읽을 문서 | 소유하는 계약 |
| --- | --- | --- |
| Discovery 작성·확인 | [Discovery 계약](contracts/discovery.md), [문서 형식과 완결성](contracts/document-format.md) | Discovery 상태, transition, 확인 조건과 문서 schema |
| REQ·AC 작성·승인 | [REQ와 AC 계약](contracts/requirements-and-criteria.md), [문서 형식과 완결성](contracts/document-format.md) | AC 변경 집합, binding, REQ·AC lifecycle과 문서 schema |
| Micro-SPEC 작성·구현·IQC | [Micro-SPEC과 IQC 계약](contracts/micro-spec-and-iqc.md), [문서 형식과 완결성](contracts/document-format.md) | Micro-SPEC·IQC 상태와 문서 schema |
| DQC·main 통합·delivery | [Line 검증·통합·Delivery 계약](contracts/line-delivery.md), [문서 형식과 완결성](contracts/document-format.md) | branch lifecycle, Line·DQC 상태, 통합·delivery gate와 문서 schema |
| 파생 문서·보존·프로젝트 설정 | [저장·파생 문서·보존 계약](contracts/storage-and-retention.md) | `docs/requirements.md`, 보존·폐기, `proofline.yaml` |
| 공통 형식·placeholder·validator | [문서 형식과 완결성](contracts/document-format.md) | 공통 frontmatter·Markdown 규칙, metadata policy, placeholder와 완결성 gate |

## 6. 규칙 소유권과 참조 원칙

- 하나의 canonical 규칙은 위 표에 지정한 한 문서만 소유한다.
- 다른 contract 문서는 같은 규칙을 다시 정의하지 않고 소유 문서로 연결한다.
- 경로, ID 및 cardinality가 다른 문서의 예시와 충돌하면 이 문서가 우선한다.
- Artifact별 최소 필수 field와 본문 schema는 각 작업별 소유 문서가 우선한다.
- 공통 frontmatter·Markdown 형식, metadata policy 및 placeholder 규칙은 `contracts/document-format.md`가 우선한다.
- Lifecycle과 gate는 각 작업별 소유 문서가 우선한다.
- Contract 문서의 분리는 artifact의 canonical status나 source of truth를 변경하지 않는다.

## 7. AI agent 읽기 규칙

AI agent는 모든 contract 문서를 항상 한꺼번에 읽지 않는다.

1. 먼저 이 문서에서 대상 artifact의 path와 identity를 확인한다.
2. 위 탐색표에서 현재 작업을 소유하는 contract 문서를 읽는다.
3. Canonical artifact를 작성하거나 검증할 때는 `contracts/document-format.md`를 함께 읽는다.
4. Main 통합이나 delivery를 판정할 때는 `contracts/line-delivery.md`를 반드시 읽는다.
5. 파생 문서 생성, cleanup 또는 evidence 보존을 수행할 때는 `contracts/storage-and-retention.md`를 읽는다.
