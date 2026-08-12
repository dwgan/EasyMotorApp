"""Self-update handoff implemented by the downloaded EasyMotor executable."""

from __future__ import annotations

import ctypes
import hashlib
import os
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Final


HEALTH_ARGUMENT: Final = "--easymotor-update-health"
APPLY_ARGUMENT: Final = "--easymotor-apply-update"
ELEVATED_ARGUMENT: Final = "--easymotor-update-elevated"
HEALTH_TIMEOUT_SECONDS: Final = 30
EXIT_TIMEOUT_SECONDS: Final = 15
OFFICIAL_EXECUTABLE_RE: Final = re.compile(
    r"^EasyMotor_v\d+\.\d+\.\d+(?:\.\d+)?_win-x64\.exe$",
    re.IGNORECASE,
)


def update_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "EasyMotor" / "updates"


def _path_argument(arguments: list[str], flag: str) -> Path | None:
    try:
        index = arguments.index(flag)
    except ValueError:
        return None
    if index + 1 >= len(arguments):
        return None
    return Path(arguments[index + 1]).resolve()


def _is_under_update_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(update_root().resolve())
    except ValueError:
        return False
    return True


def health_marker_from_argv(argv: list[str] | None = None) -> Path | None:
    arguments = list(sys.argv[1:] if argv is None else argv)
    marker = _path_argument(arguments, HEALTH_ARGUMENT)
    if marker is None or not _is_under_update_root(marker):
        return None
    return marker


def acknowledge_healthy_start(marker: Path | None) -> None:
    if marker is None:
        return
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("ok\n", encoding="ascii")


def _append_log(log_path: Path, message: str) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as stream:
        stream.write(message.rstrip() + "\n")


def _wait_for_process(pid: int, timeout: float) -> bool:
    """Return True when the process exits (or no longer exists)."""
    if os.name != "nt":
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                os.kill(pid, 0)
            except OSError:
                return True
            time.sleep(0.1)
        return False
    synchronize = 0x00100000
    wait_object_0 = 0
    wait_timeout = 0x00000102
    handle = ctypes.windll.kernel32.OpenProcess(synchronize, False, pid)
    if not handle:
        return True
    try:
        result = ctypes.windll.kernel32.WaitForSingleObject(handle, int(timeout * 1000))
        if result == wait_object_0:
            return True
        if result == wait_timeout:
            return False
        raise OSError(ctypes.get_last_error(), "unable to wait for the previous EasyMotor process")
    finally:
        ctypes.windll.kernel32.CloseHandle(handle)


def _start_process(executable: Path, *arguments: str) -> subprocess.Popen[bytes]:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(
        [str(executable), *arguments],
        close_fds=True,
        creationflags=creation_flags,
    )


def _request_elevation(arguments: list[str]) -> None:
    parameters = subprocess.list2cmdline(arguments)
    result = ctypes.windll.shell32.ShellExecuteW(
        None, "runas", str(Path(sys.executable).resolve()), parameters, None, 0
    )
    if result <= 32:
        raise OSError(f"update elevation was cancelled or failed ({result})")


def _probe_target_directory(target: Path) -> None:
    probe = target.parent / f".easymotor-update-{uuid.uuid4().hex}.tmp"
    try:
        probe.write_text("probe", encoding="ascii")
    finally:
        probe.unlink(missing_ok=True)


