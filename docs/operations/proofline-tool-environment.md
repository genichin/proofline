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

Official v0.2.2 fresh install의 기본 UX는 exact immutable tag installer 한 줄이다.

```bash
curl -fsSL https://raw.githubusercontent.com/genichin/proofline/v0.2.2/install.sh | sh
```

Installer는 temporary directory에서 v0.2.2 wheel·`SHA256SUMS`를 내려받아 strict 검증하고 non-editable user-level uv tool로 설치한다. Existing installation은 자동 overwrite하지 않으며 명시적으로 교체할 때만 다음을 사용한다.

```bash
curl -fsSL https://raw.githubusercontent.com/genichin/proofline/v0.2.2/install.sh | sh -s -- --force
```

Installer는 tagged source에 포함되고 GitHub Release upload asset allowlist는 updater 호환성을 위해 wheel·`SHA256SUMS` 두 개를 유지한다.

### Manual strict verification

다음 v0.1.0 절차는 installer를 사용하지 않는 기존 manual workflow 예시다. 실제 설치 version에는 selected stable tag와 wheel filename을 사용한다.

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
proofline line init --title "TITLE" --dry-run
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

## Explicit update와 v0.1.0·v0.2.0 전환

Official `v0.2.1` 이상에서는 다음 명령을 사용한다.

```bash
proofline update --check
proofline update
proofline update --version 0.2.1
```

Source checkout installation은 기본 update에서 `adoption-required`로 중단된다. Official wheel로 명시적으로 전환할 때만 `proofline update --adopt-official`을 사용한다.

`v0.1.0` executable에는 update command가 없고 `v0.2.0` updater에는 uv-tool ownership defect가 있다. 두 version에서는 `v0.2.1` wheel·`SHA256SUMS`를 직접 download·strict verify한 뒤 한 번만 `uv tool install --force <verified-v0.2.1-wheel>`를 실행한다. 이후에는 `proofline update`를 사용한다. Published `v0.2.0` objects는 보존하며 사용을 권장하지 않는다.

Update는 application cwd를 install cwd로 사용하지 않고 `.venv`, `pyproject.toml`, lockfile, Git state와 `.proofline/`을 변경하지 않는다. Background update, automatic rollback과 PyPI는 이 계약에 포함하지 않는다.

## v0.6.0 corrective installer 경계

Public `v0.6.0` executable의 `proofline update`는 `v0.6.1`, `v0.6.2` 또는 다음 corrective release로 전환하는 지원 경로가 아니다. `v0.6.1`과 `v0.6.2`도 corrective target으로 재지정하지 않는다. Future corrective exact tag의 publication과 unauthenticated read-back 전까지 installer의 corrective option은 fail-closed placeholder다.

Publication 후에는 `<CORRECTIVE_EXACT_TAG>`를 published immutable tag로 치환한 다음 경로만 사용한다.

```bash
curl -fsSL https://raw.githubusercontent.com/genichin/proofline/<CORRECTIVE_EXACT_TAG>/install.sh | sh -s -- --corrective-transition
```

Windows에서는 같은 tagged `install.ps1`을 native temporary file로 내려받아 `-CorrectiveTransition`으로 실행한다. Fresh install과 package-only `--force`/`-Force`는 이 transition과 별도 interface다.

Corrective installer는 target package를 격리 staging한 뒤 target-owned internal module에 transaction을 위임한다. Module은 checksum-bound official `v0.6.0` archive installation 및 exact legacy HOME을 mutation 전에 확인하고 deterministic no-overwrite `~/.proofline.backup-v0.6.0`을 만든 뒤 target-owned HOME으로 수렴시킨다. Backup collision이나 legacy HOME drift, symlink, unexpected entry/path type은 package install 전에 거부한다. Install, HOME commit 또는 read-back 실패에서는 predecessor package/HOME rollback과 coherence verification을 수행하며 application cwd의 dependency, environment, `.proofline/`과 Git state는 변경하지 않는다.
