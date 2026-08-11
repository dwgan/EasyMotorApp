"""EasyMotor desktop identity shared by the main and auxiliary windows."""

from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from pathlib import Path
from typing import Final


APP_USER_MODEL_ID: Final = "EasyMotor.Desktop"
APP_ICON_PATH: Final = Path(__file__).resolve().parents[1] / "favicon.ico"


def configure_windows_app_id() -> bool:
    """Give the Tk process an EasyMotor taskbar identity on Windows."""
    if sys.platform != "win32":
        return False
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except (AttributeError, OSError):
        return False
    return True


def apply_window_icon(window: tk.Misc, *, set_default: bool = False) -> bool:
    """Apply the bundled ICO to one window and optionally all future Toplevels."""
    if not APP_ICON_PATH.is_file():
        return False
    icon_path = str(APP_ICON_PATH)
    try:
        window.iconbitmap(icon_path)
        if set_default:
            window.iconbitmap(default=icon_path)
    except (tk.TclError, OSError):
        return False
    return True