def _retry_file_operation(operation, *, timeout: float = 10.0) -> None:
    """Retry transient Windows sharing violations from the one-file bootloader."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            operation()
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)


def _validate_asset_name(asset_name: str) -> str:
    if not asset_name or Path(asset_name).name != asset_name:
        raise ValueError("update asset name must be a plain filename")
    if "/" in asset_name or "\\" in asset_name:
        raise ValueError("update asset name must not contain a path")
    if OFFICIAL_EXECUTABLE_RE.fullmatch(asset_name) is None:
        raise ValueError("update asset name does not follow the EasyMotor release convention")
    return asset_name


def resolve_install_target(current_target: Path, asset_name: str) -> Path:
    """Use a new official name only when the running EXE has an official name."""
    asset_name = _validate_asset_name(asset_name)
    current_target = current_target.resolve()
    if OFFICIAL_EXECUTABLE_RE.fullmatch(current_target.name):
        return current_target.with_name(asset_name)
    return current_target


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _files_identical(left: Path, right: Path) -> bool:
    return left.stat().st_size == right.stat().st_size and _sha256(left) == _sha256(right)


def _restore_previous_version(
    *,
    current_target: Path,
    install_target: Path,
    backup: Path,
    created_install_target: bool,
) -> None:
    if created_install_target:
        install_target.unlink(missing_ok=True)
    if backup.exists():
        _retry_file_operation(lambda: os.replace(backup, current_target))


def apply_update_from_argv(argv: list[str] | None = None) -> bool:
    """Apply an update in staged-EXE helper mode; return whether it was handled."""
    arguments = list(sys.argv[1:] if argv is None else argv)
    if APPLY_ARGUMENT not in arguments:
        return False
    index = arguments.index(APPLY_ARGUMENT)
    # flag, old PID, current target, install target, asset name, marker, log
    if len(arguments) < index + 7:
        return True
    old_pid = int(arguments[index + 1])
    current_target = Path(arguments[index + 2]).resolve()
    requested_install_target = Path(arguments[index + 3]).resolve()
    asset_name = arguments[index + 4]
    marker = Path(arguments[index + 5]).resolve()
    log_path = Path(arguments[index + 6]).resolve()
    elevated = ELEVATED_ARGUMENT in arguments
    staged = Path(sys.executable).resolve()

    if not _is_under_update_root(marker) or not _is_under_update_root(log_path):
        return True
    try:
        install_target = resolve_install_target(current_target, asset_name)
    except ValueError as exc:
        log_path.write_text(f"Update arguments rejected: {exc}\n", encoding="utf-8")
        return True
    if requested_install_target != install_target or staged.name.casefold() != asset_name.casefold():
        log_path.write_text("Update arguments do not match the validated release asset.\n", encoding="utf-8")
        return True
    if install_target.parent != current_target.parent:
        log_path.write_text("Update target must remain in the current application directory.\n", encoding="utf-8")
        return True

    identifier = marker.stem.removeprefix("health-") or uuid.uuid4().hex
    backup = current_target.with_name(f".{current_target.name}.{identifier}.previous")
    pending = install_target.with_name(f".{install_target.name}.{identifier}.pending")
    log_path.write_text(
        "Staged EasyMotor helper started.\n"
        f"Current: {current_target}\n"
        f"Install: {install_target}\n"
        f"Waiting for process {old_pid}.\n",
        encoding="utf-8",
    )

    if not _wait_for_process(old_pid, EXIT_TIMEOUT_SECONDS):
        _append_log(log_path, f"Old process {old_pid} did not exit; stopping it.")
        os.kill(old_pid, signal.SIGTERM)
        if not _wait_for_process(old_pid, 5):
            _append_log(log_path, "Old process could not be stopped; update aborted.")
            return True

    try:
        _probe_target_directory(install_target)
    except OSError as exc:
        if elevated:
            _append_log(log_path, f"Elevated update cannot write target directory: {exc}")
            _start_process(current_target)
            return True
        try:
            _append_log(log_path, "Requesting administrator permission.")
            _request_elevation(arguments + [ELEVATED_ARGUMENT])
        except OSError as elevation_error:
            _append_log(log_path, f"Update elevation failed: {elevation_error}")
            _start_process(current_target)
        return True

    reuse_existing_target = False
    if install_target != current_target and install_target.exists():
        try:
            identical_target = _files_identical(install_target, staged)
        except OSError as exc:
            _append_log(log_path, f"Update aborted: existing target could not be verified: {exc}")
            _start_process(current_target)
            return True
        if not identical_target:
            _append_log(log_path, f"Update aborted: {install_target.name} already exists with different content.")
            _start_process(current_target)
            return True
        reuse_existing_target = True
        _append_log(log_path, f"Reusing identical existing target: {install_target.name}")

    created_install_target = False
    try:
        pending.unlink(missing_ok=True)
        _retry_file_operation(lambda: os.replace(current_target, backup))
        if not reuse_existing_target:
            _retry_file_operation(lambda: shutil.copy2(staged, pending))
            _retry_file_operation(lambda: os.replace(pending, install_target))
            created_install_target = True
    except OSError as exc:
        pending.unlink(missing_ok=True)
        _restore_previous_version(
            current_target=current_target,
            install_target=install_target,
            backup=backup,
            created_install_target=created_install_target,
        )
        _append_log(log_path, f"Replacement failed; previous version restored: {exc}")
        if current_target.exists():
            _start_process(current_target)
        return True

    process: subprocess.Popen[bytes] | None = None
    try:
        marker.unlink(missing_ok=True)
        process = _start_process(install_target, HEALTH_ARGUMENT, str(marker))
        deadline = time.monotonic() + HEALTH_TIMEOUT_SECONDS
        while time.monotonic() < deadline and process.poll() is None:
            if marker.exists():
                break
            time.sleep(0.25)
        if not marker.exists():
            raise RuntimeError("updated EasyMotor did not report a healthy startup")
        marker.unlink(missing_ok=True)
        _retry_file_operation(lambda: backup.unlink(missing_ok=True))
        _append_log(log_path, f"Update completed successfully: {current_target.name} -> {install_target.name}")
    except Exception as exc:
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        _restore_previous_version(
            current_target=current_target,
            install_target=install_target,
            backup=backup,
            created_install_target=created_install_target,
        )
        _append_log(log_path, f"Updated version failed; previous version restored: {exc}")
        if current_target.exists():
            _start_process(current_target)
    finally:
        pending.unlink(missing_ok=True)
    return True


def launch_update_helper(staged: Path, current_target: Path, asset_name: str) -> Path:
    if os.name != "nt":
        raise OSError("EasyMotor self-update is supported only on Windows")
    staged = staged.resolve(strict=True)
    current_target = current_target.resolve(strict=True)
    install_target = resolve_install_target(current_target, asset_name)
    if staged.name.casefold() != asset_name.casefold():
        raise ValueError("downloaded update filename does not match the release manifest")
    root = update_root()
    root.mkdir(parents=True, exist_ok=True)
    identifier = uuid.uuid4().hex
    marker = root / f"health-{identifier}.ok"
    log_path = root / "last-update.log"
    _start_process(
        staged,
        APPLY_ARGUMENT,
        str(os.getpid()),
        str(current_target),
        str(install_target),
        asset_name,
        str(marker),
        str(log_path),
    )
    return install_target
