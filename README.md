# ProofLine

ProofLine은 AI agent와 함께 변경 사양을 Line, Discovery, REQ, AC로 관리하고 검증하는 Python CLI입니다.

## 설치

공개 `v0.9.0`에서 Line별 비정식 참고 문서를 위한 명시적 `line-NNNN/evidence/` 경계를 포함한 `v0.9.1`로 전환할 때는 immutable exact-tag installer를 사용합니다. 이 전환은 package만 교체하며 과거 `~/.proofline/`, project `.proofline/`, agent skill registry와 agent target을 읽거나 변경하지 않습니다.

```bash
curl -fsSL https://raw.githubusercontent.com/genichin/proofline/v0.9.1/install.sh | sh -s -- --force
```

```powershell
$InstallerPath = Join-Path ([System.IO.Path]::GetTempPath()) ("proofline-install-" + [guid]::NewGuid().ToString("N") + ".ps1")
try {
    Invoke-WebRequest -UseBasicParsing -Uri "https://raw.githubusercontent.com/genichin/proofline/v0.9.1/install.ps1" -OutFile $InstallerPath
    & $InstallerPath -Force
} finally {
    if (Test-Path -LiteralPath $InstallerPath) { Remove-Item -LiteralPath $InstallerPath -Force }
}
```

Installer는 exact wheel과 `SHA256SUMS`, 격리 설치·import, 최종 executable version과 distribution 위치를 확인합니다.

## 프로젝트

```bash
proofline project init
proofline line init --title "변경 제목"
proofline requirement init line-0001 --manifest admission.yaml
proofline validate
```

Project 설정은 root의 `proofline.yaml`, canonical artifact는 `.proofline/`에 저장합니다. 자세한 contract는 [`docs/artifact-layout.md`](docs/artifact-layout.md)에서 시작합니다.

## Agent Skill

ProofLine은 설치된 package의 검증된 `proofline-*` skill을 실제 agent 탐색 위치에 복사합니다. 링크나 agent 설정 변경은 사용하지 않습니다.

```bash
proofline agent-skill setup hermes --profile default
proofline agent-skill setup codex --scope user
proofline agent-skill status
proofline agent-skill doctor hermes --profile default
```

Hermes는 확인된 profile skill root 아래 `proofline/` 묶음을, Codex는 `$HOME/.agents/skills/proofline-*/` 평면 배치를 사용합니다. 설치별 manifest는 Linux `${XDG_STATE_HOME:-~/.local/state}/proofline/agent-skills/`, Windows `%LOCALAPPDATA%\ProofLine\State\agent-skills\`에 저장하며 agent target에는 manifest를 두지 않습니다.

기존 target bytes가 현재 package와 정확히 같을 때만 명시적으로 등록할 수 있습니다.

```bash
proofline agent-skill setup codex --scope user --adopt-existing
```

```bash
proofline agent-skill repair codex --scope user
proofline agent-skill remove codex --scope user
proofline agent-skill unregister codex --scope user
```

`remove`는 유효한 소유권과 exact bytes를 확인한 뒤 관리 target과 manifest를 제거합니다. `unregister`는 target을 그대로 두고 manifest만 제거합니다.

## 상태와 업데이트

```bash
proofline status
proofline status --json
proofline update --check
proofline update
proofline update --no-sync-agent-skills
```

`proofline status`는 network 요청이나 변경 없이 package, 현재 directory의 project, 등록된 agent skill 상태를 구분해 출력합니다. 기본 update는 등록 설치를 선택 wheel과 함께 동기화하며 package-only 선택은 문제 설치를 `skipped-with-issues`로 보고합니다.

과거 version이 만든 `~/.proofline/`은 더 이상 ProofLine의 관리 대상이나 resource 원본이 아닙니다. 새 명령은 해당 tree를 자동 삭제·이동·등록하지 않습니다.
