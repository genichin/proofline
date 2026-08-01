---
name: proofline-tool-environment
description: Use when installing or verifying one shared user-level ProofLine uv tool environment without modifying an application project's dependencies or virtual environment.
version: 1.3.0
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

공식 stable GitHub Release의 checksum-verified wheel을 non-editable 방식으로 설치·명시적으로 update한다. 사용자가 명시한 clean source checkout 설치도 개발·복구 경로로 유지한다.

## When to Use

- 한 OS user의 모든 ProofLine 적용 project가 공유할 tool environment를 최초 설치할 때
- 공용 `proofline` executable과 module provenance를 검증할 때
- Application `.venv`와 ProofLine governance environment가 분리됐는지 점검할 때

다음에는 사용하지 않는다.

- Background update, rollback 또는 release publication을 수행할 때
- Application project의 source build·test dependency를 설치할 때
- Worktree별 ProofLine environment를 만들 때

## Preconditions

1. `uv`가 설치되어 있는지 `command -v uv`로 확인한다.
2. Official release install이면 selected tag, wheel filename과 `SHA256SUMS` identity를 확인한다.
3. Source checkout install이면 absolute path, branch, commit과 `git status --porcelain`을 확인하고 uncommitted source를 설치하지 않는다.
4. 적용 project의 `pyproject.toml`, lockfile와 `.venv` 상태를 확인한다.
5. 기존 `proofline` tool installation이 있으면 자동 overwrite하지 않고 중단한다.
6. Credential, token과 connection string 값을 출력하거나 보존하지 않는다. 필요한 값은 `[REDACTED]`로 표시한다.

## Bootstrap

Official v0.2.2 fresh install의 기본 사용자 경로는 exact immutable tag의 installer다. Script가 wheel과 `SHA256SUMS`를 temporary directory에서 strict 검증한 뒤 non-editable user-level uv tool로 설치한다.

```bash
curl -fsSL https://raw.githubusercontent.com/genichin/proofline/v0.2.2/install.sh | sh
```

Existing installation을 명시적으로 verified wheel로 교체할 때만 force를 전달한다.

```bash
curl -fsSL https://raw.githubusercontent.com/genichin/proofline/v0.2.2/install.sh | sh -s -- --force
```

다음 v0.1.0 절차는 installer를 사용하지 않는 manual strict verification 예시로 유지한다.

Official `v0.1.0` fresh install은 적용 project 밖의 unique temporary directory에서 asset bytes를 먼저 검증한다.

```bash
RELEASE_DIR="$(mktemp -d)"
gh release download v0.1.0 \
  --repo genichin/proofline \
  --dir "$RELEASE_DIR" \
  --pattern 'proofline-0.1.0-py3-none-any.whl' \
  --pattern 'SHA256SUMS'
(cd "$RELEASE_DIR" && sha256sum --check --strict SHA256SUMS)
uv tool install "$RELEASE_DIR/proofline-0.1.0-py3-none-any.whl"
```

개발·복구 목적으로 사용자가 명시한 clean source checkout을 설치할 때만 `uv tool install <proofline-checkout>`을 사용한다. 두 경로 모두 non-editable이며 `--editable` option을 사용하지 않는다. 기존 installation을 자동 덮어쓰거나 삭제하지 않는다.

ProofLine CLI는 application dependency를 설치하지 않는다. Bootstrap 중 application `pyproject.toml`, lockfile, `.venv`와 canonical `.proofline/` artifact를 변경하지 않는 no-mutation 경계를 유지한다.

## Verification

Tool root와 executable directory를 확인한다.

```bash
uv tool dir
uv tool dir --bin
command -v proofline
proofline --help
proofline --version
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
- ProofLine explicit update: 공용 `proofline update ...`
- Rollback·schema compatibility: 별도 승인 Line

공용 tool environment는 모든 적용 project가 동일한 installed ProofLine version을 사용한다. Project별 environment에 별도 ProofLine을 설치해 우회하지 않는다.

## Explicit Update

GitHub Issue #9와 Line 0008에서 승인한 explicit maintenance boundary다.

Official `v0.2.1` 이상에서는 다음을 사용한다. 현재 stable target은 v0.2.2다.

```bash
proofline update --check
proofline update
proofline update --version 0.2.1
```

Source checkout provenance는 기본 update에서 보존된다. Official wheel 전환 의도가 명시된 경우에만 실행한다.

```bash
proofline update --adopt-official
```

`v0.1.0`에는 update command가 없고 `v0.2.0` updater에는 uv-tool ownership defect가 있으므로 최초 한 번은 `v0.2.1` wheel·`SHA256SUMS`를 내려받아 strict 검증한 뒤 `uv tool install --force <verified-v0.2.1-wheel>`로 전환한다. Existing `v0.2.0` release bytes는 overwrite하지 않는다.

## Failure and Recovery

- Install 실패 시 application project나 canonical artifact를 수정하지 않는다.
- 기존 정상 tool environment를 자동 삭제하지 않는다.
- Bootstrap에서는 `--force`·`--editable`을 사용하지 않는다. Explicit updater만 verified local wheel에 `uv tool install --force`를 사용하며 mutable remote main 또는 checksum 미검증 asset을 선택하지 않는다.
- Update 실패를 success로 보고하지 않으며 자동 rollback은 수행하지 않는다.

## Verification Checklist

- [ ] Official release이면 tag·wheel·`SHA256SUMS`를 확인했고 checksum 검증이 통과했다.
- [ ] Source checkout이면 path·exact commit을 확인했고 checkout이 clean하다.
- [ ] Non-editable `uv tool install`을 사용했다.
- [ ] Executable과 module provenance가 `uv tool` environment를 가리킨다.
- [ ] `proofline --version`이 selected version과 일치한다.
- [ ] Checkout 밖 적용 project에서 `proofline validate`가 통과한다.
- [ ] Application `pyproject.toml`, lockfile와 `.venv`가 변하지 않았다.
- [ ] Worktree마다 ProofLine 전용 `.venv`를 만들지 않았다.
- [ ] Update는 사용자가 명시적으로 요청했고 source 전환에는 `--adopt-official`을 사용했다.
