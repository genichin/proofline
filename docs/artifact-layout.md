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

## 5. REQ와 AC 변경 집합

REQ는 해당 Line의 전체 변경 범위를 AC ID로 선언한다.

```yaml
---
id: req-0001
status: draft
discovery: dcy-0001
criteria:
  create:
    - ac-0001
  update:
    - ac-0003
  retire: []
---
```

규칙은 다음과 같다.

- `create`는 이 Line에서 새로 도입하는 AC이다.
- `update`는 같은 stable AC 파일의 현재 내용을 변경하는 AC이다.
- `retire`는 더 이상 현재 사양으로 적용하지 않을 AC이다.
- 같은 AC를 하나의 REQ 안에서 둘 이상의 변경 종류에 동시에 기록하지 않는다.
- REQ에는 AC 본문을 복제하지 않는다.
- REQ의 AC 변경 집합은 해당 Line의 모든 Micro-SPEC이 따라야 하는 승인 범위의 source of truth이다.
- AC 상세 내용은 각 `.proofline/criteria/ac-<NNNN>.md` 파일이 소유한다.

## 6. AC 파일

AC는 프로젝트 전체에서 안정적인 ID와 고정 경로를 갖는 최소 canonical 사양이다.

```yaml
---
id: ac-0003
status: draft
---
```

```markdown
# 로그인 세션 유지 시간

## Criterion

로그인 세션은 마지막 사용자 활동 후 15분 동안 유지되어야 한다.

## Verification

- 15분 이전에는 세션이 유효해야 한다.
- 15분 이후에는 인증이 거부되어야 한다.
```

- AC 하나는 독립적으로 변경하고 검증할 수 있는 하나의 criterion을 표현한다.
- 새로운 독립 조건은 기존 AC에 누적하지 않고 새 AC identity로 분리한다.
- 같은 사양 축의 값이나 조건을 변경하는 경우 stable AC 파일을 같은 경로에서 수정한다.
- 승인 전의 AC 내용은 candidate 사양이며 구현 기준인 canonical specification baseline이 아니다.
- REQ 승인 Git revision의 AC 내용이 해당 Line의 canonical specification baseline이 된다.
- 승인된 AC는 구현 완료 여부와 관계없이 구현이 따라야 할 사양으로 효력을 갖는다.
- 과거 AC 내용과 이전 승인 baseline은 Git history에서 확인한다.

## 7. Micro-SPEC과 REQ binding

Micro-SPEC의 직접적인 source of truth는 같은 Line의 REQ이다.

```yaml
---
id: ms-0001-001
spec_status: draft
implementation_status: not_started
parent_req: req-0001
criteria:
  - ac-0001
  - ac-0002
---
```

AC는 사양의 atomic unit이고 Micro-SPEC은 기술적 구현 unit이므로 두 분해 경계는 반드시 일치하지 않는다. AC와 Micro-SPEC은 N:M 관계를 사용한다.

```text
하나의 AC         → 하나 이상의 Micro-SPEC이 공동으로 구현 가능
하나의 Micro-SPEC → 하나 이상의 AC를 함께 구현 가능
```

규칙은 다음과 같다.

- `parent_req`는 같은 Line의 유일한 REQ여야 한다.
- Micro-SPEC은 parent REQ가 선언한 AC 중 자신이 담당하는 하나 이상의 AC를 `criteria`에 명시해야 한다.
- Micro-SPEC의 모든 AC는 parent REQ의 `create`, `update`, `retire` 합집합에 포함되어야 한다.
- Micro-SPEC은 REQ에 없는 AC나 제품 동작을 임의로 추가할 수 없다.
- 하나의 AC를 여러 Micro-SPEC이 공동으로 담당할 수 있다.
- 하나의 Micro-SPEC이 서로 관련된 여러 AC를 함께 담당할 수 있다.
- parent REQ가 선언한 모든 대상 AC는 최소 하나의 Micro-SPEC에 배정되어야 한다.
- 같은 AC를 여러 Micro-SPEC이 참조하더라도 AC 내용의 source of truth는 `.proofline/criteria/`의 단일 AC 파일이다.
- 독립적인 구현·검증 경계를 가진 AC를 하나의 Micro-SPEC에 불필요하게 묶지 않는다.
- 새로운 사양이 구현 중 발견되면 Micro-SPEC에 바로 추가하지 않고 AC와 REQ 범위를 먼저 갱신한다.

집합 불변식은 다음과 같다.

```text
각 Micro-SPEC의 criteria
⊆ parent REQ의 (create ∪ update ∪ retire)

해당 REQ에 속한 모든 Micro-SPEC criteria의 합집합
= parent REQ의 (create ∪ update ∪ retire)
```

관계 예시는 다음과 같다.

```text
req-0001
├── ac-0001 ──→ ms-0001-001
├── ac-0002 ──→ ms-0001-001
└── ac-0003 ──→ ms-0001-001
             └→ ms-0001-002
```

## 8. Specification baseline과 branch lifecycle

ProofLine은 specification governance와 implementation을 branch 경계로 분리한다.

```text
main governance
→ Line 생성
→ Discovery 작성·확인
→ REQ와 대상 AC 작성·검토
→ REQ 승인
→ specification baseline commit 확정

implementation branch
→ 승인 commit에서 branch 생성
→ Micro-SPEC 작성·검토
→ 구현
→ 검증

main integration
→ 검증된 구현 통합
→ Line delivery 판정
```

규칙은 다음과 같다.

- Line 생성부터 Discovery confirm과 REQ approve까지는 main의 직렬화된 governance 흐름에서 수행한다.
- REQ approval commit은 해당 Line의 REQ와 대상 AC exact bytes를 고정하는 canonical specification baseline이다.
- implementation branch는 REQ approval commit에서 생성해야 한다.
- Micro-SPEC, 구현 및 구현 검증은 implementation branch에서 수행한다.
- Micro-SPEC과 구현은 branch base에 고정된 승인 REQ 및 AC baseline을 따라야 한다.
- 검증을 통과한 implementation 결과만 main 통합 대상으로 삼는다.
- REQ approval, 그에 따른 AC lifecycle transition 및 implementation branch 생성은 구현·검증 또는 delivery 완료를 의미하지 않는다.

구현 중 승인된 AC의 의미를 변경할 필요가 발견되면 implementation branch에서 그 변경을 승인하거나 구현과 함께 main에 통합하지 않는다. 다음 순서를 따른다.

```text
implementation branch에서 변경 필요 발견
→ 영향받는 구현 중단
→ main governance로 반환
→ REQ와 AC 변경
→ 변경 영향 검토
→ REQ 재승인과 대상 AC lifecycle 확정
→ 새로운 specification baseline commit 확정
→ implementation branch에 새 baseline 반영
→ 영향받는 Micro-SPEC 갱신
→ 구현 재개
```

- 구현자나 Micro-SPEC은 승인된 AC를 임의로 확대, 축소 또는 변경할 수 없다.
- 같은 구현의 PASS/FAIL 결과를 바꿀 수 있는 AC 수정은 specification 변경이다.
- specification 변경이 발생하면 기존 승인 baseline에 대한 영향받는 구현·검증 완료 주장을 중단한다.
- 변경된 REQ가 main governance에서 재승인되고 대상 AC lifecycle이 확정되기 전에는 영향받는 구현을 재개하지 않는다.
- 독립적인 새 승인 또는 delivery 경계가 필요한 변경은 기존 Line을 확대하지 않고 새 Line으로 분리한다.

