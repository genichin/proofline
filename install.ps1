[CmdletBinding()]
param(
    [switch]$Force
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$VERSION = "0.5.0"
$REPOSITORY = "genichin/proofline"
$WHEEL = "proofline-${VERSION}-py3-none-any.whl"
$BASE_URL = "https://github.com/${REPOSITORY}/releases/download/v${VERSION}"
$TempDir = $null

function Fail([string]$Message) {
    throw "ProofLine installer: $Message"
}

function Invoke-UvCapture([string[]]$Arguments) {
    $Output = & uv @Arguments
    if ($LASTEXITCODE -ne 0) {
        Fail "uv $($Arguments -join ' ') failed"
    }
    return ($Output | Out-String).Trim()
}

try {
    if (-not (Get-Command uv -CommandType Application -ErrorAction SilentlyContinue)) {
        Fail "required command not found: uv"
    }

    $ToolDir = Invoke-UvCapture @("tool", "dir")
    $ExistingTool = Join-Path $ToolDir "proofline"
    if ((-not $Force) -and (Test-Path -LiteralPath $ExistingTool)) {
        Fail "ProofLine is already installed; rerun with -Force to replace it explicitly"
    }

    $NativeTempRoot = [System.IO.Path]::GetTempPath()
    $TempDir = Join-Path $NativeTempRoot ("proofline-install-" + [guid]::NewGuid().ToString("N"))
    [void](New-Item -ItemType Directory -Path $TempDir)
    $WheelPath = Join-Path $TempDir $WHEEL
    $ChecksumsPath = Join-Path $TempDir "SHA256SUMS"

    Invoke-WebRequest -UseBasicParsing -Uri "${BASE_URL}/${WHEEL}" -OutFile $WheelPath
    Invoke-WebRequest -UseBasicParsing -Uri "${BASE_URL}/SHA256SUMS" -OutFile $ChecksumsPath

    $EscapedWheel = [regex]::Escape($WHEEL)
    $ChecksumLines = @(Get-Content -LiteralPath $ChecksumsPath)
    if ($ChecksumLines.Count -ne 1) {
        Fail "SHA256SUMS must contain exactly one strict checksum record for $WHEEL"
    }
    if ($ChecksumLines[0] -notmatch "^([0-9A-Fa-f]{64})\s+\*?${EscapedWheel}$") {
        Fail "SHA256SUMS contains a malformed or unexpected checksum record"
    }
    $ExpectedHash = $Matches[1].ToLowerInvariant()
    $ActualHash = (Get-FileHash -LiteralPath $WheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($ActualHash -cne $ExpectedHash) {
        Fail "wheel checksum verification failed"
    }

    if ($Force) {
        & uv tool install --force --no-config $WheelPath
    } else {
        & uv tool install --no-config $WheelPath
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "uv tool install failed"
    }

    $BinDir = Invoke-UvCapture @("tool", "dir", "--bin")
    $ProofLine = Join-Path $BinDir "proofline.exe"
    if (-not (Test-Path -LiteralPath $ProofLine -PathType Leaf)) {
        Fail "installed executable not found: $ProofLine"
    }
    $ActualVersion = (& $ProofLine --version | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $ActualVersion -cne "proofline $VERSION") {
        Fail "post-install version verification failed"
    }

    $ToolPython = Join-Path $ExistingTool "Scripts\python.exe"
    if (-not (Test-Path -LiteralPath $ToolPython -PathType Leaf)) {
        Fail "installed tool interpreter not found: $ToolPython"
    }
    $ProvenanceCode = "from importlib.metadata import version; from pathlib import Path; import proofline; p=Path(proofline.__file__).resolve(); print(version('proofline')+'|'+str(p)); assert 'site-packages' in p.parts"
    $Provenance = (& $ToolPython -I -c $ProvenanceCode | Out-String).Trim()
    if ($LASTEXITCODE -ne 0) {
        Fail "installed distribution provenance verification failed"
    }
    $ProvenanceParts = $Provenance.Split("|", 2)
    if ($ProvenanceParts.Count -ne 2 -or $ProvenanceParts[0] -cne $VERSION) {
        Fail "installed distribution version verification failed"
    }
    $InstalledModule = [System.IO.Path]::GetFullPath($ProvenanceParts[1])
    $ExpectedToolRoot = [System.IO.Path]::GetFullPath($ExistingTool)
    if (-not $InstalledModule.StartsWith($ExpectedToolRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        Fail "installed distribution provenance is outside the selected uv tool"
    }

    Write-Output "ProofLine $VERSION installed: $ProofLine"
} catch {
    [Console]::Error.WriteLine($_.Exception.Message)
    throw
} finally {
    if ($null -ne $TempDir -and (Test-Path -LiteralPath $TempDir)) {
        Remove-Item -LiteralPath $TempDir -Recurse -Force
    }
}
