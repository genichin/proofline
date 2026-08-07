# 저장·파생 문서·보존 계약

이 문서는 canonical artifact와 파생 산출물의 경계, 보존·폐기 및 프로젝트 설정을 정의한다. Canonical 경로는 [산출물 디렉터리 구조](../artifact-layout.md)가 소유한다.

## User-level harness

ProofLine의 manifest, contracts, operations, templates와 skills는 `~/.proofline/`에 저장한다. `docs/operations/*.md`는 wheel과 HOME에 exact bytes로 대응하며 `manifest.yaml`이 path와 SHA-256을 기록한다. 이 resource는 project lifecycle artifact가 아니며 project `.proofline/`에 복제하지 않는다.

Project `.proofline/`은 Line, Discovery, REQ와 AC의 canonical root이다. `proofline init`은 user-level harness만 초기화하며 current project를 변경하지 않는다. Project scaffold는 `proofline.yaml`, `.proofline/lines/.gitkeep`과 `.proofline/criteria/.gitkeep`만 초기화하며 Git commit, branch 또는 remote를 조작하지 않는다.

## 정본과 파생 산출물

`.proofline/line-identities.json`은 Line allocation source of truth이다. ProofLine은 별도의 자동 index를 만들지 않으며 writer와 validator는 deterministic canonical path를 직접 탐색한다.

현재 사용자용 파생 문서는 `docs/requirements.md` 하나이다. 지정한 source Git commit에서 `active`인 AC를 ID 순서로 펼쳐 ID, 제목, Criterion과 Verification을 포함한다.

- 자동 생성된 비정본 문서임을 머리말에 명시한다.
- 생성 기준 exact source commit을 기록한다.
- 기존 파일이 있으면 덮어쓰지 않고 충돌로 보고한다.
- Canonical 상태, validation 또는 approval의 입력으로 사용하지 않는다.
- 내용이 충돌하면 `.proofline/criteria/ac-*.md`를 우선한다.

Canonical tree에는 임시 파일, cache, 원시 대화·debug log, credential, token과 프로젝트 외 참고 자료를 저장하지 않는다.

## 보존과 폐기

Line, Discovery, REQ와 AC는 현재 tree에 canonical 내용을 보존한다. Status-bearing artifact를 archive로 이동하거나 lifecycle cleanup을 이유로 삭제하지 않는다.

```text
Discovery.status: withdrawn
REQ.status: withdrawn
AC.status: retired
```

- 할당된 Line directory와 terminal Discovery, REQ 및 AC를 삭제하거나 이동하지 않는다.
- 별도 `.proofline/archive/`를 만들지 않는다.
- 승인된 적 없는 신규 draft나 잘못 생성되어 어디에서도 참조하지 않는 artifact만 dangling reference가 생기지 않는 범위에서 제거할 수 있다.
- 변경 논의를 중단할 때는 필요한 Discovery와 REQ를 `withdrawn`으로 전환한다. Line에는 상태 전환이 없다.
- 한 번 사용한 canonical identity는 다른 의미로 재사용하지 않는다.

`docs/requirements.md`, 임시 파일, cache, local log와 download artifact는 필요하면 재생성하거나 삭제할 수 있다.

## Opaque retained data

과거 ProofLine version이 `.proofline/`에 저장한 현재 canonical 범위 밖 artifact와 directory는 opaque retained data로 취급한다.

- 기존 데이터와 Git history를 보존할 수 있고 이를 제거하도록 요구하지 않는다.
- Writer, validator, contract와 skill은 이를 새로 만들거나 현재 lifecycle 입력으로 해석하지 않는다.
- 현재 schema로 소급 변환하거나 canonical path로 이동·병합하지 않는다.
- 보존 자체는 과거 판정이나 구현·배포 완료를 현재 ProofLine이 보증한다는 뜻이 아니다.
- 기존 Line의 `execution_status`와 `implementation_history`는 opaque retained metadata이며 현재 상태나 완료 증거로 해석하지 않는다.

이 compatibility 경계에는 migration operation이 없다. Credential, 개인정보, 악성 binary 또는 법적으로 삭제해야 하는 데이터는 예외이며 프로젝트의 security incident와 history 정화 절차를 따른다.

## 프로젝트 설정

Repository root의 설정은 다음과 같다.

```yaml
schema_version: 1
artifact_root: .proofline
```

Schema version 1에서 `artifact_root`는 정확히 `.proofline`인 필수 상수다. 절대 경로, `..`, `.`, 빈 값, 다른 상대 경로와 symbolic link는 허용하지 않는다. Writer와 validator는 다른 root를 탐색·생성하거나 두 tree를 병합하지 않는다. 다른 root가 필요하면 별도 schema를 먼저 정의해야 한다.