사양 상태와 실행 상태는 서로 다른 사실이므로 별도로 관리한다.

```text
Specification state
→ REQ와 AC가 구현 기준으로 승인됐는가

Execution state
→ Micro-SPEC 작성, 구현, 검증 및 delivery가 어디까지 진행됐는가
```

따라서 다음 상태는 유효하다.

```text
REQ specification: approved
Target AC lifecycle: active
Implementation: not started 또는 in progress
Verification: not completed
Delivery: not completed
```

REQ 승인과 그에 따른 AC lifecycle 확정은 구현·검증·delivery 완료를 의미하지 않으며, 구현·검증 결과가 존재한다는 사실도 승인되지 않은 사양을 canonical specification baseline으로 만들지 않는다. Main 통합 gate와 Line delivery 판정은 13절의 execution contract를 따르며 specification state와 execution state를 하나의 status 의미로 합치지 않는다.

## 9. Discovery specification status

Discovery의 `status`는 문제와 변경 필요성에 대한 governance 상태만 표현하며 다음 세 값만 허용한다.

```text
draft
confirmed
withdrawn
```

각 상태의 의미는 다음과 같다.

| Status | 의미 | REQ 승인 |
| --- | --- | --- |
| `draft` | 문제, 배경, 범위 및 위험을 탐색·검토·수정하는 중 | 금지 |
| `confirmed` | 문제와 변경 필요성이 확인되어 REQ의 근거로 사용할 수 있음 | 허용 |
| `withdrawn` | 해당 Discovery를 더 이상 진행하지 않음 | 금지 또는 중단 |

허용 transition은 다음과 같다.

```text
draft ───────→ confirmed
  │                 │
  └──→ withdrawn    ├──→ withdrawn
                    │
                    └──→ draft ──→ confirmed
                         의미 변경    재확인
```

규칙은 다음과 같다.

- `confirmed`된 Discovery만 같은 Line의 REQ를 `approved`로 전환할 수 있는 근거가 된다.
- Discovery를 `confirmed`로 전환하려면 Open Question의 `Status`를 포함하여 문서 전체에 governance placeholder가 없어야 한다. 남아 있는 `deferred` Open Question은 명시적인 `Owner`와 `Exit Condition`을 가져야 한다.
- Discovery가 `draft`이면 같은 Line의 REQ는 작성할 수 있지만 `approved`로 전환할 수 없다.
- `confirmed`된 Discovery의 문제, 범위 또는 변경 의도를 의미 있게 변경하려면 Discovery를 먼저 `draft`로 전환하고 다시 검토·확인해야 한다.
- Discovery의 의미 변경이 이미 승인된 REQ의 근거에 영향을 주면 영향받는 구현을 중단하고 REQ도 `draft`로 전환하여 대상 AC와 함께 재검토한 뒤 REQ를 재승인하고 AC lifecycle을 다시 확정해야 한다.
- `draft` 또는 `confirmed` Discovery는 `withdrawn`으로 전환할 수 있다.
- Discovery가 `withdrawn`이면 같은 Line의 REQ를 새로 승인할 수 없으며, 이미 승인된 REQ와 진행 중인 구현은 중단하고 REQ를 `withdrawn`으로 전환해야 한다.
- `withdrawn`은 terminal status이다. 같은 변경을 다시 진행하려면 독립적인 새 Line과 새 Discovery/REQ를 만든다.
- Discovery의 `confirmed`는 REQ 승인, 구현 완료, 검증 또는 delivery 완료를 의미하지 않는다.

## 10. REQ specification status

REQ의 `status`는 specification governance만 표현하며 다음 세 값만 허용한다.

```text
draft
approved
withdrawn
```

각 상태의 의미는 다음과 같다.

| Status | 의미 | 구현 시작 |
| --- | --- | --- |
| `draft` | REQ와 대상 AC를 작성·검토·수정하는 중이며 승인 baseline이 아님 | 금지 |
| `approved` | REQ와 대상 AC exact bytes가 승인되어 specification baseline을 형성함 | approval commit에서 허용 |
| `withdrawn` | 해당 REQ 변경 계약을 더 이상 진행하지 않음 | 금지 또는 중단 |

허용 transition은 다음과 같다.

```text
draft ───────→ approved
  │                │
  └──→ withdrawn   ├──→ withdrawn
                   │
                   └──→ draft ──→ approved
                        의미 변경    재승인
```

규칙은 다음과 같다.

- `draft → approved` transition을 기록한 main governance commit이 specification baseline을 생성한다.
- `approved` REQ의 의미 또는 대상 AC의 PASS/FAIL 결과를 바꾸려면 영향받는 구현을 중단하고 REQ를 먼저 `draft`로 전환해야 한다.
- 변경된 REQ와 `draft` AC는 함께 검토한다. REQ가 `draft → approved` transition을 거치면 대상 AC는 변경 종류에 따라 `active` 또는 `retired`로 확정되고, 해당 commit이 새로운 specification baseline이 된다.
- 재승인 전까지 이전 `approved` Git revision은 마지막 승인 baseline으로 보존되지만, 변경 중인 `draft` revision을 구현 기준으로 사용할 수 없다.
- `draft` 또는 `approved` REQ는 `withdrawn`으로 전환할 수 있다.
- `withdrawn`은 terminal status이다. 같은 변경을 다시 진행하려면 독립적인 새 Line과 새 Discovery/REQ를 만든다.
- `approved` 상태를 유지한 채 REQ 또는 대상 AC의 의미를 변경하는 것은 허용하지 않는다.
- implementation branch는 `approved` transition을 기록한 exact main commit에서만 생성할 수 있다.
- 구현·검증·delivery 진행 상태는 REQ `status`에 기록하지 않는다.

다음 값은 REQ `status`가 아니다.

```text
implementing
tested
verified
delivered
```

이 값들이 필요하다면 별도의 execution state vocabulary에서 정의한다.

## 11. AC lifecycle status

AC의 `status`는 해당 AC revision의 canonical specification lifecycle을 표현하며 다음 세 값만 허용한다.

```text
draft
active
retired
```

| Status | 의미 |
| --- | --- |
| `draft` | REQ에서 생성 또는 수정 대상으로 검토 중인 candidate AC |
| `active` | 승인된 REQ baseline에 포함된 현재 canonical 사양 |
| `retired` | 승인된 REQ에 의해 폐기되어 더 이상 적용되지 않는 사양 |

허용 transition은 다음과 같다.

```text
새 AC       draft ─────────→ active
기존 AC     active → draft → active
기존 AC     active ────────→ retired
```

규칙은 다음과 같다.

- REQ의 `create` 또는 `update` 대상인 `draft` AC는 해당 REQ가 승인될 때 `active`가 된다.
- 기존 `active` AC의 의미를 변경하려면 변경 REQ와 함께 AC를 `draft`로 전환하고 재검토해야 한다.
- 변경 중인 AC가 `draft`인 동안 이전 `active` Git revision이 마지막 승인 baseline으로 유지된다.
- REQ의 `retire` 대상인 `active` AC는 해당 REQ가 승인될 때 `retired`가 된다.
- 승인 전에 새 AC 도입을 철회하면 아직 승인된 적 없는 `draft` AC 파일을 제거한다.
- 기존 AC 수정이 철회되면 candidate `draft` revision을 버리고 마지막 `active` revision을 복원한다.
- `retired`는 terminal status이다. 같은 사양이 다시 필요하면 새 AC identity를 만든다.
- `implemented`, `tested`, `passed`, `failed`는 AC `status`가 아니다.

