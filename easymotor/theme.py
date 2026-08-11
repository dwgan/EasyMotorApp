"""EasyMotor visual theme derived from the bundled blue logo."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Final


BACKGROUND: Final = "#F3F7FA"
SURFACE: Final = "#FFFFFF"
SURFACE_ALT: Final = "#E5EEF5"
PRIMARY_DARK: Final = "#112947"
PRIMARY: Final = "#284D76"
PRIMARY_MID: Final = "#50769D"
PRIMARY_SOFT: Final = "#8DA7C0"
PRIMARY_PALE: Final = "#C8DBE7"
TEXT: Final = PRIMARY_DARK
MUTED_TEXT: Final = "#50708E"
DISABLED_TEXT: Final = "#8A9AAA"
WARNING_TEXT: Final = "#806300"
STOP_ACCENT: Final = "#B86E00"
STOP_ACCENT_HOVER: Final = "#D58B19"
STOP_ACCENT_PRESSED: Final = "#754600"

LOG_BACKGROUND: Final = "#0D1D2F"
LOG_FOREGROUND: Final = "#DCE8F2"
LOG_TX: Final = "#7CC4EF"
LOG_EVENT: Final = "#F2C96D"
LOG_ERROR: Final = "#F0A43A"

WAVE_BACKGROUND: Final = "#0A1726"
WAVE_GRID: Final = "#28415D"
WAVE_LABEL: Final = "#AFC4D6"
WAVE_U: Final = "#70B7E6"
WAVE_V: Final = "#74C69D"
WAVE_W: Final = "#B39DDB"


def configure_theme(root: tk.Misc) -> ttk.Style:
    """Configure a consistent logo-derived ttk palette for the application."""
    root.configure(background=BACKGROUND)
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    style.configure(".", font=("Microsoft YaHei UI", 10), foreground=TEXT)
    style.configure("TFrame", background=BACKGROUND)
    style.configure("TLabel", background=BACKGROUND, foreground=TEXT)
    style.configure(
        "TLabelframe",
        background=SURFACE,
        bordercolor=PRIMARY_SOFT,
        lightcolor=PRIMARY_PALE,
        darkcolor=PRIMARY_SOFT,
        relief="solid",
    )
    style.configure(
        "TLabelframe.Label",
        background=SURFACE,
        foreground=PRIMARY,
        font=("Microsoft YaHei UI", 10, "bold"),
    )
    style.configure(
        "TButton",
        background=SURFACE_ALT,
        foreground=TEXT,
        bordercolor=PRIMARY_SOFT,
        lightcolor=SURFACE,
        darkcolor=PRIMARY_SOFT,
        padding=(10, 5),
        focusthickness=1,
        focuscolor=PRIMARY_MID,
    )
    style.map(
        "TButton",
        background=[("pressed", PRIMARY), ("active", PRIMARY_PALE)],
        foreground=[("pressed", SURFACE), ("disabled", DISABLED_TEXT)],
        bordercolor=[("focus", PRIMARY_MID), ("active", PRIMARY_MID)],
    )
    style.configure(
        "DemoAction.TButton",
        background=PRIMARY,
        foreground=SURFACE,
        bordercolor=PRIMARY_DARK,
        font=("Microsoft YaHei UI", 15, "bold"),
        padding=14,
    )
    style.map(
        "DemoAction.TButton",
        background=[("pressed", PRIMARY_DARK), ("active", PRIMARY_MID)],
        foreground=[("disabled", PRIMARY_PALE), ("!disabled", SURFACE)],
    )
    style.configure(
        "Stop.TButton",
        background=STOP_ACCENT,
        foreground=SURFACE,
        bordercolor=STOP_ACCENT,
        font=("Microsoft YaHei UI", 10, "bold"),
    )
    style.map(
        "Stop.TButton",
        background=[
            ("pressed", STOP_ACCENT_PRESSED),
            ("active", STOP_ACCENT_HOVER),
        ],
        foreground=[("disabled", PRIMARY_PALE), ("!disabled", SURFACE)],
    )
    style.configure(
        "DemoStop.TButton",
        background=STOP_ACCENT,
        foreground=SURFACE,
        bordercolor=STOP_ACCENT,
        font=("Microsoft YaHei UI", 16, "bold"),
        padding=14,
    )
    style.map(
        "DemoStop.TButton",
        background=[
            ("pressed", STOP_ACCENT_PRESSED),
            ("active", STOP_ACCENT_HOVER),
        ],
        foreground=[("disabled", PRIMARY_PALE), ("!disabled", SURFACE)],
    )
    for widget_style in ("TCheckbutton", "TRadiobutton"):
        style.configure(
            widget_style,
            background=BACKGROUND,
            foreground=TEXT,
            indicatorcolor=SURFACE,
        )
        style.map(
            widget_style,
            background=[("active", BACKGROUND)],
            foreground=[("disabled", DISABLED_TEXT)],
            indicatorcolor=[
                ("selected", PRIMARY),
                ("pressed", PRIMARY_DARK),
                ("!selected", SURFACE),
            ],
        )
    for field_style in ("TEntry", "TCombobox", "TSpinbox"):
        style.configure(
            field_style,
            fieldbackground=SURFACE,
            foreground=TEXT,
            bordercolor=PRIMARY_SOFT,
            lightcolor=PRIMARY_PALE,
            darkcolor=PRIMARY_SOFT,
            insertcolor=TEXT,
        )
        style.map(
            field_style,
            fieldbackground=[("readonly", SURFACE), ("disabled", SURFACE_ALT)],
            foreground=[("disabled", DISABLED_TEXT)],
            bordercolor=[("focus", PRIMARY_MID)],
        )
    style.configure("TNotebook", background=BACKGROUND, borderwidth=0)
    style.configure(
        "TNotebook.Tab",
        background=SURFACE_ALT,
        foreground=TEXT,
        bordercolor=PRIMARY_SOFT,
        borderwidth=1,
        font=("Microsoft YaHei UI", 10, "bold"),
        padding=(14, 7),
        focusthickness=0,
    )
    # Clam inserts a Notebook.focus element around the tab label. The selected
    # fill already communicates focus, so omit that dotted black text outline.
    style.layout(
        "TNotebook.Tab",
        [
            (
                "Notebook.tab",
                {
                    "sticky": "nswe",
                    "children": [
                        (
                            "Notebook.padding",
                            {
                                "side": "top",
                                "sticky": "nswe",
                                "children": [
                                    (
                                        "Notebook.label",
                                        {"side": "top", "sticky": ""},
                                    )
                                ],
                            },
                        )
                    ],
                },
            )
        ],
    )
    style.map(
        "TNotebook.Tab",
        background=[("selected", PRIMARY), ("active", PRIMARY_PALE)],
        foreground=[("selected", SURFACE)],
        padding=[("selected", (14, 7)), ("!selected", (14, 7))],
        borderwidth=[("selected", 1), ("!selected", 1)],
    )
    style.configure(
        "Horizontal.TProgressbar",
        background=PRIMARY,
        troughcolor=SURFACE_ALT,
        bordercolor=PRIMARY_SOFT,
        lightcolor=PRIMARY_MID,
        darkcolor=PRIMARY_DARK,
    )
    style.configure(
        "TScrollbar",
        background=PRIMARY_PALE,
        troughcolor=SURFACE_ALT,
        bordercolor=PRIMARY_SOFT,
        arrowcolor=PRIMARY_DARK,
    )
    style.map("TScrollbar", background=[("active", PRIMARY_SOFT)])
    return style


def apply_window_surface(window: tk.Misc) -> None:
    """Use the theme background behind ttk content in a Toplevel."""
    try:
        window.configure(background=BACKGROUND)
    except tk.TclError:
        pass
