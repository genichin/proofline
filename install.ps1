[CmdletBinding()]
param([switch]$Force)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"
$VERSION = "0.9.0"
$REPOSITORY = "genichin/proofline"
$WHEEL = "proofline-${VERSION}-py3-none-any.whl"
$BASE_URL = "https://github.com/${REPOSITORY}/releases/download/v${VERSION}"
$TempDir = Join-Path ([System.IO.Path]::GetTempPath()) ("proofline-install-" + [guid]::NewGuid().ToString("N"))

try {
    if (-not (Get-Command uv -CommandType Application -ErrorAction SilentlyContinue)) { throw "ProofLine installer: required command not found: uv" }
    [void](New-Item -ItemType Directory -Path $TempDir)
    $ToolDir = (& uv tool dir | Out-String).Trim()
    $ExistingTool = Join-Path $ToolDir "proofline"
    if ((-not $Force) -and (Test-Path -LiteralPath $ExistingTool)) { throw "ProofLine installer: already installed; rerun with -Force" }
    $WheelPath = Join-Path $TempDir $WHEEL
    $ChecksumsPath = Join-Path $TempDir "SHA256SUMS"
    Invoke-WebRequest -UseBasicParsing -Uri "${BASE_URL}/${WHEEL}" -OutFile $WheelPath
    Invoke-WebRequest -UseBasicParsing -Uri "${BASE_URL}/SHA256SUMS" -OutFile $ChecksumsPath
    $Lines = @(Get-Content -LiteralPath $ChecksumsPath)
    $EscapedWheel = [regex]::Escape($WHEEL)
    if ($Lines.Count -ne 1 -or $Lines[0] -notmatch "^([0-9a-f]{64})  ${EscapedWheel}$") { throw "ProofLine installer: invalid SHA256SUMS" }
    if ((Get-FileHash -LiteralPath $WheelPath -Algorithm SHA256).Hash.ToLowerInvariant() -cne $Matches[1]) { throw "ProofLine installer: wheel checksum verification failed" }
    $Stage = Join-Path $TempDir "stage"
    & uv venv --no-config $Stage
    if ($LASTEXITCODE -ne 0) { throw "ProofLine installer: cannot create staging environment" }
    $StagePython = Join-Path $Stage "Scripts\python.exe"
    & uv pip install --no-config --python $StagePython $WheelPath
    if ($LASTEXITCODE -ne 0) { throw "ProofLine installer: cannot stage target package" }
    & $StagePython -I -c "from importlib.metadata import version; import proofline; assert version('proofline') == '$VERSION'"
    if ($LASTEXITCODE -ne 0) { throw "ProofLine installer: staged package verification failed" }
    $Arguments = @("tool", "install", "--no-config")
    if ($Force) { $Arguments += "--force" }
    $Arguments += $WheelPath
    & uv @Arguments
    if ($LASTEXITCODE -ne 0) { throw "ProofLine installer: uv tool install failed" }
    $BinDir = (& uv tool dir --bin | Out-String).Trim()
    $ProofLine = Join-Path $BinDir "proofline.exe"
    if ((& $ProofLine --version | Out-String).Trim() -cne "proofline $VERSION") { throw "ProofLine installer: post-install version verification failed" }
    $ToolPython = Join-Path $ToolDir "proofline\Scripts\python.exe"
    & $ToolPython -I -c "from importlib.metadata import version; from pathlib import Path; import proofline; p=Path(proofline.__file__).resolve(); assert version('proofline') == '$VERSION' and 'site-packages' in p.parts"
    if ($LASTEXITCODE -ne 0) { throw "ProofLine installer: installed distribution provenance verification failed" }
    Write-Output "ProofLine $VERSION installed: $ProofLine"
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    throw
} finally {
    if (Test-Path -LiteralPath $TempDir) { Remove-Item -LiteralPath $TempDir -Recurse -Force }
}