## 12. Micro-SPEC status

Micro-SPEC은 계획 승인과 구현 진행을 혼합하지 않고 `spec_status`와 `implementation_status` 두 축으로 관리한다.

### 12.1 Specification status

`spec_status`는 다음 세 값만 허용한다.

```text
draft
approved
withdrawn
```

| Status | 의미 |
| --- | --- |
| `draft` | 구현 범위와 방법을 작성·검토·수정하는 중 |
| `approved` | 구현 범위와 방법이 승인되어 구현을 시작할 수 있음 |
| `withdrawn` | 해당 Micro-SPEC을 더 이상 구현하지 않음 |

허용 transition은 다음과 같다.

```text
draft ───────→ approved
  │                │
  └──→ withdrawn   ├──→ withdrawn
                   │
                   └──→ draft ──→ approved
                        의미 변경    재승인
```

- parent REQ가 `approved`여야 Micro-SPEC을 `approved`로 전환할 수 있다.
- Micro-SPEC의 `spec_status`가 `approved`여야 구현을 시작할 수 있다.
- 승인된 구현 범위나 방법을 의미 있게 변경하려면 `approved → draft`로 전환하고 영향받는 구현을 중단한다.
- 변경된 Micro-SPEC을 재검토하여 다시 `approved`로 전환한 뒤 구현을 재개한다.
- `withdrawn`은 terminal status이다.

### 12.2 Implementation status

`implementation_status`는 다음 세 값만 허용한다.

```text
not_started
in_progress
implemented
```

허용 transition은 다음과 같다.

```text
not_started → in_progress → implemented
implemented → in_progress → implemented
              재작업
```

- `not_started`는 승인 여부와 별개로 구현 작업을 시작하지 않은 상태이다.
- `in_progress`는 코드, test 또는 관련 문서를 구현 중인 상태이다.
- `implemented`는 Micro-SPEC이 요구한 구현 변경을 완료한 상태이다.
- `implemented`는 검증 통과를 의미하지 않는다.
- `spec_status`가 `withdrawn`이면 진행 중인 구현을 중단하며 더 이상 implementation transition을 진행하지 않는다.
- 구현 검증 상태와 결과는 같은 Micro-SPEC의 IQC artifact가 소유한다.

예시는 다음과 같다.

```yaml
---
id: ms-0001-001
parent_req: req-0001
criteria:
  - ac-0001
  - ac-0002
spec_status: approved
implementation_status: in_progress
---
```

### 12.3 IQC result

각 Micro-SPEC은 다음 고정 경로의 IQC artifact를 하나 가진다.

```text
.proofline/lines/line-<NNNN>/micro-specs/iqc-<NNNN>-<SSS>.md
```

`result`는 다음 네 값만 허용한다.

```text
draft
passed
failed
blocked
```

- `draft`는 검증을 준비하거나 결과를 작성 중인 상태이다.
- `passed`는 필수 검사를 실행하고 판정 기준을 만족한 상태이다.
- `failed`는 검사를 실행했으나 하나 이상의 필수 판정이 실패한 상태이다.
- `blocked`는 환경이나 필수 입력 문제로 검증을 완료하지 못한 상태이다.
- IQC는 exact Micro-SPEC commit과 실제 검증한 implementation commit을 함께 bind해야 한다.
- 재검증할 때 같은 IQC 파일을 갱신하고 과거 결과는 Git history로 보존한다. Attempt별 IQC 파일은 만들지 않는다.
- 대용량 원시 log나 binary evidence는 canonical tree에 복사하지 않고 안정적인 저장소 경로나 외부 참조를 IQC에 기록한다. 장기 보존이 필요한 evidence는 가능한 경우 digest를 함께 기록한다.
- `passed`는 해당 Micro-SPEC 구현의 검증 통과만 의미하며 main 통합, Line delivery 또는 release를 승인하지 않는다.

### 12.4 DQC result

`verifying` 단계의 Line은 다음 고정 경로의 DQC artifact를 하나 가진다.

```text
.proofline/lines/line-<NNNN>/dqc-<NNNN>.md
```

DQC는 모든 Micro-SPEC이 합쳐진 exact integration candidate commit을 Line 전체 관점에서 검증한다. `result`는 IQC와 같은 네 값을 사용한다.

```text
draft
passed
failed
blocked
```

- DQC를 시작하기 전에 모든 non-withdrawn Micro-SPEC의 구현과 IQC를 포함한 `candidate_commit`을 고정한다.
- DQC는 모든 대상 AC의 Micro-SPEC 배정, 모든 필수 IQC PASS, Micro-SPEC 간 충돌·회귀, Line 전체 test와 REQ 범위를 검증한다.
- `passed`는 해당 `candidate_commit`이 main 통합 gate를 요청할 수 있음을 의미하며 아직 main 통합이나 delivery를 의미하지 않는다.
- 재검증할 때 같은 DQC 파일을 갱신하고 과거 결과는 Git history로 보존한다.

## 13. Line execution artifact와 status

각 Line은 다음 canonical artifact를 정확히 하나 소유한다.

```text
.proofline/lines/line-<NNNN>/line-<NNNN>.md
```

Line artifact는 Line identity와 전체 execution 상태를 소유한다. 최소 형태는 다음과 같다.

```yaml
---
id: line-0001
execution_status: not_started
---
```

`execution_status`는 다음 다섯 값만 허용한다.

```text
not_started
in_progress
verifying
delivered
cancelled
```

| Status | 의미 |
| --- | --- |
| `not_started` | Line은 존재하지만 implementation branch를 아직 시작하지 않음 |
| `in_progress` | Micro-SPEC 작성 또는 구현을 진행 중 |
| `verifying` | 구현을 마치고 Line 전체 검증을 진행 중 |
| `delivered` | 검증을 통과한 결과가 main에 통합됨 |
| `cancelled` | Discovery 또는 REQ 철회로 Line 실행을 중단함 |

기본 transition은 다음과 같다.

```text
not_started → in_progress → verifying → delivered
                 ↑            │
                 └────────────┘
                   검증 실패

not_started ─→ cancelled
in_progress ─→ cancelled
verifying   ─→ cancelled
```

규칙은 다음과 같다.

- Line을 main governance에서 생성할 때 `execution_status`는 `not_started`이다.
- 승인된 REQ baseline에서 implementation branch를 생성할 때 `not_started → in_progress`로 전환한다.
- 같은 Line의 모든 non-withdrawn Micro-SPEC이 `spec_status: approved`, `implementation_status: implemented`이고 대응하는 IQC가 `result: passed`이면 `in_progress → verifying`로 전환할 수 있다.
- 검증이 실패하거나 재작업이 필요하면 `verifying → in_progress`로 전환한다.
- Discovery 또는 REQ가 `withdrawn`이면 완료되지 않은 Line을 `cancelled`로 전환한다.
- `delivered`와 `cancelled`는 terminal status이다.

### 13.1 Main 통합 gate

Line implementation branch는 다음 조건을 모두 만족해야 main에 통합할 수 있다.

```text
Discovery.status = confirmed
REQ.status = approved
Line.execution_status = verifying
모든 non-withdrawn Micro-SPEC.spec_status = approved
모든 non-withdrawn Micro-SPEC.implementation_status = implemented
모든 대응 IQC.result = passed
DQC.result = passed
canonical artifact validation = passed
통합 대상 canonical artifact의 governance placeholder = 0개
Line verification 또는 delivery까지 해소하기로 한 deferred Open Question = 0개
```

