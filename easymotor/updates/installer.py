"""Launch a detached PowerShell helper that safely replaces the running EXE."""

from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Final


HEALTH_ARGUMENT: Final = "--easymotor-update-health"
HEALTH_TIMEOUT_SECONDS: Final = 30

_HELPER_SCRIPT = r'''param(
    [Parameter(Mandatory=$true)][string]$Staged,
    [Parameter(Mandatory=$true)][string]$Target,
    [Parameter(Mandatory=$true)][int]$OldPid,
    [Parameter(Mandatory=$true)][string]$Marker,
    [Parameter(Mandatory=$true)][string]$LogPath,
    [switch]$Elevated
)
$ErrorActionPreference = "Stop"
function Quote-Argument([string]$Value) { return '"' + $Value.Replace('"', '\"') + '"' }
try { Wait-Process -Id $OldPid -ErrorAction SilentlyContinue } catch {}
$backup = "$Target.previous"
try {
    $probe = Join-Path (Split-Path -Parent $Target) ('.easymotor-update-' + [Guid]::NewGuid().ToString('N') + '.tmp')
    Set-Content -LiteralPath $probe -Value 'probe' -Encoding ASCII
    Remove-Item -LiteralPath $probe -Force
} catch {
    if ($Elevated) {
        ("Update failed before replacement: " + $_.Exception.Message) | Set-Content -LiteralPath $LogPath -Encoding UTF8
        if (Test-Path -LiteralPath $Target) { Start-Process -FilePath $Target }
        exit 1
    }
    $args = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Quote-Argument $PSCommandPath),
        '-Staged', (Quote-Argument $Staged), '-Target', (Quote-Argument $Target),
        '-OldPid', $OldPid, '-Marker', (Quote-Argument $Marker),
        '-LogPath', (Quote-Argument $LogPath), '-Elevated'
    )
    try {
        Start-Process -FilePath 'powershell.exe' -Verb RunAs -ArgumentList $args
    } catch {
        ("Update elevation was cancelled or failed: " + $_.Exception.Message) | Set-Content -LiteralPath $LogPath -Encoding UTF8
        if (Test-Path -LiteralPath $Target) { Start-Process -FilePath $Target }
        exit 1
    }
    exit 0
}
try {
    if (Test-Path -LiteralPath $backup) { Remove-Item -LiteralPath $backup -Force }
    Move-Item -LiteralPath $Target -Destination $backup -Force
    Move-Item -LiteralPath $Staged -Destination $Target -Force
} catch {
    try { if (-not (Test-Path -LiteralPath $Target) -and (Test-Path -LiteralPath $backup)) { Move-Item -LiteralPath $backup -Destination $Target -Force } } catch {}
    ("Update replacement failed and the previous version was restored: " + $_.Exception.Message) | Set-Content -LiteralPath $LogPath -Encoding UTF8
    if (Test-Path -LiteralPath $Target) { Start-Process -FilePath $Target }
    exit 1
}
try {
    $process = Start-Process -FilePath $Target -ArgumentList @('__HEALTH_ARGUMENT__', (Quote-Argument $Marker)) -PassThru
    $deadline = (Get-Date).AddSeconds(__HEALTH_TIMEOUT__)
    while ((Get-Date) -lt $deadline -and -not (Test-Path -LiteralPath $Marker) -and -not $process.HasExited) {
        Start-Sleep -Milliseconds 250
        $process.Refresh()
    }
    if (-not (Test-Path -LiteralPath $Marker)) { throw 'The updated application did not report a healthy startup.' }
    Remove-Item -LiteralPath $Marker -Force -ErrorAction SilentlyContinue
    Remove-Item -LiteralPath $backup -Force -ErrorAction SilentlyContinue
    "Update completed successfully." | Set-Content -LiteralPath $LogPath -Encoding UTF8
} catch {
    try { if ($process -and -not $process.HasExited) { Stop-Process -Id $process.Id -Force } } catch {}
    try { if (Test-Path -LiteralPath $Target) { Remove-Item -LiteralPath $Target -Force } } catch {}
    try { if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $Target -Force } } catch {}
    ("Update failed and the previous version was restored: " + $_.Exception.Message) | Set-Content -LiteralPath $LogPath -Encoding UTF8
    if (Test-Path -LiteralPath $Target) { Start-Process -FilePath $Target }
    exit 1
}
'''.replace("__HEALTH_ARGUMENT__", HEALTH_ARGUMENT).replace(
    "__HEALTH_TIMEOUT__", str(HEALTH_TIMEOUT_SECONDS)
)


def update_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "EasyMotor" / "updates"


def health_marker_from_argv(argv: list[str] | None = None) -> Path | None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    try:
        index = arguments.index(HEALTH_ARGUMENT)
    except ValueError:
        return None
    if index + 1 >= len(arguments):
        return None
    marker = Path(arguments[index + 1]).resolve()
    try:
        marker.relative_to(update_root().resolve())
    except ValueError:
        return None
    return marker


def acknowledge_healthy_start(marker: Path | None) -> None:
    if marker is None:
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="ascii")


def launch_update_helper(staged: Path, target: Path) -> Path:
    if os.name != "nt":
        raise OSError("EasyMotor self-update is supported only on Windows")
    staged = staged.resolve(strict=True)
    target = target.resolve(strict=True)
    root = update_root()
    root.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    marker = root / f"health-{identifier}.ok"
    log_path = root / "last-update.log"
    helper = root / f"apply-{identifier}.ps1"
    helper.write_text(_HELPER_SCRIPT, encoding="utf-8-sig")
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "DETACHED_PROCESS", 0
    )
    subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(helper),
            "-Staged",
            str(staged),
            "-Target",
            str(target),
            "-OldPid",
            str(os.getpid()),
            "-Marker",
            str(marker),
            "-LogPath",
            str(log_path),
        ],
        close_fds=True,
        creationflags=creation_flags,
    )
    return helper
