# 공용 ProofLine Tool Environment

## 목적

ProofLine governance tool을 application dependency와 분리한다. Main, 모든 Line worktree와 모든 적용 project는 OS user가 소유하는 하나의 공용 `proofline` executable을 사용한다.

```text
~/.local/share/uv/tools/proofline/   # 공용 tool environment
~/.local/bin/proofline               # 공용 executable
project-a/.venv/                     # project-owned environment
project-b/.venv/                     # project-owned environment
```

Worktree마다 ProofLine 전용 `.venv`를 생성하지 않는다. Application project에도 ProofLine package를 dependency로 추가하지 않는다.

## 현재 설치 계약

공식 stable channel은 GitHub Release `v0.1.0`의 checksum-verified wheel이다. Fresh installation은 wheel과 `SHA256SUMS`를 unique temporary directory에 내려받아 검증한 뒤 non-editable 방식으로 수행한다.

```bash
gh release download v0.1.0 \
  --repo genichin/proofline \
  --dir "$RELEASE_DIR" \
  --pattern 'proofline-0.1.0-py3-none-any.whl' \
  --pattern 'SHA256SUMS'

(cd "$RELEASE_DIR" && sha256sum --check --strict SHA256SUMS)
uv tool install "$RELEASE_DIR/proofline-0.1.0-py3-none-any.whl"
```

`RELEASE_DIR`은 `mktemp -d`로 새로 만들며 기존 evidence를 삭제하지 않는다. `--editable`, mutable remote main과 자동 overwrite를 사용하지 않는다. 기존 installation이 있으면 중단하고 Issue #9의 update·rollback 계약을 따른다.

개발·복구 목적으로 사용자가 명시한 clean source checkout을 설치하는 기존 경로도 유지한다. 이 경우 absolute path와 exact Git commit을 확인한 뒤 `uv tool install <proofline-checkout>`을 사용한다.

Tool root와 executable directory는 `uv`가 소유한다.

```bash
uv tool dir
uv tool dir --bin
```

현재 기본 위치는 다음과 같다.

```text
~/.local/share/uv/tools/proofline/
~/.local/bin/proofline
```

## 실행 계약

Main, 모든 Line worktree와 적용 project의 ProofLine governance는 공용 `proofline`을 사용한다.

```bash
proofline validate
proofline line init line-NNNN --title "TITLE" --dry-run
```

구현 대상 project의 source build·test는 해당 project가 정의한 command와 environment를 사용한다. Project build environment를 ProofLine governance environment로 취급하지 않는다.

## 설치 검증

1. `command -v proofline`이 `uv tool dir --bin` 아래 executable을 가리키는지 확인한다.
2. Executable shebang과 installed `proofline.__file__`이 `uv tool dir` 아래인지 확인한다.
3. `proofline --version`이 selected release version을 출력하는지 확인한다.
4. ProofLine source checkout 밖의 적용 project에서 `proofline validate`를 실행한다.
5. 실행 전후 application `pyproject.toml`, lockfile, `.venv`, canonical `.proofline/` bytes와 Git status가 같은지 확인한다.
6. Worktree에 ProofLine 전용 `.venv`가 생성되지 않았는지 확인한다.

공용 설치와 validation은 application dependency를 변경하지 않는 no-mutation 작업이어야 한다.

## Explicit update와 v0.1.0·v0.2.0 migration

Official `v0.2.1` 이상에서는 다음 명령을 사용한다.

```bash
proofline update --check
proofline update
proofline update --version 0.2.1
```

Source checkout installation은 기본 update에서 `adoption-required`로 중단된다. Official wheel로 명시적으로 전환할 때만 `proofline update --adopt-official`을 사용한다.

`v0.1.0` executable에는 update command가 없고 `v0.2.0` updater에는 uv-tool ownership defect가 있다. 두 version에서는 `v0.2.1` wheel·`SHA256SUMS`를 직접 download·strict verify한 뒤 한 번만 `uv tool install --force <verified-v0.2.1-wheel>`를 실행한다. 이후에는 `proofline update`를 사용한다. Published `v0.2.0` objects는 보존하며 사용을 권장하지 않는다.

Update는 application cwd를 install cwd로 사용하지 않고 `.venv`, `pyproject.toml`, lockfile, Git state와 `.proofline/`을 변경하지 않는다. Background update, automatic rollback과 PyPI는 이 계약에 포함하지 않는다.
