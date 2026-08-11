"""Minimal Windows PE architecture and version-resource validation."""

from __future__ import annotations

import ctypes
import os
import struct
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExecutableInfo:
    product_name: str
    file_version: str
    product_version: str
    machine: int


def read_pe_machine(path: Path) -> int:
    with path.open("rb") as stream:
        if stream.read(2) != b"MZ":
            raise ValueError("update is not a Windows executable")
        stream.seek(0x3C)
        raw_offset = stream.read(4)
        if len(raw_offset) != 4:
            raise ValueError("update has a truncated DOS header")
        stream.seek(struct.unpack("<I", raw_offset)[0])
        if stream.read(4) != b"PE\0\0":
            raise ValueError("update has an invalid PE header")
        raw_machine = stream.read(2)
        if len(raw_machine) != 2:
            raise ValueError("update has a truncated PE header")
        return struct.unpack("<H", raw_machine)[0]


def _query_string(buffer, block: str) -> str:
    value = ctypes.c_void_p()
    size = wintypes.UINT()
    if not ctypes.windll.version.VerQueryValueW(
        buffer, block, ctypes.byref(value), ctypes.byref(size)
    ):
        return ""
    return ctypes.wstring_at(value, size.value).rstrip("\0")


def read_executable_info(path: Path) -> ExecutableInfo:
    machine = read_pe_machine(path)
    if os.name != "nt":
        raise OSError("Windows version resources can only be checked on Windows")
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        raise ValueError("update executable has no Windows version resource")
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, buffer):
        raise ValueError("unable to read update executable version resource")
    translation_ptr = ctypes.c_void_p()
    translation_size = wintypes.UINT()
    if version.VerQueryValueW(
        buffer, "\\VarFileInfo\\Translation", ctypes.byref(translation_ptr), ctypes.byref(translation_size)
    ) and translation_size.value >= 4:
        language, codepage = struct.unpack("<HH", ctypes.string_at(translation_ptr, 4))
    else:
        language, codepage = 0x0409, 0x04B0
    prefix = f"\\StringFileInfo\\{language:04X}{codepage:04X}\\"
    return ExecutableInfo(
        product_name=_query_string(buffer, prefix + "ProductName"),
        file_version=_query_string(buffer, prefix + "FileVersion"),
        product_version=_query_string(buffer, prefix + "ProductVersion"),
        machine=machine,
    )


def validate_easymotor_executable(path: Path, expected_version: str) -> ExecutableInfo:
    info = read_executable_info(path)
    if info.machine != 0x8664:
        raise ValueError("update executable is not Windows x64")
    if info.product_name != "EasyMotor":
        raise ValueError("update executable ProductName is not EasyMotor")
    expected_windows_version = expected_version + ".0" if expected_version.count(".") == 2 else expected_version
    if info.file_version != expected_windows_version:
        raise ValueError("update executable FileVersion does not match the release")
    if not info.product_version.startswith(expected_version):
        raise ValueError("update executable ProductVersion does not match the release")
    return info