추가 binding 규칙은 다음과 같다.

- 각 IQC의 `micro_spec_commit`과 `implementation_commit`은 실제 검증한 exact commit이어야 한다.
- DQC의 `candidate_commit`은 모든 대상 Micro-SPEC 구현, IQC PASS 및 `execution_status: verifying`를 포함한 exact commit이어야 한다.
- REQ의 대상 AC 집합과 각 AC lifecycle이 승인 baseline에 일치해야 한다.
- `Exit Condition`이 Line verification 또는 delivery를 가리키는 deferred Open Question은 DQC PASS 전에 해소하고 답을 canonical owner section에 반영해야 한다.
- DQC PASS를 기록한 뒤 main 통합 전까지 제품 source, test, build 또는 runtime configuration을 변경할 수 없다. 변경하면 새 `candidate_commit`을 고정하고 영향받는 IQC와 DQC를 다시 수행한다.
- Main 통합은 commit identity를 바꾸지 않는 fast-forward 방식만 허용한다. Main이 진행되어 fast-forward할 수 없으면 implementation branch에 새 main을 반영하고 candidate를 다시 고정한 뒤 DQC를 다시 수행한다.
- Squash, cherry-pick 또는 commit을 다시 작성하는 통합은 기존 IQC와 DQC binding을 무효화하므로 허용하지 않는다.

### 13.2 Line delivery 판정

Main 통합과 Line delivery는 다음 순서로 수행한다.

```text
DQC passed
→ main 통합 gate 확인
→ implementation branch를 main에 fast-forward
→ 통합된 exact commit과 canonical artifact를 확인
→ main에서 Line.execution_status를 delivered로 전환
```

`verifying → delivered` transition은 다음 조건을 모두 만족해야 한다.

- DQC가 `result: passed`이고 그 `candidate_commit`이 main history에 exact commit으로 존재해야 한다.
- Main에 통합된 branch head는 DQC PASS 이후 제품 source, test, build 또는 runtime configuration을 변경하지 않아야 한다.
- Main에서 canonical artifact validation이 통과해야 한다.
- Delivery transition commit은 main의 직렬화된 governance 흐름에서 작성하며, 그 직전 parent는 통합된 implementation branch head여야 한다.
- 위 조건을 만족하기 전에는 Line을 `delivered`로 기록할 수 없다.

통합 확인에 실패하면 Line은 `verifying`에 남는다. 구현 변경이 필요하면 `verifying → in_progress`로 되돌리고 영향받는 IQC와 DQC를 다시 수행한다.

## 14. Artifact 문서 형식과 최소 필수 field

모든 canonical artifact 문서는 UTF-8 Markdown과 YAML frontmatter를 사용한다.

```markdown
---
# machine-readable metadata
---

# Human-readable content
```

공통 규칙은 다음과 같다.

- YAML frontmatter는 문서의 첫 줄에서 `---`로 시작하고 닫는 `---`로 종료한다.
- 모든 artifact는 `id`를 가져야 하며 `id`는 파일명과 일치해야 한다.
- Line 내부 artifact의 Line 번호는 상위 `line-<NNNN>/` directory 번호와 일치해야 한다.
- status field는 각 artifact에 대해 이 문서가 정의한 vocabulary만 사용한다.
- reference field는 존재하는 canonical artifact를 가리켜야 한다.
- `created_at`, `updated_at`, `author`, `last_modified_by`는 최소 필수 field가 아니다. 작성자와 변경 시점은 Git history가 소유한다.

artifact별 최소 필수 frontmatter는 다음과 같다.

### 14.1 Line

```yaml
---
id: line-0001
execution_status: not_started
---
```

필수 field:

```text
id
execution_status
```

### 14.2 Discovery

```yaml
---
id: dcy-0001
status: draft
---
```

필수 field:

```text
id
status
```

### 14.3 REQ

```yaml
---
id: req-0001
status: draft
discovery: dcy-0001
criteria:
  create: []
  update: []
  retire: []
---
```

필수 field:

```text
id
status
discovery
criteria
criteria.create
criteria.update
criteria.retire
```

- `criteria.create`, `criteria.update`, `criteria.retire`는 각각 빈 list일 수 있지만 세 field를 모두 명시해야 한다.
- 세 list의 합집합에는 최소 하나의 AC가 있어야 한다.
- 같은 AC를 둘 이상의 list에 중복해서 기록할 수 없다.
- `discovery`는 같은 Line의 유일한 Discovery를 가리켜야 한다.

### 14.4 AC

```yaml
---
id: ac-0001
status: draft
---
```

필수 field:

```text
id
status
```

AC의 변경 이력을 나타내는 `introduced_by`, `governing_req`, `last_updated_by` 같은 reverse reference는 최소 필수 field가 아니다. REQ의 AC 변경 집합과 Git history가 해당 관계와 이력을 소유한다.

### 14.5 Micro-SPEC

```yaml
---
id: ms-0001-001
parent_req: req-0001
criteria:
  - ac-0001
spec_status: draft
implementation_status: not_started
---
```

필수 field:

```text
id
parent_req
criteria
spec_status
implementation_status
```

- `criteria`에는 최소 하나의 AC가 있어야 한다.
- `criteria`의 모든 AC는 parent REQ의 `create`, `update`, `retire` 합집합에 포함되어야 한다.
- `parent_req`는 같은 Line의 유일한 REQ를 가리켜야 한다.

### 14.6 IQC

```yaml
---
id: iqc-0001-001
micro_spec: ms-0001-001
micro_spec_commit: "<git-commit>"
implementation_commit: "<git-commit>"
result: draft
---
```

필수 field:

```text
id
micro_spec
micro_spec_commit
implementation_commit
result
```

- `id`의 `NNNN-SSS`는 `micro_spec`과 일치해야 한다.
- `micro_spec_commit`은 검증 기준으로 사용한 exact Micro-SPEC commit이다.
- `implementation_commit`은 실제 검증한 implementation commit이다.
- 두 commit field는 해당 저장소에서 해석되는 exact Git commit이어야 한다.

### 14.7 DQC

```yaml
---
id: dqc-0001
line: line-0001
candidate_commit: "<git-commit>"
result: draft
---
```

필수 field:

```text
id
line
candidate_commit
result
```

- `id`의 `NNNN`은 `line`과 상위 Line directory 번호에 일치해야 한다.
- `candidate_commit`은 DQC가 실제 검증한 exact Line integration candidate commit이다.
- `candidate_commit`은 해당 저장소에서 해석되는 exact Git commit이어야 한다.

### 14.8 Markdown 본문 schema

Line을 제외한 canonical artifact의 Markdown 본문은 정확히 하나의 H1 제목을 가져야 한다. 제목은 사람이 artifact를 식별하기 위한 canonical label이며 같은 값을 `title` frontmatter로 중복하지 않는다.

필수 H2 section은 각 artifact에 대해 아래에 정의한 이름과 순서를 사용하고 각각 정확히 한 번만 나타나야 한다. 허용된 선택 H2 외의 임의 H2 section은 사용할 수 없다. H3 이하 heading, list 및 checklist는 소유 H2 section 안에서 사용할 수 있다.

