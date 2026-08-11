param(
    [string]$Version = "",

    [string]$Name = "EasyMotor",

    [string]$CompanyName = "EasyMotor",

    [string]$OutputDirectory = "release",

    [switch]$Clean,

    [switch]$NoInstallDependencies,

    [switch]$SkipTests,

    [switch]$WriteChecksum,

    [Alias("h")]
    [switch]$Help
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$entryScript = Join-Path $scriptDir "easymotor_app.py"
$iconPath = Join-Path $scriptDir "favicon.ico"
$runtimeRequirements = Join-Path $scriptDir "requirements.txt"
$buildRequirements = Join-Path $scriptDir "requirements-build.txt"
$buildRoot = Join-Path $scriptDir "build\easymotor_release"

if ($Help) {
    Write-Host "EasyMotor one-file Windows release builder"
    Write-Host ""
    Write-Host "Usage:"
    Write-Host "  .\build_easymotor_release.ps1 [-Clean] [-Version 1.2.3] [-WriteChecksum]"
    Write-Host ""
    Write-Host "The default output is release\EasyMotor_v<version>_win-<arch>.exe."
    Write-Host "Dependencies and PyInstaller are installed automatically unless -NoInstallDependencies is used."
    exit 0
}

function Assert-FileExists {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required file not found: $Path"
    }
}

function Find-Python {
    $venvPython = Join-Path $scriptDir ".venv\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
        return @{ Exe = $venvPython; Prefix = @() }
    }
    $pythonCommand = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($null -ne $pythonCommand) {
        return @{ Exe = $pythonCommand.Source; Prefix = @() }
    }
    $launcherCommand = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($null -ne $launcherCommand) {
        return @{ Exe = $launcherCommand.Source; Prefix = @("-3") }
    }
    throw "Python 3.10 or newer was not found. Install Python and enable Tcl/Tk."
}

function Invoke-Python {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $allArguments = @($script:pythonPrefix) + $Arguments
    & $script:pythonExe @allArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
}

function Invoke-PythonCapture {
    param([Parameter(Mandatory = $true)][string[]]$Arguments)
    $allArguments = @($script:pythonPrefix) + $Arguments
    $output = & $script:pythonExe @allArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed ($LASTEXITCODE): $($Arguments -join ' ')"
    }
    return ($output | Select-Object -Last 1).ToString().Trim()
}

