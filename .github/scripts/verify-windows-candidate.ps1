[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$WheelPath,
    [Parameter(Mandatory = $true)][string]$ChecksumPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$CleanRunnerPlanId = "candidate-clean-runner-v1"
$CleanRunnerPlanResource = ".github/resources/candidate-clean-runner-plan-v1.json"
$CleanRunnerPlanOutcome = "contract_only"
$CleanRunnerEndpoint = "https://pypi.org/simple"
$CleanRunnerVersionSource = "uv.lock"
$CleanRunnerNetworkMode = "online-offline"
$CleanRunnerPublicationPrerequisite = "none"
$CleanRunnerStepOrder = @("verify-wheel", "verify-checksum", "create-environment", "install-candidate", "provision-test-dependencies", "contract-probe")

$Root = Join-Path $env:RUNNER_TEMP ("proofline-windows-candidate-" + [guid]::NewGuid().ToString("N"))
try {
    $Wheel = Get-Item -LiteralPath $WheelPath
    $ChecksumLines = @(Get-Content -LiteralPath $ChecksumPath)
    $EscapedName = [regex]::Escape($Wheel.Name)
    if ($ChecksumLines.Count -ne 1 -or $ChecksumLines[0] -notmatch "^([0-9a-fA-F]{64})  ${EscapedName}$") { throw "invalid candidate checksum record" }
    if ((Get-FileHash -LiteralPath $Wheel.FullName -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Matches[1].ToLowerInvariant()) { throw "candidate checksum mismatch" }
    New-Item -ItemType Directory -Path $Root | Out-Null
    $Environment = Join-Path $Root "environment"
    & uv venv --no-config $Environment
    if ($LASTEXITCODE -ne 0) { throw "candidate environment creation failed" }
    $Python = Join-Path $Environment "Scripts\python.exe"
    & uv pip install --no-config --python $Python $Wheel.FullName
    if ($LASTEXITCODE -ne 0) { throw "candidate installation failed" }
    $ProofLine = Join-Path $Environment "Scripts\proofline.exe"
    $env:HOME = Join-Path $Root "home"
    $env:USERPROFILE = $env:HOME
    $env:LOCALAPPDATA = Join-Path $Root "local-app-data"
    New-Item -ItemType Directory -Path $env:HOME | Out-Null
    & $ProofLine status --json
    if ($LASTEXITCODE -ne 0) { throw "candidate local status failed" }
    & $Python -I -c "from importlib.resources import files; root=files('skills'); assert any(p.name.startswith('proofline-') for p in root.iterdir())"
    if ($LASTEXITCODE -ne 0) { throw "candidate skill resource verification failed" }
    Write-Output "candidate-wheel verified"
} finally {
    if (Test-Path -LiteralPath $Root) { Remove-Item -LiteralPath $Root -Recurse -Force }
}
