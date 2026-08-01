---
name: proofline-tool-environment
description: Use when installing or verifying one shared user-level ProofLine uv tool environment without modifying an application project's dependencies or virtual environment.
version: 1.0.0
author: ProofLine
license: MIT
metadata:
  hermes:
    tags: [proofline, uv, virtual-environment, governance]
    related_skills: []
---

# ProofLine Shared Tool Environment

## Overview

Main과 모든 Line worktree 및 모든 적용 project의 ProofLine governance command는 OS user가 소유하는 하나의 공용 `uv tool` environment와 `proofline` executable을 사용한다. Application `.venv`에 ProofLine을 설치하지 않고 worktree마다 ProofLine 전용 `.venv`를 생성하지 않는다.

현재 공식 release channel이 없으므로 사용자가 명시적으로 선택한 clean ProofLine source checkout을 non-editable 방식으로 설치한다. 이 skill은 install과 현재 installation 검증만 담당한다. `update`, version pinning과 rollback 정책은 Issue #9의 후속 Discovery 범위다.

## When to Use

- 한 OS user의 모든 ProofLine 적용 project가 공유할 tool environment를 최초 설치할 때
- 공용 `proofline` executable과 module provenance를 검증할 때
- Application `.venv`와 ProofLine governance environment가 분리됐는지 점검할 때

다음에는 사용하지 않는다.

- ProofLine update, rollback, release publication 또는 version pinning을 수행할 때
- Application project의 source build·test dependency를 설치할 때
- Worktree별 ProofLine environment를 만들 때

## Preconditions

1. `uv`가 설치되어 있는지 `command -v uv`로 확인한다.
2. 사용자가 선택한 `<proofline-checkout>` absolute path를 확보한다. 경로를 추측하지 않는다.
3. Source checkout의 branch, commit과 `git status --porcelain`을 기록하고 uncommitted source를 설치하지 않는다.
4. 적용 project의 `pyproject.toml`, lockfile와 `.venv` 상태를 확인한다.
5. Credential, token과 connection string 값을 출력하거나 보존하지 않는다. 필요한 값은 `[REDACTED]`로 표시한다.

## Bootstrap

초기 설치는 적용 project가 아니라 별도로 선택한 ProofLine checkout을 source로 사용한다.

```bash
uv tool install <proofline-checkout>
```

이 명령은 non-editable installation이다. `--editable` option을 사용하지 않는다. 기존 installation을 자동 덮어쓰거나 삭제하지 않는다. 이미 설치되어 있거나 source/version이 다르면 중단하고 현재 state를 보고한다.

ProofLine CLI는 application dependency를 설치하지 않는다. Bootstrap 중 application `pyproject.toml`, lockfile, `.venv`와 canonical `.proofline/` artifact를 변경하지 않는 no-mutation 경계를 유지한다.

## Verification

Tool root와 executable directory를 확인한다.

```bash
uv tool dir
uv tool dir --bin
command -v proofline
proofline --help
```

기본 user-level 위치는 다음과 같다.

```text
~/.local/share/uv/tools/proofline/
~/.local/bin/proofline
```

`proofline` executable의 shebang과 `proofline.__file__` provenance가 application `.venv`나 Line worktree가 아니라 `uv tool dir` 아래 installation을 가리키는지 확인한다.

적용 project root에서 공용 executable을 검증한다.

```bash
proofline validate
```

명령 전후에 application의 다음 state가 같아야 한다.

```text
pyproject.toml bytes
lockfile bytes
application .venv 존재 여부와 내용
canonical .proofline artifact bytes
Git working tree status
```

변화가 있으면 성공으로 보고하지 않는다.

## Command Ownership

- Main과 모든 Line worktree 및 적용 project의 governance: 공용 `proofline ...`
- 구현 대상 project의 source build·test: 해당 project가 정의한 command와 environment
- ProofLine update·rollback·version compatibility: Issue #9와 후속 승인 Line

공용 tool environment는 모든 적용 project가 동일한 installed ProofLine version을 사용한다. Project별 environment에 별도 ProofLine을 설치해 우회하지 않는다.

## Failure and Recovery

- Install 실패 시 application project나 canonical artifact를 수정하지 않는다.
- 기존 정상 tool environment를 자동 삭제하지 않는다.
- `--force`, `--editable`, 임의 latest source 또는 remote main을 자동 선택하지 않는다.
- Version 변경, update와 rollback은 이 workflow에서 수행하지 않고 Issue #9로 보낸다.

## Verification Checklist

- [ ] Source checkout path와 exact commit을 명시적으로 확인했다.
- [ ] Source checkout이 clean하다.
- [ ] Non-editable `uv tool install <proofline-checkout>`을 사용했다.
- [ ] Executable과 module provenance가 `uv tool` environment를 가리킨다.
- [ ] Checkout 밖 적용 project에서 `proofline validate`가 통과한다.
- [ ] Application `pyproject.toml`, lockfile와 `.venv`가 변하지 않았다.
- [ ] Worktree마다 ProofLine 전용 `.venv`를 만들지 않았다.
- [ ] Update와 rollback을 수행하지 않았다.