`draft` 상태에서도 H1과 필수 H2 heading은 모두 존재해야 한다. 미완성 section은 비워 두지 않고 이 문서가 허용한 governance placeholder로 표시한다. `confirmed`, `approved`, `active` 또는 `retired` 완결성 gate에서는 모든 필수 section에 실질적인 내용이 있어야 하고 placeholder가 없어야 한다. `withdrawn` artifact는 철회 시점의 미완성 내용을 보존할 수 있다.

#### Line

Line artifact는 execution manifest이므로 Markdown 본문을 갖지 않는다. 닫는 YAML frontmatter delimiter 뒤에는 공백과 마지막 newline 외의 내용을 기록하지 않는다.

#### Discovery

필수 본문 schema는 다음과 같다.

```markdown
# 제목

## Problem

## Evidence

## Scope

## Out of Scope

- 이번 Discovery에서 의도적으로 제외하는 범위 1
- 이번 Discovery에서 의도적으로 제외하는 범위 2
```

각 section의 canonical 의미는 다음과 같다.

- `Problem`: 해결해야 할 문제
- `Evidence`: 문제가 실제로 존재한다는 근거
- `Scope`: 이번 Discovery가 다루는 범위
- `Out of Scope`: 의도적으로 제외하는 범위

`Out of Scope`는 Markdown table이 아닌 unordered list로 작성한다. 서로 독립적인 제외 범위는 각각 별도 list item으로 기록한다. 명시적으로 제외할 범위가 없으면 `- 없음`이라고 기록한다.

다음 H2는 필요할 때만 `Out of Scope` 뒤에 추가할 수 있는 유일한 선택 section이다.

```markdown
## Risks and Unknowns
```

Open Question이 있으면 `Risks and Unknowns` 아래에 다음 H3를 사용한다.

```markdown
### Open Questions

- `OQ-001`
  - Type: `DECIDE`
  - Status: {{TODO: API 호환성 정책을 결정해야 함}}
  - Question: 기존 API 호환성을 유지해야 하는가?
  - Owner: product owner
  - Exit Condition: 호환성 정책을 명시적으로 결정하고 그 결과를 Scope 또는 Out of Scope에 반영한다.

- `OQ-002`
  - Type: `DATA`
  - Status: `deferred`
  - Question: 실제 장치의 최대 처리 지연은 얼마인가?
  - Owner: verification
  - Exit Condition: Line verification에서 장치 측정 결과를 기록한다.
```

Open Question은 별도 artifact나 frontmatter metadata로 만들지 않고 Discovery 본문이 소유한다. Markdown table은 사용하지 않으며, 각 질문을 최상위 unordered list item으로 기록하고 구조화된 field를 하위 list item으로 기록한다. 각 field의 규칙은 다음과 같다.

- `ID`는 같은 Discovery 안에서 `OQ-001`부터 시작하는 안정적인 local identity이다. ID를 renumber하거나 제거된 ID를 재사용하지 않는다.
- `Type`은 `DECIDE`, `CONFIRM`, `DATA` 중 하나여야 한다.
  - `DECIDE`는 책임 있는 사람 또는 authority의 product, policy, priority, scope 또는 risk 선택이 필요함을 뜻한다.
  - `CONFIRM`은 fact, interpretation 또는 boundary를 evidence로 확인해야 함을 뜻한다.
  - `DATA`는 측정이나 실행을 통해 후속 evidence를 수집해야 함을 뜻한다.
- `Status`는 필수 field이며 다음 중 정확히 하나여야 한다.
  - `{{TODO: ...}}`, `{{UNKNOWN: ...}}`, `{{NEEDS_EVIDENCE: ...}}` 중 하나의 governance placeholder: 아직 해소되지 않아 Discovery confirmation을 차단하는 질문
  - `answered`: 질문이 해소되고 답이 canonical owner section에 반영된 상태
  - `deferred`: REQ 의미를 바꾸지 않으며 명시적인 후속 단계에서 해소하도록 이관한 상태
- 미해결 질문의 성격에 따라 결정이나 작업이 필요하면 `TODO`, 필요한 사실을 아직 모르면 `UNKNOWN`, 근거가 필요하면 `NEEDS_EVIDENCE`를 사용한다.
- 미해결 질문의 `Status`는 하나의 governance placeholder 전체로 표현하며 placeholder 밖에 별도의 상태 문자열을 함께 쓰지 않는다.
- `Owner`는 질문을 결정·확인하거나 후속 evidence를 수집할 책임 주체 또는 단계를 명시한다.
- `Exit Condition`은 질문을 해소할 판정 조건이나 명시적인 후속 stage·artifact·evidence를 기록한다. `나중에 결정`, `추후 확인`처럼 대상과 조건이 없는 표현은 허용하지 않는다.

질문의 답에 따라 Discovery의 Scope, REQ의 대상 AC 집합 또는 동일 구현의 PASS/FAIL 결과가 달라질 수 있으면 `Status`를 governance placeholder로 유지해야 하며 `deferred`로 전환할 수 없다. Product·policy·compatibility·scope·risk acceptance 결정도 같은 규칙을 따른다.

REQ 의미를 바꾸지 않고 후속 단계에서만 얻을 수 있는 implementation detail이나 measurement evidence는 `deferred`로 둘 수 있다. 이 경우 해소할 stage, artifact 또는 evidence를 `Exit Condition`에 구체적으로 기록해야 한다.

Discovery를 `confirmed`로 전환할 때는 다음 gate를 모두 만족해야 한다.

```text
Open Question을 포함한 Discovery 전체의 governance placeholder = 0개
모든 Open Question에 필수 field 존재
모든 Open Question의 Status가 구조적으로 유효함
모든 deferred Open Question에 명시적인 Owner와 Exit Condition 존재
```

Open Question 전용 blocking gate를 별도로 두지 않는다. 일반 artifact 완결성 gate가 문서 전체의 `{{...}}`를 검사하므로 미해결 Open Question의 `Status` placeholder도 다른 미완성 내용과 함께 Discovery confirmation을 차단한다. 다만 `Status` field 삭제로 이 검사를 우회할 수 없도록 Open Question 구조 검증은 confirmation gate 전에 항상 수행한다.

질문에 답이 나오면 답을 해당 사실의 canonical owner section에 먼저 반영한 뒤 `Status`를 `answered`로 전환한다.

```text
fact 또는 근거 확인       → Evidence
포함 범위 결정            → Scope
제외 범위 결정            → Out of Scope
risk 또는 dependency      → Risks and Unknowns
Line delivery 목표        → REQ Objective
implementation 범위       → REQ Scope
atomic product behavior   → AC Criterion
판정 방법                 → AC Verification
```

`answered`는 Discovery confirmation을 차단하지 않는다. 현재 상태 중심의 문서를 유지하려면 답의 반영을 검토한 뒤 해당 Open Question 항목을 제거할 수 있으며, 질문과 해소 이력은 Git history가 보존한다. 항목을 유지하더라도 답의 canonical source of truth는 `Evidence`, `Scope`, `Out of Scope` 또는 그 밖의 해당 owner section이며 Open Question에 답을 중복 기록하지 않는다.

질문이 더 이상 유효하지 않으면 그에 따른 canonical owner section의 변경을 먼저 반영한 뒤 해당 list item을 제거한다. 별도의 `dropped` 상태는 두지 않으며 제거 이력은 Git history가 보존한다.

