# 문서 형식과 완결성 계약

이 문서는 Line, Discovery, REQ와 AC의 공통 YAML frontmatter, Markdown, metadata와 placeholder 규칙을 정의한다. 경로와 identity는 [산출물 디렉터리 구조](../artifact-layout.md)를 따른다.

`line-NNNN/evidence/*.md`는 UTF-8 regular Markdown이어야 하지만 canonical artifact가 아니므로 이 문서의 frontmatter, heading, metadata와 placeholder 규칙을 적용하지 않는다. Validator는 해당 파일을 UTF-8로 안전하게 읽을 수 있는지만 확인하고 canonical artifact로 승격하거나 lifecycle 의미를 추론하지 않는다.

## 공통 형식

Canonical artifact는 UTF-8 Markdown과 YAML frontmatter를 사용한다.

```markdown
---
# machine-readable metadata
---

# Human-readable content
```

- Frontmatter는 첫 줄의 `---`로 시작하고 `---`로 종료한다.
- 모든 artifact의 `id`는 파일명과 일치해야 한다.
- Line 내부 artifact 번호는 상위 `line-<NNNN>/` 번호와 일치해야 한다.
- Status-bearing artifact의 status와 reference는 각 artifact contract가 정의한 값과 canonical artifact만 사용한다. Line은 status를 갖지 않는다.
- `created_at`, `updated_at`, `author`, `owner`, `branch` 등 Git 또는 외부 시스템이 소유하는 metadata는 추가하지 않는다.
- 정의되지 않은 frontmatter key와 `x-*` extension은 금지한다.

## Markdown 규칙

Line은 frontmatter-only이다. Discovery, REQ와 AC는 정확히 하나의 H1을 가지며 각 artifact contract가 정의한 필수 H2를 이름과 순서대로 한 번씩 사용한다. H3 이하 heading, list와 checklist는 소유 H2 안에서 사용할 수 있다.

Draft artifact도 H1과 필수 H2를 모두 가져야 한다. 미완성 section은 비우지 않고 허용된 governance placeholder를 사용한다. `confirmed`, `approved`, `active` 또는 `retired`로 전환하기 전에는 필수 section에 실질적인 내용이 있고 placeholder가 없어야 한다. `withdrawn` artifact는 철회 시점의 미완성 내용을 보존할 수 있다.

## Authored-content language guidance

사람이 작성하는 본문은 원칙적으로 한국어로 작성한다. H1 제목·구조적 heading, ID, status, YAML key, CLI, 코드, path, 고유 기술 용어 및 영어가 의미를 더 정확하게 전달하는 내용은 영어로 유지할 수 있다.

이 원칙은 authoring guidance이며 자연어 validation gate가 아니다. Validator, QC와 CI는 artifact의 한국어 여부만으로 구조적으로 유효한 artifact를 실패시키지 않는다. Source template·contract·skill과 wheel·initialized HOME resource의 지침 및 bytes 정합은 검사할 수 있지만 개별 artifact 본문의 언어를 PASS/FAIL 조건으로 사용하지 않는다.

## Schema 소유권

| Artifact | Schema 소유 문서 |
| --- | --- |
| Line | [Line identity 계약](line-delivery.md) |
| Discovery | [Discovery 계약](discovery.md) |
| REQ, AC | [REQ와 AC 계약](requirements-and-criteria.md) |

Source-controlled template layout은 현재 canonical 범위만 설명한다.

```text
templates/schema-v1/
├── artifacts/
│   ├── line.md
│   ├── discovery.md
│   ├── requirement.md
│   └── acceptance-criterion.md
└── derived/
    └── requirements.md
```

Template은 생성 입력이며 schema의 source of truth가 아니다. 이전 version에서 설치되거나 프로젝트에 남은 범위 밖 template과 artifact는 opaque retained data이고 현재 contract가 생성·해석하지 않는다.

## Placeholder

Template variable은 다음 문법을 사용한다.

```text
{{NAME}}
{{NAME: description}}
```

`NAME`은 대문자로 시작하는 `UPPER_SNAKE_CASE`이다. YAML scalar 전체가 variable이면 문자열로 quote한다. 생성된 canonical artifact에는 template variable이 남아 있으면 안 된다.

Canonical Markdown 본문에 남길 수 있는 governance placeholder는 다음 세 값뿐이다.

```text
{{TODO}}
{{UNKNOWN: description}}
{{NEEDS_EVIDENCE: description}}
```

Canonical frontmatter에는 placeholder를 사용할 수 없다. Governance placeholder는 Discovery, REQ 또는 AC가 `draft`일 때만 허용하며 `confirmed`, `approved`, `active` 또는 `retired` 전환 전에 모두 제거한다. `withdrawn` Discovery와 REQ에는 기존 placeholder가 남을 수 있다.

Validator는 placeholder 문법과 이름, frontmatter 사용 여부, 상태별 완결성 및 Discovery Open Question 구조를 검사한다. 현재 tree의 구조·내용 검사는 Git 이력이나 구현·배포 chronology 보증이 아니다.
