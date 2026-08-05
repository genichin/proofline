[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$WheelPath,
    [Parameter(Mandatory = $true)]
    [string]$ChecksumPath
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Assert-True([bool]$Condition, [string]$Message) {
    if (-not $Condition) { throw $Message }
}

function Get-FirstApplicationPath([string]$Name) {
    $Commands = @(Get-Command -Name $Name -CommandType Application)
    Assert-True ($Commands.Count -ge 1) "$Name executable not found"
    return $Commands[0].Source
}

function Invoke-NativeCapture(
    [string]$Executable,
    [string[]]$Arguments
) {
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $Output = (& $Executable @Arguments 2>&1 | Out-String)
        $ExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    return [pscustomobject]@{ ExitCode = $ExitCode; Output = $Output }
}

function Get-TreeSnapshot([string]$Root) {
    if (-not (Test-Path -LiteralPath $Root)) { return "<absent>" }
    $RootPath = [System.IO.Path]::GetFullPath($Root).TrimEnd("\")
    $Records = @()
    foreach ($Item in Get-ChildItem -LiteralPath $RootPath -Force -Recurse | Sort-Object FullName) {
        $Relative = $Item.FullName.Substring($RootPath.Length).TrimStart("\")
        if ($Item.PSIsContainer) {
            $Records += "D|$Relative|$($Item.Attributes)|$($Item.LastWriteTimeUtc.Ticks)"
        } else {
            $Hash = (Get-FileHash -LiteralPath $Item.FullName -Algorithm SHA256).Hash
            $Records += "F|$Relative|$($Item.Attributes)|$($Item.LastWriteTimeUtc.Ticks)|$Hash"
        }
    }
    return ($Records -join "`n")
}

function Get-ApplicationSnapshot([string]$Application) {
    $Records = @()
    foreach ($Relative in @("pyproject.toml", "uv.lock", ".venv", ".proofline")) {
        $Records += "$Relative=$(Get-TreeSnapshot (Join-Path $Application $Relative))"
    }
    $GitState = (& $script:GitPath -C $Application status --porcelain=v1 | Out-String)
    Assert-True ($LASTEXITCODE -eq 0) "fixture git status failed"
    $Records += "git status --porcelain=v1=$GitState"
    return ($Records -join "`n---`n")
}

function Get-InstallerTempSnapshot() {
    $NativeTemp = [System.IO.Path]::GetTempPath()
    $Paths = @(
        Get-ChildItem -LiteralPath $NativeTemp -Directory -Filter "proofline-install-*" -ErrorAction SilentlyContinue |
            ForEach-Object { $_.FullName } |
            Sort-Object
    )
    return ($Paths -join "`n")
}

function Invoke-InstallerChild(
    [string]$Script,
    [string[]]$InstallerArguments,
    [string]$Application
) {
    $PowerShellPath = (Get-Process -Id $PID).Path
    $ChildArguments = @(
        "-NoLogo", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", $Script
    )
    $ChildArguments += $InstallerArguments
    $TempBefore = Get-InstallerTempSnapshot
    $ApplicationBefore = Get-ApplicationSnapshot $Application
    Push-Location $Application
    try {
        $Capture = Invoke-NativeCapture -Executable $PowerShellPath -Arguments $ChildArguments
        $Output = $Capture.Output
        $ExitCode = $Capture.ExitCode
    } finally {
        Pop-Location
    }
    Assert-True ((Get-ApplicationSnapshot $Application) -ceq $ApplicationBefore) "installer changed application cwd state"
    Assert-True ((Get-InstallerTempSnapshot) -ceq $TempBefore) "installer left a new proofline-install-* temporary directory"
    return [pscustomobject]@{ ExitCode = $ExitCode; Output = $Output }
}

function Set-IsolatedInstallerEnvironment([string]$Root, [string]$Name) {
    $env:USERPROFILE = Join-Path $Root "$Name user"
    $env:HOME = $env:USERPROFILE
    $env:UV_TOOL_DIR = Join-Path $Root "$Name tools"
    $env:UV_TOOL_BIN_DIR = Join-Path $Root "$Name bin"
    [void](New-Item -ItemType Directory -Path $env:USERPROFILE -Force)
}

function Write-Checksums([string]$Path, [string]$Content) {
    Set-Content -LiteralPath $Path -Value $Content -NoNewline
}

function Assert-FailedBeforeInstall(
    [string]$Label,
    [string]$FixtureInstaller,
    [string]$Application,
    [string]$Root,
    [string]$Trace
) {
    Set-IsolatedInstallerEnvironment $Root $Label
    if (Test-Path -LiteralPath $Trace) { Remove-Item -LiteralPath $Trace -Force }
    $Result = Invoke-InstallerChild -Script $FixtureInstaller -InstallerArguments @() -Application $Application
    Assert-True ($Result.ExitCode -ne 0) "$Label was accepted"
    $TraceText = if (Test-Path -LiteralPath $Trace) { Get-Content -LiteralPath $Trace -Raw } else { "" }
    Assert-True (-not $TraceText.Contains("tool install")) "$Label reached uv tool install before checksum success"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $env:UV_TOOL_DIR "proofline"))) "$Label left a tool installation"
}