무엇을 물어야 할지조차 아직 작성하지 못한 일반 drafting placeholder와 구조화된 Open Question을 혼동하지 않는다. Open Question은 ID, Type, Status, Question, Owner 및 Exit Condition을 모두 가져야 하며, 이 중 `Status` placeholder만 그 질문이 아직 confirmation을 차단한다는 상태를 소유한다. Confirmed Discovery에는 governance placeholder가 없어야 하지만 명시적으로 `deferred`되거나 이미 `answered`된 Open Question은 남을 수 있다.

Discovery 결론은 `status: confirmed`가 소유하므로 별도 `Decision` section을 두지 않는다.

#### REQ

필수 본문 schema는 다음과 같다.

```markdown
# 제목

## Objective

## Scope

## Non-Goals
```

각 section의 canonical 의미는 다음과 같다.

| Section | 소유하는 사실 |
| --- | --- |
| `Objective` | 이번 Line에서 전달할 결과 |
| `Scope` | 승인되는 Line-level 구현 범위 |
| `Non-Goals` | 이번 delivery에서 구현하지 않을 내용 |

다음 H2는 필요할 때만 `Scope`와 `Non-Goals` 사이에 추가할 수 있는 유일한 선택 section이다.

```markdown
## Constraints
```

REQ 본문은 AC의 Criterion 또는 Verification 내용을 복제하지 않는다.

#### AC

필수 본문 schema는 다음과 같다.

```markdown
# 제목

## Criterion

## Verification
```

| Section | 소유하는 사실 |
| --- | --- |
| `Criterion` | 독립적으로 PASS/FAIL 가능한 하나의 normative specification |
| `Verification` | Criterion을 판정할 관찰 또는 검사 방법 |

AC의 `Verification`은 판정 방법을 정의하며 실행 결과를 저장하지 않는다.

#### Micro-SPEC

필수 본문 schema는 다음과 같다.

```markdown
# 제목

## Scope

## Implementation

## Verification
```

| Section | 소유하는 사실 |
| --- | --- |
| `Scope` | 이 Micro-SPEC이 담당하는 기술적 경계 |
| `Implementation` | 변경할 component와 구현 작업 |
| `Verification` | 실행할 test와 검사 계획 |

Micro-SPEC의 목표는 H1 제목, `parent_req` 및 `criteria`가 함께 나타내므로 별도 `Goal` section을 두지 않는다. 변경 대상은 `Implementation`에 포함하므로 별도 `Changes` section을 두지 않는다. 구현 완료는 `implementation_status`가 소유하므로 별도 `Completion Conditions` section을 두지 않는다. Micro-SPEC의 `Verification`은 검증 계획이며 실제 실행 결과는 대응하는 IQC가 소유한다.

#### IQC

필수 본문 schema는 다음과 같다.

```markdown
# IQC: 제목

## Target

## Checks

## Criteria Results

## Result
```

| Section | 소유하는 사실 |
| --- | --- |
| `Target` | 검증 대상 Micro-SPEC과 implementation의 설명 |
| `Checks` | 실제 실행한 test·검사와 결과 및 evidence 참조 |
| `Criteria Results` | Micro-SPEC이 담당하는 AC별 검증 결과 |
| `Result` | IQC 전체 판정과 필요한 설명 |

원시 log나 binary evidence를 본문에 복제하지 않는다. `Checks`에는 검증에 사용한 명령, exit code, 결과 요약과 안정적인 evidence 경로 또는 참조를 기록한다.

#### DQC

필수 본문 schema는 다음과 같다.

```markdown
# DQC: 제목

## Target

## IQC Results

## Checks

## Criteria Results

## Result
```

| Section | 소유하는 사실 |
| --- | --- |
| `Target` | 검증 대상 Line과 candidate commit의 설명 |
| `IQC Results` | 모든 대상 Micro-SPEC과 대응 IQC PASS의 종합 |
| `Checks` | Line 전체 test, 회귀·충돌 및 REQ 범위 검사 결과 |
| `Criteria Results` | REQ 대상 AC 전체의 종합 판정 |
| `Result` | DQC 전체 판정과 필요한 설명 |

### 14.9 Frontmatter metadata policy

초기 ProofLine canonical schema는 각 artifact에 대해 이 문서가 명시한 최소 필수 frontmatter field만 허용한다.

```text
optional metadata 없음
unknown field 금지
x-* extension field 금지
```

Validator는 정의되지 않은 frontmatter key를 오류로 처리한다. 이는 field 이름 오타가 조용히 무시되는 것을 막고 canonical 사실이 임의 metadata로 분산되는 것을 방지한다.

`created_at`, `updated_at`, `author`, `owner`, `reviewer`, `priority`, `due_date`, `branch` 등은 현재 schema에 추가하지 않는다. IQC의 `micro_spec_commit`과 `implementation_commit`, DQC의 `candidate_commit`처럼 이 문서가 명시적으로 정의한 exact binding 외에는 Git 또는 외부 시스템이 이미 소유하는 사실을 중복하지 않는다. 새 metadata가 필요하면 이 문서를 먼저 개정하고 template과 validator를 함께 갱신한다.

### 14.10 최소 필수 field 요약

다음은 artifact별 최소 필수 frontmatter 요약이다.

```yaml
# line-NNNN.md
id:
execution_status:

# dcy-NNNN.md
id:
status:

# req-NNNN.md
id:
status:
discovery:
criteria:
  create:
  update:
  retire:

# ac-NNNN.md
id:
status:

# ms-NNNN-SSS.md
id:
parent_req:
criteria:
spec_status:
implementation_status:

# iqc-NNNN-SSS.md
id:
micro_spec:
micro_spec_commit:
implementation_commit:
result:

# dqc-NNNN.md
id:
line:
candidate_commit:
result:
```

## 15. Placeholder 문법과 완결성 gate

ProofLine template과 미완성 canonical artifact의 placeholder는 이중 중괄호 문법을 사용한다.

```text
{{NAME}}
{{NAME: description}}
```

`NAME`은 대문자로 시작하는 `UPPER_SNAKE_CASE`여야 한다. description은 같은 줄에 작성하며 `:` 뒤에 하나 이상의 공백을 둔다.

일반 placeholder 문법은 다음 정규식과 같다.

```regex
\{\{[A-Z][A-Z0-9_]*(?:: [^{}\n]+)?\}\}
```

유효한 예시는 다음과 같다.

```text
{{TITLE}}
{{TIMEOUT_SECONDS}}
{{TODO}}
{{UNKNOWN: confirm the target timeout}}
{{NEEDS_EVIDENCE: attach the target-device log}}
```

다음 형식은 허용하지 않는다.

```text
{{todo}}
{{Needs Evidence}}
{{timeout-seconds}}
{{TODO:nospace}}
{{}}
{{ }}
{{OUTER: {{INNER}}}}
```

### 15.1 Template variable과 governance placeholder

Template file은 실제 값으로 치환될 일반 variable을 사용할 수 있다.

```text
{{ARTIFACT_ID}}
{{TITLE}}
```

Template에서 YAML scalar 전체를 placeholder로 표현할 때는 YAML의 flow mapping 문법과 충돌하지 않도록 반드시 문자열로 quote한다.

```yaml
id: "{{ARTIFACT_ID}}"
title: "{{TITLE}}"
```

다음 형태는 사용하지 않는다.

```yaml
id: {{ARTIFACT_ID}}
```

생성된 canonical artifact에서는 template variable이 모두 실제 값으로 치환되어야 한다. Canonical artifact에 남길 수 있는 미완성 governance placeholder의 이름은 다음 세 값으로 제한한다.

