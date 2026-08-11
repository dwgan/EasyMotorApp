"""Beginner-facing, bilingual EasyMotor demonstration page."""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable, Iterable
from tkinter import ttk

from easymotor.core.safety_policy import (
    DEMO_DEFAULT_DURATION_MS,
    DEMO_SPEED_PRESETS_RPM,
)
from easymotor.i18n import tr
from easymotor.theme import WARNING_TEXT


class DemoView(ttk.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        port_var: tk.StringVar,
        connection_var: tk.StringVar,
        interface_var: tk.StringVar,
        language_var: tk.StringVar,
        on_refresh: Callable[[], None],
        on_toggle_connection: Callable[[], None],
        on_interface_changed: Callable[[], None],
        on_language_changed: Callable[[], None],
        on_run: Callable[[int, int, bool], None],
        on_stop: Callable[[], None],
        on_engineer_mode: Callable[[], None],
    ) -> None:
        super().__init__(master, padding=24)
        self.port_var = port_var
        self.connection_var = connection_var
        self.interface_var = interface_var
        self.language_var = language_var
        self._on_refresh = on_refresh
        self._on_toggle_connection = on_toggle_connection
        self._on_interface_changed = on_interface_changed
        self._on_language_changed = on_language_changed
        self._on_run = on_run
        self._on_stop = on_stop
        self._on_engineer_mode = on_engineer_mode
        self.speed_var = tk.IntVar(value=DEMO_SPEED_PRESETS_RPM[0])
        self.continuous_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="")
        self._ports: tuple[str, ...] = ()

        style = ttk.Style(self)
        style.configure("DemoAction.TButton", font=("Microsoft YaHei UI", 15), padding=14)
        style.configure("DemoStop.TButton", font=("Microsoft YaHei UI", 16, "bold"), padding=14)
        self.rebuild()

    @property
    def language(self) -> str:
        return self.language_var.get()

    def rebuild(self) -> None:
        for child in self.winfo_children():
            child.destroy()
        language = self.language

        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        ttk.Label(
            header, text=tr(language, "demo_title"), font=("Microsoft YaHei UI", 22, "bold")
        ).pack(side=tk.LEFT)
        ttk.Button(
            header, text=tr(language, "engineer_mode"), command=self._on_engineer_mode
        ).pack(side=tk.RIGHT)
        language_combo = ttk.Combobox(
            header,
            width=9,
            state="readonly",
            values=(tr(language, "english"), tr(language, "chinese")),
        )
        language_combo.current(0 if language == "en" else 1)
        language_combo.pack(side=tk.RIGHT, padx=(0, 10))
        language_combo.bind("<<ComboboxSelected>>", lambda _event: self._select_language(language_combo.current()))
        ttk.Label(header, text=tr(language, "language")).pack(side=tk.RIGHT, padx=(0, 6))

        connection = ttk.LabelFrame(self, text=tr(language, "connection"), padding=14)
        connection.pack(fill=tk.X, pady=(22, 0))
        ttk.Label(connection, text=tr(language, "interface")).grid(row=0, column=0, padx=(0, 8))
        self.can_button = ttk.Radiobutton(
            connection,
            text=tr(language, "can"),
            variable=self.interface_var,
            value="can",
            command=self._on_interface_changed,
        )
        self.can_button.grid(row=0, column=1, padx=(0, 12), sticky="w")
        self.rs485_button = ttk.Radiobutton(
            connection,
            text=tr(language, "rs485"),
            variable=self.interface_var,
            value="rs485",
            command=self._on_interface_changed,
        )
        self.rs485_button.grid(row=0, column=2, padx=(0, 18), sticky="w")
        ttk.Label(connection, text=tr(language, "port")).grid(row=0, column=3, padx=(0, 8))
        self.port_combo = ttk.Combobox(
            connection, textvariable=self.port_var, width=14, state="readonly", values=self._ports
        )
        self.port_combo.grid(row=0, column=4, padx=(0, 8))
        self.refresh_button = ttk.Button(connection, text=tr(language, "refresh"), command=self._on_refresh)
        self.refresh_button.grid(row=0, column=5, padx=(0, 12))
        self.connect_button = ttk.Button(
            connection, text=tr(language, "connect"), command=self._on_toggle_connection
        )
        self.connect_button.grid(row=0, column=6, padx=(0, 14))
        ttk.Label(connection, textvariable=self.connection_var).grid(row=1, column=0, columnspan=7, pady=(10, 0), sticky="w")
        connection.columnconfigure(6, weight=1)

        status = ttk.LabelFrame(self, text=tr(language, "motor_status"), padding=18)
        status.pack(fill=tk.X, pady=(18, 0))
        ttk.Label(status, textvariable=self.status_var, font=("Microsoft YaHei UI", 16)).pack(anchor="center")

        control = ttk.LabelFrame(self, text=tr(language, "demo_control"), padding=20)
        control.pack(fill=tk.BOTH, expand=True, pady=(18, 0))
        ttk.Label(control, text=tr(language, "choose_speed"), font=("Microsoft YaHei UI", 12)).pack()
        speeds = ttk.Frame(control)
        speeds.pack(pady=(12, 18))
        captions = {5: "speed_low", 10: "speed_mid", 20: "speed_high"}
        self.speed_buttons = []
        for speed in DEMO_SPEED_PRESETS_RPM:
            button = ttk.Radiobutton(
                speeds, text=tr(language, captions[speed]), variable=self.speed_var, value=speed
            )
            button.pack(side=tk.LEFT, padx=14)
            self.speed_buttons.append(button)

        buttons = ttk.Frame(control)
        buttons.pack(fill=tk.X, pady=(4, 14))
        self.forward_button = ttk.Button(
            buttons, text=tr(language, "forward"), style="DemoAction.TButton", command=lambda: self._request_run(1)
        )
        self.forward_button.grid(row=0, column=0, padx=8, sticky="ew")
        self.stop_button = ttk.Button(
            buttons, text=tr(language, "stop"), style="DemoStop.TButton", command=self._on_stop
        )
        self.stop_button.grid(row=0, column=1, padx=8, sticky="ew")
        self.reverse_button = ttk.Button(
            buttons, text=tr(language, "reverse"), style="DemoAction.TButton", command=lambda: self._request_run(-1)
        )
        self.reverse_button.grid(row=0, column=2, padx=8, sticky="ew")
        for column in range(3):
            buttons.columnconfigure(column, weight=1)

        self.continuous_check = ttk.Checkbutton(
            control, text=tr(language, "continuous"), variable=self.continuous_var
        )
        self.continuous_check.pack()
        ttk.Label(
            control,
            text=tr(language, "duration_note", seconds=DEMO_DEFAULT_DURATION_MS // 1000)
            + " "
            + tr(language, "change_note"),
            foreground=WARNING_TEXT,
        ).pack(pady=(12, 0))
        ttk.Label(self, text=tr(language, "safety_note"), foreground=WARNING_TEXT).pack(
            pady=(18, 0)
        )

    def _select_language(self, selected: int) -> None:
        self.language_var.set("en" if selected == 0 else "zh_CN")
        self._on_language_changed()

    def _request_run(self, direction: int) -> None:
        self._on_run(direction, int(self.speed_var.get()), bool(self.continuous_var.get()))

    def set_ports(self, ports: Iterable[str]) -> None:
        self._ports = tuple(ports)
        self.port_combo["values"] = self._ports

    def render(
        self,
        *,
        connected: bool,
        status_text: str,
        run_enabled: bool,
        stop_enabled: bool,
        settings_enabled: bool,
    ) -> None:
        self.connect_button.configure(text=tr(self.language, "disconnect" if connected else "connect"))
        connect_state = tk.NORMAL if settings_enabled or connected else tk.DISABLED
        self.connect_button.configure(state=connect_state)
        settings_state = tk.NORMAL if settings_enabled and not connected else tk.DISABLED
        self.refresh_button.configure(state=settings_state)
        self.port_combo.configure(state="readonly" if settings_state == tk.NORMAL else tk.DISABLED)
        self.can_button.configure(state=settings_state)
        self.rs485_button.configure(state=settings_state)
        self.status_var.set(status_text)
        run_state = tk.NORMAL if run_enabled else tk.DISABLED
        self.forward_button.configure(state=run_state)
        self.reverse_button.configure(state=run_state)
        self.stop_button.configure(state=tk.NORMAL if stop_enabled else tk.DISABLED)
        for button in self.speed_buttons:
            button.configure(state=run_state)
        self.continuous_check.configure(state=run_state)

    def reset_continuous(self) -> None:
        self.continuous_var.set(False)
