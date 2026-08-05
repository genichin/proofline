# 문서 형식과 완결성 계약

이 문서는 모든 canonical artifact의 공통 YAML frontmatter 형식, 공통 Markdown 규칙, metadata policy, placeholder 문법 및 완결성 gate를 정의한다. Artifact별 field와 본문 schema는 각 artifact contract가 소유하며, 경로와 identity는 [산출물 디렉터리 구조](../artifact-layout.md)를 따른다.

## Artifact 문서 형식과 최소 필수 field

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

## Markdown 본문 공통 규칙

Line을 제외한 canonical artifact의 Markdown 본문은 정확히 하나의 H1 제목을 가져야 한다. 제목은 사람이 artifact를 식별하기 위한 canonical label이며 같은 값을 `title` frontmatter로 중복하지 않는다.

필수 H2 section은 각 artifact에 대해 아래에 정의한 이름과 순서를 사용하고 각각 정확히 한 번만 나타나야 한다. 허용된 선택 H2 외의 임의 H2 section은 사용할 수 없다. H3 이하 heading, list 및 checklist는 소유 H2 section 안에서 사용할 수 있다.

`draft` 상태에서도 H1과 필수 H2 heading은 모두 존재해야 한다. 미완성 section은 비워 두지 않고 이 문서가 허용한 governance placeholder로 표시한다. `confirmed`, `approved`, `active` 또는 `retired` 완결성 gate에서는 모든 필수 section에 실질적인 내용이 있어야 하고 placeholder가 없어야 한다. `withdrawn` artifact는 철회 시점의 미완성 내용을 보존할 수 있다.

## Frontmatter metadata policy

초기 ProofLine canonical schema는 각 artifact contract 문서가 명시한 최소 필수 frontmatter field만 허용한다.

```text
optional metadata 없음
unknown field 금지
x-* extension field 금지
```

Validator는 정의되지 않은 frontmatter key를 오류로 처리한다. 이는 field 이름 오타가 조용히 무시되는 것을 막고 canonical 사실이 임의 metadata로 분산되는 것을 방지한다.

`created_at`, `updated_at`, `author`, `owner`, `reviewer`, `priority`, `due_date`, `branch` 등은 현재 schema에 추가하지 않는다. Line의 `implementation_history`, IQC의 `micro_spec_commit`과 `implementation_commit`, DQC의 `candidate_commit`처럼 각 artifact contract 문서가 명시적으로 정의한 policy·exact binding 외에는 Git 또는 외부 시스템이 이미 소유하는 사실을 중복하지 않는다. 새 metadata가 필요하면 해당 artifact contract와 이 공통 형식 계약을 먼저 개정하고 template과 validator를 함께 갱신한다.

## Artifact별 문서 schema 소유권

| Artifact | Schema 소유 문서 |
| --- | --- |
| Line, DQC, Integration manifest | [Line 검증·통합·Delivery 계약](line-delivery.md) |
| Discovery | [Discovery 계약](discovery.md) |
| REQ, AC | [REQ와 AC 계약](requirements-and-criteria.md) |
| Micro-SPEC, IQC | [Micro-SPEC과 IQC 계약](micro-spec-and-iqc.md) |

Artifact별 최소 필수 field와 본문 section은 위 소유 문서에서만 정의한다.

## Template source layout

ProofLine schema version 1의 source-controlled template은 저장소 root의 다음 경로에 둔다.

```text
templates/schema-v1/
├── artifacts/
│   ├── line.md
│   ├── discovery.md
│   ├── requirement.md
│   ├── acceptance-criterion.md
│   ├── micro-spec.md
│   ├── iqc.md
│   ├── dqc.md
│   └── integration.md
└── derived/
    └── requirements.md
```

- `artifacts/`는 canonical artifact 생성 입력을 소유한다.
- `derived/requirements.md`는 사용자용 `docs/requirements.md` 생성 입력이다.
- Template은 ProofLine 저장소의 Git 추적 소스 자산이며 적용 프로젝트의 `.proofline/` canonical tree에 복사하지 않는다.
- Writer는 `proofline.yaml`의 `schema_version`과 일치하는 template bundle만 사용한다.
- Template은 artifact schema의 source of truth가 아니다. 이 문서와 artifact별 contract가 template보다 우선한다.
- Template을 변경할 때는 대응 artifact의 frontmatter field, H1·H2 구조, placeholder 및 lifecycle vocabulary를 함께 검증한다. Frontmatter-only integration template은 `INTEGRATION_ID`, `LINE_ID`, `MAIN_PARENT`, `LINE_HEAD` variable을 실제 값으로 모두 치환하며 governance placeholder나 Markdown 본문을 갖지 않는다.

## Placeholder 문법과 완결성 gate

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

### Template variable과 governance placeholder

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

### 상태별 허용 규칙

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