$WheelPath = (Resolve-Path -LiteralPath $WheelPath).Path
$ChecksumPath = (Resolve-Path -LiteralPath $ChecksumPath).Path
$RepoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$WheelName = Split-Path -Leaf $WheelPath
$VersionMatch = [regex]::Match($WheelName, '^proofline-(\d+\.\d+\.\d+)-py3-none-any\.whl$')
Assert-True $VersionMatch.Success "candidate wheel filename is invalid"
$Version = $VersionMatch.Groups[1].Value
$CandidateDigest = (Get-FileHash -LiteralPath $WheelPath -Algorithm SHA256).Hash.ToLowerInvariant()
$ChecksumLines = @(Get-Content -LiteralPath $ChecksumPath)
Assert-True ($ChecksumLines.Count -eq 1) "artifact SHA256SUMS must contain exactly one expected wheel record"
$EscapedWheelName = [regex]::Escape($WheelName)
Assert-True ($ChecksumLines[0] -match "^([0-9A-Fa-f]{64})\s+\*?${EscapedWheelName}$") "artifact SHA256SUMS expected wheel record is malformed"
Assert-True ($Matches[1].ToLowerInvariant() -ceq $CandidateDigest) "artifact candidate wheel SHA256 digest mismatch"
Write-Output "candidate-wheel: $WheelName"
Write-Output "candidate-sha256: $CandidateDigest"

$OriginalEnvironment = @{}
foreach ($Name in @("USERPROFILE", "HOME", "UV_TOOL_DIR", "UV_TOOL_BIN_DIR", "Path", "UV_REAL_EXE", "UV_FAKE_MODE", "UV_TRACE")) {
    $OriginalEnvironment[$Name] = [Environment]::GetEnvironmentVariable($Name, "Process")
}
$MachinePathBefore = [Environment]::GetEnvironmentVariable("Path", "Machine")
$MachineEnvironmentBefore = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" | ConvertTo-Json -Compress)
$GitPath = Get-FirstApplicationPath "git"
$Root = Join-Path ([System.IO.Path]::GetTempPath()) ("proofline candidate " + [guid]::NewGuid().ToString("N"))
$ServerJob = $null