```text
TODO
UNKNOWN
NEEDS_EVIDENCE
```

각 의미는 다음과 같다.

| Name | 의미 |
| --- | --- |
| `TODO` | 작성할 내용은 알지만 아직 작성하지 않음 |
| `UNKNOWN` | 결정에 필요한 사실을 아직 알지 못함 |
| `NEEDS_EVIDENCE` | 주장이나 판단을 뒷받침할 근거가 아직 없음 |

Canonical artifact에서 허용되는 placeholder 정규식은 다음과 같다.

```regex
\{\{(?:TODO|UNKNOWN|NEEDS_EVIDENCE)(?:: [^{}\n]+)?\}\}
```

현재 canonical schema에는 선택 metadata가 없고 모든 최소 필수 field가 구조적으로 유효해야 하므로 canonical artifact의 YAML frontmatter에는 placeholder를 사용할 수 없다. Canonical artifact의 governance placeholder는 Markdown 본문에서만 사용할 수 있다.

향후 placeholder를 허용하는 선택 string field를 canonical schema에 도입한다면 이 문서를 먼저 개정해야 하며, 해당 YAML scalar는 반드시 quote해야 한다.

`id`, status field, reference, list 또는 mapping처럼 모든 상태에서 구조적으로 유효해야 하는 최소 필수 field에는 placeholder를 사용할 수 없다.

### 15.2 상태별 허용 규칙

미완성 governance placeholder는 다음 상태에서만 허용한다.

```text
Discovery.status: draft
REQ.status: draft
AC.status: draft
Micro-SPEC.spec_status: draft
IQC.result: draft
DQC.result: draft
```

다음 상태로 전환하기 전에는 해당 artifact의 모든 placeholder를 제거해야 한다.

```text
Discovery.status: confirmed
REQ.status: approved
AC.status: active
AC.status: retired
Micro-SPEC.spec_status: approved
IQC.result: passed
IQC.result: failed
IQC.result: blocked
DQC.result: passed
DQC.result: failed
DQC.result: blocked
```

각 transition의 완결성 gate는 다음과 같다.

```text
artifact 안에 {{...}} placeholder가 0개여야 한다.
```

`withdrawn` artifact는 완료된 사양이 아니므로 기존 governance placeholder가 남아 있을 수 있다.

```text
Discovery.status: withdrawn
REQ.status: withdrawn
Micro-SPEC.spec_status: withdrawn
```

Line artifact는 모든 상태에서 frontmatter-only이며 canonical frontmatter에는 placeholder를 사용할 수 없으므로 어떤 `execution_status`에서도 placeholder를 허용하지 않는다.

Micro-SPEC의 `implementation_status` transition만으로는 placeholder 허용 여부가 바뀌지 않는다. Placeholder 완결성은 `spec_status`가 소유한다.

IQC는 `result: draft`에서만 본문 placeholder를 허용한다. `passed`, `failed` 또는 `blocked`로 판정하기 전에는 모든 placeholder를 제거해야 한다.

DQC도 `result: draft`에서만 본문 placeholder를 허용한다. `passed`, `failed` 또는 `blocked`로 판정하기 전에는 모든 placeholder를 제거해야 한다.

Validator는 canonical artifact에서 다음을 검사해야 한다.

- 일반 `{{...}}` 형태가 문법에 맞는지
- canonical artifact의 placeholder 이름이 `TODO`, `UNKNOWN`, `NEEDS_EVIDENCE` 중 하나인지
- canonical artifact의 YAML frontmatter에 placeholder가 없는지
- 최소 필수 structural field에 placeholder가 사용되지 않았는지
- Discovery의 각 Open Question에 ID, Type, Status, Question, Owner 및 Exit Condition이 모두 있는지
- Open Question의 Status가 governance placeholder 전체, `answered` 또는 `deferred` 중 하나인지
- `deferred` Open Question에 구체적인 Owner와 Exit Condition이 있는지
- IQC와 DQC의 ID, reference 및 commit binding이 해당 Line 구조와 일치하는지
- 완결성 gate를 요구하는 상태에 placeholder가 남아 있지 않은지

ProofLine template은 생성 입력이고 `.proofline/` 아래의 canonical artifact가 아니다. Template의 일반 variable 허용 규칙과 canonical artifact의 제한된 governance placeholder 규칙을 혼합하지 않는다.

## 16. 정본과 파생 산출물

`.proofline/` 아래에서 이 문서가 canonical path로 정의한 산출물은 Git으로 추적하는 정본이다.

ProofLine은 별도의 자동 생성 index 파일을 만들지 않는다. Writer, validator 및 agent는 이 문서의 deterministic canonical path를 직접 탐색해야 하며 index 파일을 discovery나 lifecycle gate의 입력으로 요구하지 않는다.

사용자용 파생 문서는 프로젝트의 `docs/` 아래에 개별 고정 경로로 저장한다. `docs/` 전체를 ProofLine 전용 directory로 소유하지 않으며, 새로운 파생 문서를 추가하려면 이 문서에 정확한 output path와 생성 계약을 먼저 정의해야 한다.

현재 정의한 사용자용 파생 문서는 다음 하나이다.

```text
docs/requirements.md
```

`docs/requirements.md`는 프로젝트의 현재 전체 요구사항을 한 번에 확인하기 위한 문서이며, 지정된 source Git commit에서 `status: active`인 `.proofline/criteria/ac-*.md` 전체를 AC ID 순서로 펼쳐 생성한다. 각 AC의 ID, H1 제목, `Criterion` 및 `Verification`을 포함하며 `draft`와 `retired` AC는 포함하지 않는다.

생성 및 소유권 규칙은 다음과 같다.

- 문서 머리말에 자동 생성된 비정본 문서이며 직접 수정하지 않아야 함을 명시한다.
- 생성 기준으로 사용한 exact source Git commit을 머리말에 기록한다.
- 파일은 Git으로 추적하되 내용 변경은 generator를 통한 전체 재생성으로만 수행한다.
- 적용 프로젝트에 기존 `docs/requirements.md`가 있으면 writer는 덮어쓰지 않고 충돌로 보고하며 어떤 파일도 변경하지 않는다.
- 이 문서는 정본 상태를 변경하지 않으며 validation, approval, IQC, DQC, main 통합 또는 delivery gate의 입력으로 사용하지 않는다.
- 내용이 충돌하면 `.proofline/criteria/ac-*.md`와 해당 Git history를 우선한다.
- 자동 생성 index와 사용자용 파생 문서를 `.proofline/` 아래에 저장하지 않는다.

ProofLine의 canonical tree 안에 다음 내용을 저장하지 않는다.

- 실행 중에만 필요한 임시 파일
- 재생성 가능한 cache
- agent의 원시 대화 기록이나 debug log
- credential, token 및 기타 비밀 정보
- 적용 대상 프로젝트와 무관한 참고 자료

## 17. 보존 및 폐기 규칙

ProofLine의 canonical artifact는 현재 tree에서 마지막 canonical 상태를, Git history에서 이전 revision과 검증 attempt를 보존한다. Terminal artifact를 별도 archive directory로 이동하거나 lifecycle cleanup을 이유로 삭제하지 않는다.

```text
현재 canonical tree = 각 artifact의 마지막 canonical 상태
Git history          = 이전 specification revision과 verification attempt
```

### 17.1 Canonical terminal state와 검증 결과 보존

