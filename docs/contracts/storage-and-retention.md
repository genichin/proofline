# 저장·파생 문서·보존 계약

이 문서는 canonical artifact와 파생 산출물의 경계, 보존·폐기 규칙 및 프로젝트 설정을 정의한다. Canonical 경로는 [산출물 디렉터리 구조](../artifact-layout.md)가 소유한다.

## User-level harness resource

ProofLine이 관리하는 manifest, contracts, templates와 skills는 user home의 `~/.proofline/`에 저장한다. 이 resource는 project lifecycle artifact가 아니며 project `.proofline/` 아래에 복제하지 않는다.

Project `.proofline/`은 Lines와 criteria의 canonical artifact root로만 사용한다. `proofline init`은 user-level harness만 초기화하며 current project의 `.proofline/`, `proofline.yaml` 또는 Git state를 변경하지 않는다.

`proofline update`는 selected checksum-verified official wheel의 CLI/package와 해당 wheel에 포함된 user-level harness resource를 같은 target version으로 함께 갱신한다. Existing `~/.proofline/`은 current manifest의 exact managed path와 checksum에 일치해야 하며, user modification, unexpected entry, malformed manifest 또는 symlink가 있으면 project와 user resource를 변경하지 않고 실패한다. `proofline update --check`는 package와 harness 상태를 읽기만 하고 staging, download, install 또는 filesystem mutation을 수행하지 않는다.

## 정본과 파생 산출물

[산출물 디렉터리 구조](../artifact-layout.md)가 canonical path로 정의한 `.proofline/` 산출물은 Git으로 추적하는 정본이다.

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

## 보존 및 폐기 규칙

ProofLine의 canonical artifact는 현재 tree에서 마지막 canonical 상태를, Git history에서 이전 revision과 검증 attempt를 보존한다. Terminal artifact를 별도 archive directory로 이동하거나 lifecycle cleanup을 이유로 삭제하지 않는다.

```text
현재 canonical tree = 각 artifact의 마지막 canonical 상태
Git history          = 이전 specification revision과 verification attempt
```

### Canonical terminal state와 검증 결과 보존

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

### Draft cleanup과 candidate 철회

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

### IQC/DQC evidence 보존

IQC와 DQC Markdown에 기록한 검증 summary는 canonical artifact로 무기한 보존한다. 최소한 실제 실행한 command 또는 check, exit code나 판정 결과, 결과 요약, AC별 판정 및 evidence reference를 현재 파일 또는 Git history에서 확인할 수 있어야 한다.

대용량 원시 log, binary capture, 장치 trace 및 CI artifact는 canonical tree에 복사하지 않는다. 외부 evidence에는 다음 규칙을 적용한다.

- IQC 또는 DQC 판정 시점에는 evidence에 접근할 수 있어야 한다.
- IQC 또는 DQC에 안정적인 repository path나 external reference를 기록한다.
- 장기 감사가 필요한 evidence에는 가능한 경우 digest를 함께 기록한다.
- 원시 evidence의 실제 보존 기간은 적용 프로젝트 또는 외부 evidence store의 정책이 소유한다.
- 외부 evidence의 사후 만료만으로 과거 IQC/DQC result를 자동 변경하지 않는다. 다만 evidence를 다시 확인할 수 없다면 그 evidence에 의존하는 새로운 재검증 또는 새로운 verification claim은 `blocked`로 판정한다.

### 파생 산출물과 실행 부산물

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

### 보안·법적 삭제 예외

Credential, token, 개인정보, 법적으로 삭제해야 하는 데이터 또는 악성 binary가 canonical artifact나 Git history에 포함된 경우 일반 retention 규칙을 적용하지 않는다. 이를 repository security incident로 처리한다.

```text
working tree에서 민감 정보 제거
→ credential 폐기·교체
→ 필요 시 Git history 정화
→ mirror, cache 및 CI artifact 정리
→ 프로젝트의 incident 절차에 따라 기록
```

Artifact를 `withdrawn` 또는 Line을 `cancelled`로 전환하는 것만으로 민감 정보 제거를 완료한 것으로 간주하지 않는다. 보안·법적 삭제를 위해 history를 정화한 뒤에도 가능한 범위에서 비민감한 lifecycle 사실과 identity를 보존한다.

## 프로젝트 설정

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