try {
    [void](New-Item -ItemType Directory -Path $Root)
    $Application = Join-Path $Root "application with spaces"
    Copy-Item -LiteralPath (Join-Path $RepoRoot "tests\fixtures\valid-minimal") -Destination $Application -Recurse
    $FixtureLine = Join-Path $Application ".proofline\lines\line-0001\line-0001.md"
    $FixtureLineText = Get-Content -LiteralPath $FixtureLine -Raw
    Assert-True ($FixtureLineText.Contains("execution_status: verifying")) "fixture Line status is unexpected"
    Set-Content -LiteralPath $FixtureLine -Value $FixtureLineText.Replace("execution_status: verifying", "execution_status: delivered") -NoNewline
    Set-Content -LiteralPath (Join-Path $Application "pyproject.toml") -Value "[project]`nname='candidate-fixture'`nversion='0.0.0'`n" -NoNewline
    Set-Content -LiteralPath (Join-Path $Application "uv.lock") -Value "version = 1`nrevision = 1`n" -NoNewline
    & $GitPath -C $Application init -q -b main
    Assert-True ($LASTEXITCODE -eq 0) "fixture git init failed"
    & $GitPath -C $Application config core.autocrlf false
    Assert-True ($LASTEXITCODE -eq 0) "fixture git newline configuration failed"
    & $GitPath -C $Application add -A
    & $GitPath -C $Application -c user.name="ProofLine Gate" -c user.email="proofline@example.invalid" commit -qm "fixture baseline"
    Assert-True ($LASTEXITCODE -eq 0) "fixture git commit failed"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $Application ".venv"))) "fixture .venv must start absent"

    Push-Location $RepoRoot
    try {
        & uv run pytest -q -p no:cacheprovider --basetemp (Join-Path $Root "pytest temp") tests/test_home_init.py
        Assert-True ($LASTEXITCODE -eq 0) "tests/test_home_init.py failed on native Windows"
    } finally {
        Pop-Location
    }

    $DirectUser = Join-Path $Root "direct user"
    $DirectToolDir = Join-Path $Root "direct tools"
    $DirectToolBin = Join-Path $Root "direct bin"
    foreach ($Directory in @($DirectUser, $DirectToolDir, $DirectToolBin)) {
        [void](New-Item -ItemType Directory -Path $Directory)
    }
    $env:USERPROFILE = $DirectUser
    $env:HOME = $DirectUser
    $env:UV_TOOL_DIR = $DirectToolDir
    $env:UV_TOOL_BIN_DIR = $DirectToolBin
    $ApplicationBeforeDirect = Get-ApplicationSnapshot $Application
    Push-Location $Application
    try {
        & uv tool install --no-config $WheelPath
        Assert-True ($LASTEXITCODE -eq 0) "candidate uv tool installation failed"
    } finally {
        Pop-Location
    }
    Assert-True ((Get-ApplicationSnapshot $Application) -ceq $ApplicationBeforeDirect) "direct candidate install changed application"
    $ProofLine = Join-Path $DirectToolBin "proofline.exe"
    $ToolPython = Join-Path $DirectToolDir "proofline\Scripts\python.exe"
    Assert-True (Test-Path -LiteralPath $ProofLine -PathType Leaf) "candidate executable is absent"
    Assert-True (Test-Path -LiteralPath $ToolPython -PathType Leaf) "candidate tool Python is absent"
    $ObservedVersion = (& $ProofLine --version | Out-String).Trim()
    Assert-True ($LASTEXITCODE -eq 0 -and $ObservedVersion -ceq "proofline $Version") "candidate executable version mismatch"
    $ProvenanceCode = "from importlib.metadata import version; from pathlib import Path; import proofline; p=Path(proofline.__file__).resolve(); print(version('proofline')+'|'+str(p)); assert 'site-packages' in p.parts"
    $Provenance = (& $ToolPython -I -c $ProvenanceCode | Out-String).Trim().Split("|", 2)
    Assert-True ($LASTEXITCODE -eq 0 -and $Provenance.Count -eq 2) "candidate distribution provenance probe failed"
    Assert-True ($Provenance[0] -ceq $Version) "candidate distribution metadata version mismatch"
    Assert-True ([System.IO.Path]::GetFullPath($Provenance[1]).StartsWith([System.IO.Path]::GetFullPath($DirectToolDir), [System.StringComparison]::OrdinalIgnoreCase)) "candidate import is outside isolated tool dir"

    $ApplicationBeforeCandidateSequence = Get-ApplicationSnapshot $Application
    Push-Location $Application
    try {
        $HomeBeforeDryRun = Get-TreeSnapshot $DirectUser
        $DryRun = (& $ProofLine init --dry-run | Out-String)
        Assert-True ($LASTEXITCODE -eq 0 -and $DryRun.Contains("would create")) "init --dry-run failed"
        Assert-True ((Get-TreeSnapshot $DirectUser) -ceq $HomeBeforeDryRun) "init --dry-run mutated USERPROFILE"
        $Created = (& $ProofLine init | Out-String)
        Assert-True ($LASTEXITCODE -eq 0 -and $Created.Contains("created")) "fresh init failed"
        $ManifestProbe = "from pathlib import Path; import hashlib,yaml; root=Path(r'$($DirectUser.Replace("'", "''"))')/'.proofline'; m=yaml.safe_load((root/'manifest.yaml').read_text()); rec=m['managed_files']; assert rec and len({x['path'] for x in rec})==len(rec); assert all(hashlib.sha256((root/x['path']).read_bytes()).hexdigest()==x['sha256'] for x in rec); print(len(rec))"
        $ManagedCount = (& $ToolPython -I -c $ManifestProbe | Out-String).Trim()
        Assert-True ($LASTEXITCODE -eq 0 -and [int]$ManagedCount -gt 0) "manifest managed_files SHA256 verification failed"
        $HomeBeforeRepeat = Get-TreeSnapshot $DirectUser
        $Repeated = (& $ProofLine init | Out-String)
        Assert-True ($LASTEXITCODE -eq 0 -and $Repeated.Contains("already-initialized")) "repeat init was not already-initialized"
        Assert-True ((Get-TreeSnapshot $DirectUser) -ceq $HomeBeforeRepeat) "repeat init mutated USERPROFILE"
        $HomeBeforeCheck = Get-TreeSnapshot $DirectUser
        $UpdateCheck = (& $ProofLine update --check --version $Version | Out-String)
        Assert-True ($LASTEXITCODE -eq 0 -and $UpdateCheck.Contains("status: already-current")) "initialized update --check was not already-current"
        Assert-True ((Get-TreeSnapshot $DirectUser) -ceq $HomeBeforeCheck) "update --check mutated USERPROFILE"
        $Validation = (& $ProofLine validate | Out-String)
        Assert-True ($LASTEXITCODE -eq 0) "fixture project validate failed: $Validation"
    } finally {
        Pop-Location
    }
    Assert-True ((Get-ApplicationSnapshot $Application) -ceq $ApplicationBeforeCandidateSequence) "installed candidate sequence changed application"

    $Assets = Join-Path $Root "installer assets with spaces"
    [void](New-Item -ItemType Directory -Path $Assets)
    Copy-Item -LiteralPath $WheelPath -Destination (Join-Path $Assets $WheelName)
    $AssetChecksums = Join-Path $Assets "SHA256SUMS"
    Write-Checksums $AssetChecksums "$CandidateDigest  $WheelName`n"
    $ReadyFile = Join-Path $Root "server ready.txt"
    $ServerScript = Join-Path $Root "fixture server.py"
    @'
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import functools, sys
assets, ready = sys.argv[1], Path(sys.argv[2])
handler = functools.partial(SimpleHTTPRequestHandler, directory=assets)
server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
ready.write_text(str(server.server_address[1]), encoding="ascii")
server.serve_forever()
'@ | Set-Content -LiteralPath $ServerScript -NoNewline
    $PythonPath = Get-FirstApplicationPath "python"
    $ServerJob = Start-Job -ScriptBlock {
        param($PythonExecutable, $ScriptPath, $AssetPath, $ReadyPath)
        & $PythonExecutable $ScriptPath $AssetPath $ReadyPath
    } -ArgumentList $PythonPath, $ServerScript, $Assets, $ReadyFile
    $Deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $ReadyFile)) {
        $JobState = (Get-Job -Id $ServerJob.Id).State
        if ($JobState -in @("Completed", "Failed", "Stopped")) {
            $ServerOutput = (Receive-Job -Id $ServerJob.Id 2>&1 | Out-String)
            throw "fixture server exited before readiness: $ServerOutput"
        }
        if ([DateTime]::UtcNow -gt $Deadline) { throw "fixture server readiness timed out" }
        Start-Sleep -Milliseconds 100
    }
    $Port = [int](Get-Content -LiteralPath $ReadyFile -Raw)

    $ProductionInstaller = Get-Content -LiteralPath (Join-Path $RepoRoot "install.ps1") -Raw
    $OfficialBase = '$BASE_URL = "https://github.com/${REPOSITORY}/releases/download/v${VERSION}"'
    Assert-True ($ProductionInstaller.Contains($OfficialBase)) "installer immutable URL constant was not found"
    $FixtureInstaller = Join-Path $Root "installer fixture with spaces.ps1"
    $FixtureBase = '$BASE_URL = "http://127.0.0.1:' + $Port + '"'
    Set-Content -LiteralPath $FixtureInstaller -Value $ProductionInstaller.Replace($OfficialBase, $FixtureBase) -NoNewline
    $DownloadFailureInstaller = Join-Path $Root "download failure fixture.ps1"
    $DownloadFailureBase = '$BASE_URL = "http://127.0.0.1:' + $Port + '/missing"'
    Set-Content -LiteralPath $DownloadFailureInstaller -Value $ProductionInstaller.Replace($OfficialBase, $DownloadFailureBase) -NoNewline

    $RealUv = Get-FirstApplicationPath "uv"
    $FakeBin = Join-Path $Root "fake uv bin with spaces"
    [void](New-Item -ItemType Directory -Path $FakeBin)
    $UvWrapper = Join-Path $FakeBin "uv.cmd"
    @'