다음 artifact 또는 상태는 고정된 canonical path에서 무기한 보존한다.

```text
Line.execution_status: delivered | cancelled
Discovery.status: withdrawn
REQ.status: withdrawn
AC.status: retired
Micro-SPEC.spec_status: withdrawn
IQC.result: passed | failed | blocked
DQC.result: passed | failed | blocked
```

- `delivered` 또는 `cancelled` Line directory 전체를 제거하거나 다른 경로로 이동하지 않는다.
- Terminal Discovery, REQ, AC 및 Micro-SPEC을 삭제하지 않는다.
- IQC와 DQC는 재검증 시 같은 고정 파일을 갱신하며 이전 result와 evidence binding은 Git history로 확인한다.
- 별도 `.proofline/archive/` directory를 만들지 않으며 writer와 validator는 archive 이동을 lifecycle transition으로 해석하지 않는다.
- Terminal artifact가 다른 canonical artifact에 의해 참조되고 있지 않다는 이유만으로도 삭제할 수 없다.

### 17.2 Draft cleanup과 candidate 철회

승인된 적 없는 신규 draft 또는 명백히 잘못 생성된 artifact만 제한적으로 제거할 수 있다. 제거 전에 다음 조건을 모두 만족해야 한다.

```text
현재 artifact가 terminal 상태가 아님
confirmed, approved, active, retired 또는 delivered 이력이 없음
implementation 또는 non-draft IQC/DQC 결과의 근거가 아님
다른 canonical artifact가 참조하지 않음
제거 후 dangling reference가 생기지 않음
```

- 승인 전에 새 AC 도입을 철회하면 아직 승인된 적 없는 `draft` AC 파일을 제거할 수 있다.
- 잘못된 ID나 경로로 생성되어 canonical workflow에 사용되지 않은 artifact는 참조가 없음을 확인한 뒤 제거할 수 있다.
- 유효한 Line을 중단하는 경우 Line directory를 제거하지 않는다. Discovery와 REQ를 `withdrawn`, Line을 `cancelled`로 전환하고 현재 경로에 보존한다.
- 기존 canonical artifact의 candidate 변경을 철회할 때 파일을 삭제하지 않고 마지막 승인 revision을 복원한다.

```text
active AC 수정 철회
→ 마지막 active revision 복원

approved REQ 수정 철회
→ 마지막 approved revision 복원
```

Git history에 한 번이라도 등장한 Line, Discovery, REQ, AC, Micro-SPEC, IQC 또는 DQC identity는 current tree에서 파일이 제거됐더라도 다른 의미로 재사용하지 않는다.

### 17.3 IQC/DQC evidence 보존

IQC와 DQC Markdown에 기록한 검증 summary는 canonical artifact로 무기한 보존한다. 최소한 실제 실행한 command 또는 check, exit code나 판정 결과, 결과 요약, AC별 판정 및 evidence reference를 현재 파일 또는 Git history에서 확인할 수 있어야 한다.

대용량 원시 log, binary capture, 장치 trace 및 CI artifact는 canonical tree에 복사하지 않는다. 외부 evidence에는 다음 규칙을 적용한다.

- IQC 또는 DQC 판정 시점에는 evidence에 접근할 수 있어야 한다.
- IQC 또는 DQC에 안정적인 repository path나 external reference를 기록한다.
- 장기 감사가 필요한 evidence에는 가능한 경우 digest를 함께 기록한다.
- 원시 evidence의 실제 보존 기간은 적용 프로젝트 또는 외부 evidence store의 정책이 소유한다.
- 외부 evidence의 사후 만료만으로 과거 IQC/DQC result를 자동 변경하지 않는다. 다만 evidence를 다시 확인할 수 없다면 그 evidence에 의존하는 새로운 재검증 또는 새로운 verification claim은 `blocked`로 판정한다.

### 17.4 파생 산출물과 실행 부산물

`docs/requirements.md`는 비정본 파생 문서이므로 exact source commit에서 재생성할 수 있다. 삭제 또는 손상된 경우 generator로 전체 재생성하며, validation이나 main 통합 전에 지정된 source와 일치해야 한다.

다음 실행 부산물은 canonical lifecycle 이력이 아니므로 필요에 따라 삭제할 수 있다.

```text
임시 파일
재생성 가능한 cache
로컬 실행 log
download artifact
agent debug output
```

이러한 파일을 제거해도 canonical IQC/DQC에 필요한 evidence reference나 재현 정보를 손상해서는 안 된다.

### 17.5 보안·법적 삭제 예외

Credential, token, 개인정보, 법적으로 삭제해야 하는 데이터 또는 악성 binary가 canonical artifact나 Git history에 포함된 경우 일반 retention 규칙을 적용하지 않는다. 이를 repository security incident로 처리한다.

```text
working tree에서 민감 정보 제거
→ credential 폐기·교체
→ 필요 시 Git history 정화
→ mirror, cache 및 CI artifact 정리
→ 프로젝트의 incident 절차에 따라 기록
```

Artifact를 `withdrawn` 또는 Line을 `cancelled`로 전환하는 것만으로 민감 정보 제거를 완료한 것으로 간주하지 않는다. 보안·법적 삭제를 위해 history를 정화한 뒤에도 가능한 범위에서 비민감한 lifecycle 사실과 identity를 보존한다.

## 18. 프로젝트 설정

ProofLine을 적용한 프로젝트는 저장소 root에 다음 `proofline.yaml`을 둔다.

```yaml
schema_version: 1
artifact_root: .proofline
```

Schema version 1에서 `artifact_root`는 사용자 지정 option이 아니라 canonical artifact root를 명시하는 필수 상수이다.

```text
artifact_root = .proofline
```

규칙은 다음과 같다.

- `artifact_root` field는 반드시 존재해야 하며 값은 정확히 `.proofline`이어야 한다.
- 절대 경로, `..` segment, `.`, 빈 값 또는 `.proofline` 이외의 상대 경로는 허용하지 않는다.
- 프로젝트 root의 `.proofline` 자체가 symbolic link이면 validation error이다.
- Writer와 validator는 `.proofline` 이외의 directory를 canonical artifact root 후보로 탐색하거나 생성하지 않는다.
- `.proofline`과 다른 후보 directory가 함께 존재하더라도 `proofline.yaml` 설정으로 alternate root를 선택하거나 두 tree를 병합하지 않는다.
- `artifact_root` 값 변경은 일반 configuration 변경이나 artifact migration 방법이 아니다. 다른 값은 즉시 validation error로 처리한다.

Monorepo에서 하나의 공통 delivery boundary를 관리하면 해당 repository root에 하나의 `proofline.yaml`과 `.proofline/`을 둔다. 독립적인 여러 ProofLine project boundary가 필요하면 각 boundary의 project-root 및 Git governance 계약을 별도로 정의해야 하며, schema version 1의 `artifact_root` 사용자 지정으로 이를 구현하지 않는다.

향후 `.proofline` 이외의 root가 실제로 필요해지면 먼저 schema를 개정하고 다음 계약을 함께 정의해야 한다.

```text
허용 path 문법과 normalization
repository boundary 및 symbolic-link 검증
기존 root에서 새 root로의 migration
old/new root split-brain 탐지
canonical reference와 evidence reference 갱신
rollback 및 validator compatibility
```

해당 schema 개정과 migration 계약이 확정되기 전까지 모든 template, writer, validator 및 agent는 `.proofline`만 사용한다.