function Escape-PythonString {
    param([Parameter(Mandatory = $true)][string]$Value)
    return $Value.Replace("\", "\\").Replace("'", "\'")
}

Assert-FileExists $entryScript
Assert-FileExists $iconPath
Assert-FileExists $runtimeRequirements
Assert-FileExists $buildRequirements

$python = Find-Python
$script:pythonExe = $python.Exe
$script:pythonPrefix = $python.Prefix

Push-Location $scriptDir
try {
    $pythonVersion = Invoke-PythonCapture @(
        "-c",
        "import sys; assert sys.version_info >= (3, 10), sys.version; print(sys.version.split()[0])"
    )
    Write-Host "[INFO] Python $pythonVersion" -ForegroundColor Cyan
    try {
        $tclVersion = Invoke-PythonCapture @(
            "-c",
            "import tkinter as tk; runtime=tk.Tcl(); print(runtime.call('info', 'patchlevel'))"
        )
    }
    catch {
        throw (
            "The selected Python installation does not contain a usable Tcl/Tk runtime. " +
            "Re-run the official Python installer, choose Modify, enable 'tcl/tk and IDLE', " +
            "then run this build again. Original error: $($_.Exception.Message)"
        )
    }
    Write-Host "[INFO] Tcl/Tk $tclVersion" -ForegroundColor Cyan

    if (-not $NoInstallDependencies) {
        Write-Host "[INFO] Installing/updating runtime and build dependencies..." -ForegroundColor Cyan
        Invoke-Python @(
            "-m", "pip", "install", "--disable-pip-version-check",
            "-r", $runtimeRequirements, "-r", $buildRequirements
        )
    }
    else {
        Invoke-Python @("-c", "import serial, PyInstaller")
    }

    if ([string]::IsNullOrWhiteSpace($Version)) {
        $Version = Invoke-PythonCapture @(
            "-c", "from easymotor.version import __version__; print(__version__)"
        )
    }
    if ($Version -notmatch '^\d+\.\d+\.\d+(\.\d+)?$') {
        throw "Version must contain three or four numeric parts, for example 1.2.3 or 1.2.3.4."
    }
    if ($Name -notmatch '^[A-Za-z0-9._-]+$') {
        throw "Name may contain only letters, digits, dot, underscore, and hyphen."
    }

    $versionParts = @($Version.Split('.') | ForEach-Object { [int]$_ })
    while ($versionParts.Count -lt 4) {
        $versionParts += 0
    }
    $invalidVersionParts = @(
        $versionParts | Where-Object { $_ -lt 0 -or $_ -gt 65535 }
    )
    if ($invalidVersionParts.Count -gt 0) {
        throw "Every Windows version component must be between 0 and 65535."
    }
    $windowsVersion = $versionParts -join "."
    $versionTuple = $versionParts -join ", "

    $machine = Invoke-PythonCapture @(
        "-c", "import platform; print(platform.machine().lower())"
    )
    $archLabel = switch -Regex ($machine) {
        '^(amd64|x86_64)$' { "x64"; break }
        '^(arm64|aarch64)$' { "arm64"; break }
        '^(x86|i[3-6]86)$' { "x86"; break }
        default { $machine }
    }
    $artifactStem = "{0}_v{1}_win-{2}" -f $Name, $Version, $archLabel

    if ([IO.Path]::IsPathRooted($OutputDirectory)) {
        $outputPath = [IO.Path]::GetFullPath($OutputDirectory)
    }
    else {
        $outputPath = [IO.Path]::GetFullPath((Join-Path $scriptDir $OutputDirectory))
    }
    $workPath = Join-Path $buildRoot "work"
    $specPath = Join-Path $buildRoot "spec"
    $versionFile = Join-Path $buildRoot "EasyMotor_version_info.txt"
    $runtimeVersionHook = Join-Path $buildRoot "EasyMotor_runtime_version.py"
    $targetExe = Join-Path $outputPath "$artifactStem.exe"
    $checksumPath = "$targetExe.sha256"

    if ($Clean) {
        if (Test-Path -LiteralPath $buildRoot) {
            Remove-Item -LiteralPath $buildRoot -Recurse -Force
        }
        if (Test-Path -LiteralPath $targetExe) {
            Remove-Item -LiteralPath $targetExe -Force
        }
        if (Test-Path -LiteralPath $checksumPath) {
            Remove-Item -LiteralPath $checksumPath -Force
        }
    }
    New-Item -ItemType Directory -Force -Path $buildRoot, $workPath, $specPath, $outputPath | Out-Null

    $escapedCompany = Escape-PythonString $CompanyName
    $escapedDescription = Escape-PythonString "EasyMotor Motor Demonstration and Engineering Tool"
    $escapedProduct = Escape-PythonString "EasyMotor"
    $escapedOriginalFilename = Escape-PythonString "$artifactStem.exe"
    $copyrightYear = (Get-Date).Year
    $versionResource = @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=($versionTuple),
    prodvers=($versionTuple),
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo([
      StringTable('040904B0', [
        StringStruct('CompanyName', '$escapedCompany'),
        StringStruct('FileDescription', '$escapedDescription'),
        StringStruct('FileVersion', '$windowsVersion'),
        StringStruct('InternalName', '$Name'),
        StringStruct('LegalCopyright', 'Copyright (C) $copyrightYear $escapedCompany'),
        StringStruct('OriginalFilename', '$escapedOriginalFilename'),
        StringStruct('ProductName', '$escapedProduct'),
        StringStruct('ProductVersion', '$Version')
      ])
    ]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"@
    Set-Content -LiteralPath $versionFile -Value $versionResource -Encoding UTF8
    Set-Content -LiteralPath $runtimeVersionHook -Encoding ASCII -Value @(
        "import os",
        "os.environ['EASYMOTOR_BUILD_VERSION'] = '$Version'"
    )

    if (-not $SkipTests) {
        Write-Host "[INFO] Running software tests..." -ForegroundColor Cyan
        Invoke-Python @("-m", "unittest", "discover", "-s", "tests", "-v")
    }

    $addData = "$iconPath$([IO.Path]::PathSeparator)."
    $pyInstallerArguments = @(
        "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--windowed",
        "--name", $artifactStem,
        "--icon", $iconPath,
        "--version-file", $versionFile,
        "--runtime-hook", $runtimeVersionHook,
        "--add-data", $addData,
        "--paths", $scriptDir,
        "--hidden-import", "serial.tools.list_ports",
        "--distpath", $outputPath,
        "--workpath", $workPath,
        "--specpath", $specPath
    )
    if ($Clean) {
        $pyInstallerArguments += "--clean"
    }
    $pyInstallerArguments += $entryScript

    Write-Host "[INFO] Building one-file EasyMotor executable..." -ForegroundColor Cyan
    Invoke-Python $pyInstallerArguments

    if (-not (Test-Path -LiteralPath $targetExe -PathType Leaf)) {
        throw "PyInstaller completed but the expected EXE was not found: $targetExe"
    }
    $versionInfo = [Diagnostics.FileVersionInfo]::GetVersionInfo($targetExe)
    if ($versionInfo.FileVersion -ne $windowsVersion) {
        throw "EXE FileVersion mismatch: expected $windowsVersion, got $($versionInfo.FileVersion)."
    }
    if (-not $versionInfo.ProductVersion.StartsWith($Version)) {
        throw "EXE ProductVersion mismatch: expected $Version, got $($versionInfo.ProductVersion)."
    }

    if ($WriteChecksum) {
        $hash = Get-FileHash -LiteralPath $targetExe -Algorithm SHA256
        Set-Content -LiteralPath $checksumPath -Encoding ASCII -Value (
            "{0} *{1}" -f $hash.Hash.ToLowerInvariant(), (Split-Path -Leaf $targetExe)
        )
    }

    $sizeMiB = [Math]::Round((Get-Item -LiteralPath $targetExe).Length / 1MB, 2)
    Write-Host ""
    Write-Host "[OK] EasyMotor release created" -ForegroundColor Green
    Write-Host "     EXE: $targetExe"
    Write-Host "     Version: $Version (Windows $windowsVersion)"
    Write-Host "     Architecture: $archLabel"
    Write-Host "     Size: $sizeMiB MiB"
    if ($WriteChecksum) {
        Write-Host "     SHA256: $checksumPath"
    }
}
finally {
    Pop-Location
}