@echo off
>>"%UV_TRACE%" echo %CD%^|%*
if /I "%UV_FAKE_MODE%"=="install-fail" if /I "%~1 %~2"=="tool install" exit /b 41
"%UV_REAL_EXE%" %*
set "UV_CODE=%ERRORLEVEL%"
if not "%UV_CODE%"=="0" exit /b %UV_CODE%
if /I "%UV_FAKE_MODE%"=="post-fail" if /I "%~1 %~2"=="tool install" del /q "%UV_TOOL_BIN_DIR%\proofline.exe"
exit /b 0
'@ | Set-Content -LiteralPath $UvWrapper -NoNewline
    $env:UV_REAL_EXE = $RealUv
    $env:UV_TRACE = Join-Path $Root "uv trace.txt"
    $env:UV_FAKE_MODE = ""
    $env:Path = "$FakeBin;$($OriginalEnvironment['Path'])"

    Set-IsolatedInstallerEnvironment $Root "fresh success"
    $Fresh = Invoke-InstallerChild -Script $FixtureInstaller -InstallerArguments @() -Application $Application
    Assert-True ($Fresh.ExitCode -eq 0) "fresh success installer fixture failed: $($Fresh.Output)"
    Assert-True ((Get-Content -LiteralPath $env:UV_TRACE -Raw).Contains($Application)) "installer child did not inherit the application cwd"
    $Refusal = Invoke-InstallerChild -Script $FixtureInstaller -InstallerArguments @() -Application $Application
    Assert-True ($Refusal.ExitCode -ne 0) "default existing refusal was accepted"
    $Forced = Invoke-InstallerChild -Script $FixtureInstaller -InstallerArguments @("-Force") -Application $Application
    Assert-True ($Forced.ExitCode -eq 0) "explicit force success failed: $($Forced.Output)"

    Set-IsolatedInstallerEnvironment $Root "unknown option"
    $Unknown = Invoke-InstallerChild -Script $FixtureInstaller -InstallerArguments @("-UnknownOption") -Application $Application
    Assert-True ($Unknown.ExitCode -ne 0) "unknown option was accepted"

    Set-IsolatedInstallerEnvironment $Root "missing uv prerequisite"
    $SavedPath = $env:Path
    $env:Path = ""
    try {
        $MissingUv = Invoke-InstallerChild -Script $FixtureInstaller -InstallerArguments @() -Application $Application
    } finally {
        $env:Path = $SavedPath
    }
    Assert-True ($MissingUv.ExitCode -ne 0) "missing uv prerequisite was accepted"

    Set-IsolatedInstallerEnvironment $Root "download failure"
    $Download = Invoke-InstallerChild -Script $DownloadFailureInstaller -InstallerArguments @() -Application $Application
    Assert-True ($Download.ExitCode -ne 0) "download failure was accepted"

    Write-Checksums $AssetChecksums "not-a-checksum`n"
    Assert-FailedBeforeInstall "malformed checksum" $FixtureInstaller $Application $Root $env:UV_TRACE
    Write-Checksums $AssetChecksums ""
    Assert-FailedBeforeInstall "missing checksum" $FixtureInstaller $Application $Root $env:UV_TRACE
    Write-Checksums $AssetChecksums "$CandidateDigest  $WheelName`n$CandidateDigest  $WheelName`n"
    Assert-FailedBeforeInstall "duplicate checksum" $FixtureInstaller $Application $Root $env:UV_TRACE
    Write-Checksums $AssetChecksums "$CandidateDigest  $WheelName`nnot-a-checksum`n"
    Assert-FailedBeforeInstall "valid checksum plus malformed extra" $FixtureInstaller $Application $Root $env:UV_TRACE
    Write-Checksums $AssetChecksums "$CandidateDigest  $WheelName`n$CandidateDigest  other.whl`n"
    Assert-FailedBeforeInstall "valid checksum plus unexpected extra" $FixtureInstaller $Application $Root $env:UV_TRACE
    Write-Checksums $AssetChecksums "$CandidateDigest  other.whl`n"
    Assert-FailedBeforeInstall "wrong filename checksum record" $FixtureInstaller $Application $Root $env:UV_TRACE
    Write-Checksums $AssetChecksums "$('0' * 64)  $WheelName`n"
    Assert-FailedBeforeInstall "wrong checksum" $FixtureInstaller $Application $Root $env:UV_TRACE
    Write-Checksums $AssetChecksums "$CandidateDigest  $WheelName`n"

    Set-IsolatedInstallerEnvironment $Root "uv install failure"
    $env:UV_FAKE_MODE = "install-fail"
    $InstallFailure = Invoke-InstallerChild -Script $FixtureInstaller -InstallerArguments @() -Application $Application
    Assert-True ($InstallFailure.ExitCode -ne 0) "uv install failure was accepted"
    Assert-True (-not (Test-Path -LiteralPath (Join-Path $env:UV_TOOL_DIR "proofline"))) "uv install failure left a tool installation"

    Set-IsolatedInstallerEnvironment $Root "post-verification failure"
    $env:UV_FAKE_MODE = "post-fail"
    $PostFailure = Invoke-InstallerChild -Script $FixtureInstaller -InstallerArguments @() -Application $Application
    Assert-True ($PostFailure.ExitCode -ne 0) "post-verification failure was accepted"
    $env:UV_FAKE_MODE = ""

    $CallerProbe = Join-Path $Root "caller survival probe.ps1"
    @'
