# ProofLine 템플릿

이 디렉터리는 ProofLine writer가 canonical artifact와 사용자용 파생 문서를 생성할 때 사용하는 Git 추적 소스 자산이다. 템플릿 자체는 `.proofline/` canonical artifact가 아니며 적용 프로젝트에 복사하지 않는다.

## 디렉터리

```text
templates/
└── schema-v1/
    ├── artifacts/
    │   ├── line.md
    │   ├── discovery.md
    │   ├── requirement.md
    │   └── acceptance-criterion.md
    └── derived/
        └── requirements.md
```

`schema-v1`은 `proofline.yaml`의 `schema_version: 1`에 대응한다.

## 사용 규칙

- `{{ARTIFACT_ID}}`, `{{TITLE}}` 같은 일반 template variable은 생성 과정에서 모두 실제 값으로 치환한다.
- YAML scalar 전체가 variable이면 항상 따옴표로 감싼다.
- 생성된 draft 본문에는 `{{TODO: ...}}`, `{{UNKNOWN: ...}}`, `{{NEEDS_EVIDENCE: ...}}`만 남길 수 있다.
- 생성 결과의 frontmatter에는 어떤 placeholder도 남기지 않는다.
- 새 REQ의 `criteria`는 `create`, `update`, `retire`, `satisfy` 네 목록을 생성 전에 확정하며 합집합에 최소 하나의 AC를 넣는다. Historical schema-v1 REQ의 세 목록 형태는 계속 유효하다.
- 필요하지 않은 선택 section과 예시 행은 생성할 때 제거한다. 필수 H1·H2 section은 제거하지 않는다.
- Line template은 stable `id`만 생성한다. Discovery, REQ와 AC의 상태 전환 권한은 각 contract가 소유한다.
- 생성 결과는 `docs/contracts/`의 해당 artifact contract와 validator를 통과해야 한다.
- 신규 canonical artifact는 Line, Discovery, Requirement, Acceptance Criterion 네 종류다.
- 기존 프로젝트의 canonical legacy 경로에 남은 Micro-SPEC, IQC, DQC, integration 및 legacy migration 파일은 opaque retained data이며 신규 생성하거나 템플릿으로 제공하지 않는다.

## 파생 요구사항 문서 렌더링

`derived/requirements.md`의 `{{ACTIVE_CRITERIA_SECTIONS}}`는 source commit에서 `status: active`인 AC를 ID 순으로 다음 형태로 펼친 전체 block으로 치환한다.

```markdown
## `ac-0001`: AC 제목

### Criterion

AC의 Criterion 내용

### Verification

AC의 Verification 내용
```

- `draft`와 `retired` AC는 포함하지 않는다.
- `{{SOURCE_COMMIT}}`에는 생성 기준 exact Git commit을 기록한다.
- 적용 프로젝트에 `docs/requirements.md`가 이미 있으면 덮어쓰지 않고 어떤 파일도 변경하지 않은 채 충돌로 보고한다.
- 생성 결과는 전체 재생성하며 직접 수정하지 않는다.

## 참고 프로젝트에서 반영한 작성 품질

참고용 스킬·하네스의 유용한 패턴 중 ProofLine contract와 충돌하지 않는 다음 항목을 반영했다.

- Discovery에서 사용자 문제, 확인된 근거, 범위·비범위, 위험·가정 및 구조화된 Open Question을 구분한다.
- AC는 관찰 가능한 성공 조건뿐 아니라 실패·경계 조건과 직접 검증 방법을 분명히 한다.

참고 프로젝트의 추가 metadata, 별도 lifecycle 권한, index, review checklist 및 중복 상태는 ProofLine schema에 포함하지 않았다.
