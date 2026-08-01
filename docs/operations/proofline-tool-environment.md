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

공식 release channel이 없는 동안 사용자가 명시적으로 선택한 clean ProofLine source checkout에서 non-editable installation을 수행한다.

```bash
uv tool install <proofline-checkout>
```

`<proofline-checkout>`은 추측하지 않고 absolute path와 exact Git commit을 확인한다. `--editable`, 임의 remote main과 자동 overwrite를 사용하지 않는다.

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
3. ProofLine source checkout 밖의 적용 project에서 `proofline validate`를 실행한다.
4. 실행 전후 application `pyproject.toml`, lockfile, `.venv`, canonical `.proofline/` bytes와 Git status가 같은지 확인한다.
5. Worktree에 ProofLine 전용 `.venv`가 생성되지 않았는지 확인한다.

공용 설치와 validation은 application dependency를 변경하지 않는 no-mutation 작업이어야 한다.

## Update 경계

이 계약은 최초 install과 current installation 검증만 정의한다. 다음 항목은 GitHub Issue #9의 별도 Discovery와 승인 Line에서 결정한다.

- `proofline update`
- Official release source
- Version compatibility와 pinning
- Rollback
- PyPI·Git tag·GitHub Release

현재 workflow는 `--force`, 자동 update, 자동 rollback 또는 latest source 추측을 수행하지 않는다.