param([string]$Installer)
$Survived = $false
try {
    Invoke-Expression (Get-Content -LiteralPath $Installer -Raw)
} catch {
    $Survived = $true
}
if (-not $Survived) { throw "installer failure did not propagate" }
Write-Output "CALLER_SURVIVED"
'@ | Set-Content -LiteralPath $CallerProbe -NoNewline
    $env:Path = ""
    try {
        $PowerShellPath = (Get-Process -Id $PID).Path
        $CallerArguments = @("-NoProfile", "-NonInteractive", "-File", $CallerProbe, $FixtureInstaller)
        $CallerCapture = Invoke-NativeCapture -Executable $PowerShellPath -Arguments $CallerArguments
        $CallerOutput = $CallerCapture.Output
        $CallerExit = $CallerCapture.ExitCode
    } finally {
        $env:Path = $SavedPath
    }
    Assert-True ($CallerExit -eq 0 -and $CallerOutput.Contains("CALLER_SURVIVED")) "Invoke-Expression caller host did not survive installer failure"

    Assert-True ([Environment]::GetEnvironmentVariable("Path", "Machine") -ceq $MachinePathBefore) "machine PATH changed"
    $MachineEnvironmentAfter = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment" | ConvertTo-Json -Compress)
    Assert-True ($MachineEnvironmentAfter -ceq $MachineEnvironmentBefore) "machine-scoped registry environment changed"
    Write-Output "Candidate Windows verification PASS"
} finally {
    if ($null -ne $ServerJob) {
        Stop-Job -Id $ServerJob.Id -ErrorAction SilentlyContinue
        Remove-Job -Id $ServerJob.Id -Force -ErrorAction SilentlyContinue
    }
    foreach ($Name in $OriginalEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($Name, $OriginalEnvironment[$Name], "Process")
    }
    if (Test-Path -LiteralPath $Root) { Remove-Item -LiteralPath $Root -Recurse -Force }
}
