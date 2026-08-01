# ProofLine

ProofLine은 프로젝트의 Discovery, Requirement, Acceptance Criterion, Micro-SPEC, IQC 및 DQC artifact를 검증하는 CLI입니다.

## 요구 사항

- Python 3.11 이상
- [`uv`](https://docs.astral.sh/uv/) 설치
- GitHub Release asset을 내려받을 수 있는 `curl`
- SHA-256 검증을 위한 `sha256sum`

## 설치

ProofLine은 PyPI가 아니라 [GitHub Releases](https://github.com/genichin/proofline/releases)에서 공식 wheel을 배포합니다. 현재 권장 stable version은 `v0.2.1`입니다.

다음 명령은 임시 디렉터리에 wheel과 checksum을 내려받고, checksum이 일치할 때만 user-level `uv tool` environment에 설치합니다.

```bash
RELEASE_VERSION="0.2.1"
RELEASE_DIR="$(mktemp -d)"
BASE_URL="https://github.com/genichin/proofline/releases/download/v${RELEASE_VERSION}"

curl -fsSL \
  "${BASE_URL}/proofline-${RELEASE_VERSION}-py3-none-any.whl" \
  -o "${RELEASE_DIR}/proofline-${RELEASE_VERSION}-py3-none-any.whl"

curl -fsSL \
  "${BASE_URL}/SHA256SUMS" \
  -o "${RELEASE_DIR}/SHA256SUMS"

(
  cd "${RELEASE_DIR}"
  sha256sum --check --strict SHA256SUMS
)

uv tool install \
  "${RELEASE_DIR}/proofline-${RELEASE_VERSION}-py3-none-any.whl"
```

기존 ProofLine tool installation이 있으면 `uv tool install`이 자동으로 덮어쓰지 않습니다. 기존 `v0.1.0` 또는 `v0.2.0`에서 `v0.2.1`로 전환하려면 checksum 검증이 성공한 뒤 마지막 명령에 `--force --no-config`를 명시합니다.

```bash
uv tool install --force --no-config \
  "${RELEASE_DIR}/proofline-${RELEASE_VERSION}-py3-none-any.whl"
```

> `v0.2.0`에는 uv-tool ownership 판정 결함이 있으므로 `v0.2.1` 이상을 사용하세요.

## 설치 확인

```bash
proofline --version
proofline update --check
```

정상적인 v0.2.1 official wheel 설치는 다음과 같이 표시됩니다.

```text
proofline 0.2.1
current: 0.2.1
target: 0.2.1
provenance: archive
status: already-current
```

`proofline` 명령을 찾지 못하면 `uv tool dir --bin`으로 executable 경로를 확인하고 해당 경로가 `PATH`에 포함됐는지 확인하세요.

## 업데이트

v0.2.1 이상에서는 다음 명령으로 최신 stable official wheel을 확인하거나 설치할 수 있습니다.

```bash
proofline update --check
proofline update
proofline update --version 0.2.1
```

Source checkout 기반 설치는 자동으로 official wheel로 바뀌지 않습니다. 명시적으로 전환하려는 경우에만 다음을 실행합니다.

```bash
proofline update --adopt-official
```

Update는 user-level ProofLine `uv tool` environment만 변경하며 application project의 `.venv`, `pyproject.toml`, lockfile, Git state 또는 `.proofline/` artifact를 변경하지 않습니다.

## 기본 사용법

현재 project의 ProofLine artifact를 검증합니다.

```bash
proofline validate
```

새 Line 생성을 미리 확인한 뒤 생성합니다.

```bash
proofline line init line-NNNN --title "TITLE" --dry-run
proofline line init line-NNNN --title "TITLE"
```

자세한 tool environment 운영 계약은 [`docs/operations/proofline-tool-environment.md`](docs/operations/proofline-tool-environment.md)를 참고하세요.
