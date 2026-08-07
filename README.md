# ProofLine

ProofLine은 프로젝트의 Line, Discovery, Requirement 및 Acceptance Criterion artifact를 검증하는 CLI입니다.

## 요구 사항

- Python 3.11 이상
- [`uv`](https://docs.astral.sh/uv/) 설치
- POSIX 설치에서는 GitHub Release asset을 내려받을 수 있는 `curl`과 SHA-256 검증용 `sha256sum`
- Windows 설치에서는 native PowerShell 5.1 이상

## 설치

ProofLine은 PyPI가 아니라 [GitHub Releases](https://github.com/genichin/proofline/releases)에서 공식 wheel을 배포합니다. 이 source의 release candidate는 `v0.7.0`이며, 아래 exact-tag 설치는 해당 tag와 Release가 게시된 뒤 사용합니다.

다음 한 줄로 설치합니다. Versioned installer가 임시 디렉터리에서 wheel과 `SHA256SUMS`를 내려받아 strict 검증한 뒤 user-level `uv tool` environment에 설치합니다.

```bash
curl -fsSL https://raw.githubusercontent.com/genichin/proofline/v0.7.0/install.sh | sh
```

기존 ProofLine installation을 verified v0.7.0 wheel로 명시적으로 교체하려면 `--force`를 전달합니다. 이는 package-only 교체이며 아래 v0.6.0 corrective transition이 아닙니다.

```bash
curl -fsSL https://raw.githubusercontent.com/genichin/proofline/v0.7.0/install.sh | sh -s -- --force
```

Windows 11에서는 exact-tag installer를 native PowerShell의 temporary file로 내려받아 실행합니다.

```powershell
$InstallerPath = Join-Path ([System.IO.Path]::GetTempPath()) ("proofline-install-" + [guid]::NewGuid().ToString("N") + ".ps1")
try {
    Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/genichin/proofline/v0.7.0/install.ps1" -OutFile $InstallerPath
    & $InstallerPath
} finally {
    if (Test-Path -LiteralPath $InstallerPath) {
        Remove-Item -LiteralPath $InstallerPath -Force
    }
}
```

기존 installation을 명시적으로 교체할 때만 `-Force`를 전달합니다.

```powershell
$InstallerPath = Join-Path ([System.IO.Path]::GetTempPath()) ("proofline-install-" + [guid]::NewGuid().ToString("N") + ".ps1")
try {
    Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/genichin/proofline/v0.7.0/install.ps1" -OutFile $InstallerPath
    & $InstallerPath -Force
} finally {
    if (Test-Path -LiteralPath $InstallerPath) {
        Remove-Item -LiteralPath $InstallerPath -Force
    }
}
```

설치 후에는 user harness를 먼저 초기화하고, initialized harness의 update 상태를 확인한 다음 project를 검증합니다.

```powershell
proofline --version
proofline init --dry-run
proofline init
proofline update --check
proofline validate
```

Installer는 application project의 `.venv`, `pyproject.toml`, lockfile, Git state 또는 `.proofline/`을 변경하지 않습니다.

### v0.6.0 corrective transition

Public `v0.6.0` executable의 기존 `proofline update`는 `v0.7.0`으로 가는 지원 경로가 아닙니다. `v0.6.1`과 `v0.6.2`도 corrective transition target이 아닙니다. `v0.7.0` exact-tag installer의 `--corrective-transition` 또는 `-CorrectiveTransition`을 사용합니다.

Publication과 public read-back이 완료된 `v0.7.0` immutable exact tag의 다음 단일 명령만 사용합니다.

```bash
curl -fsSL https://raw.githubusercontent.com/genichin/proofline/v0.7.0/install.sh | sh -s -- --corrective-transition
```

```powershell
$InstallerPath = Join-Path ([System.IO.Path]::GetTempPath()) ("proofline-transition-" + [guid]::NewGuid().ToString("N") + ".ps1")
try {
    Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/genichin/proofline/v0.7.0/install.ps1" -OutFile $InstallerPath
    & $InstallerPath -CorrectiveTransition
} finally {
    if (Test-Path -LiteralPath $InstallerPath) { Remove-Item -LiteralPath $InstallerPath -Force }
}
```

이 경로는 target wheel을 격리 staging environment에 설치하고 target package의 `proofline.installer_transition`을 실행합니다. Core는 official `v0.6.0` archive identity와 exact legacy `~/.proofline` inventory, hash, path type을 먼저 검증하고 기존 backup이 없을 때만 `~/.proofline.backup-v0.6.0`을 만듭니다. Backup collision, modified·symlink·unexpected HOME, package/HOME commit 또는 post-verification failure는 overwrite 없이 중단하거나 predecessor package와 HOME으로 rollback합니다. Application project와 Git은 대상이 아닙니다.

### 수동 strict verification

Installer를 사용하지 않으려면 동일한 검증·설치를 직접 수행할 수 있습니다.

```bash
RELEASE_VERSION="0.7.0"
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

기존 ProofLine tool installation이 있으면 `uv tool install`이 자동으로 덮어쓰지 않습니다. 기존 version에서 `v0.7.0`으로 전환하려면 checksum 검증이 성공한 뒤 마지막 명령에 `--force --no-config`를 명시합니다.

```bash
uv tool install --force --no-config \
  "${RELEASE_DIR}/proofline-${RELEASE_VERSION}-py3-none-any.whl"
```

> `v0.2.0`에는 uv-tool ownership 판정 결함이 있으므로 `v0.2.1` 이상을 사용하세요.

## 설치 확인

```bash
proofline --version
proofline init --dry-run
proofline init
proofline update --check
proofline validate
```

`proofline --version`은 설치 identity를 확인하는 선택 단계입니다. `proofline update --check`는 user harness를 초기화한 뒤 실행합니다.

```text
proofline 0.7.0
```

`proofline` 명령을 찾지 못하면 `uv tool dir --bin`으로 executable 경로를 확인하고 해당 경로가 `PATH`에 포함됐는지 확인하세요.

### User-level ProofLine resources

ProofLine 0.3.0 이상에서는 위 설치 확인 순서의 `proofline init --dry-run`, `proofline init`으로 user-level harness를 초기화합니다. 이 명령은 current project를 변경하지 않습니다.

```text
~/.proofline/
├── manifest.yaml
├── agent-context.md
├── contracts/
├── operations/
├── templates/
└── skills/
```

`docs/operations/*.md` 전체는 public managed operation inventory로 wheel의 `proofline_home/operations/`, HOME의 `~/.proofline/operations/`에 exact bytes로 설치되고 `manifest.yaml` SHA-256 record로 관리됩니다. Exact `proofline init` 재실행과 initialized `proofline update --check`는 이 tree를 읽기만 하며 변경하지 않습니다. Project의 `.proofline/`은 Lines와 criteria 같은 canonical artifact 전용이며 harness resource를 저장하지 않습니다.

## 업데이트

v0.2.1 이상에서는 다음 명령으로 최신 stable official wheel을 확인하거나 설치할 수 있습니다. 단, public `v0.6.0`은 위 exact-tag corrective installer 경계를 사용해야 하며 아래 update 예시를 bootstrap 증거로 사용하지 않습니다. v0.4.0 이상 updater는 verified CLI/package와 `~/.proofline/` manifest, contracts, templates, skills, agent context를 같은 target version으로 함께 갱신합니다. Existing harness가 manifest checksum과 다르거나 unexpected entry·symlink를 포함하면 CLI 설치 전에 실패합니다. v0.4.1부터 `~/.proofline` 교체로 현재 shell의 working directory가 제거된 상태에서도 `proofline update`가 current project path를 요구하지 않고 동작합니다.

v0.3.0 updater에서 v0.4.0 이상으로 처음 전환할 때는 기존 updater가 새 executable을 post-verify하는 `proofline --version` 경계에서 clean existing harness를 한 번 reconcile합니다. `~/.proofline/`이 없는 fresh installation은 자동 생성하지 않으며 계속 명시적 `proofline init`을 사용합니다.

```bash
proofline update --check
proofline update
proofline update --version 0.7.0
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
proofline line init --title "TITLE" --dry-run
proofline line init --title "TITLE"
proofline requirement init line-NNNN --manifest admission.yaml --dry-run
proofline requirement init line-NNNN --manifest admission.yaml
```

자세한 tool environment 운영 계약은 [`docs/operations/proofline-tool-environment.md`](docs/operations/proofline-tool-environment.md)를 참고하세요.
