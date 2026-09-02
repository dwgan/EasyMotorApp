"""RS04/OpenArmX MIT bench controls."""

from __future__ import annotations

import math
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Callable

from easymotor.protocols.can_motor import (
    MIT_MAX_POSITION_STEP_RAD,
    MitCommand,
    MotorFeedback,
)


TEXT = {
    "en": {
        "title": "MIT Bench Test (OpenArmX / RS04 Type 1)",
        "zero": "Set current zero",
        "enable": "Enable / align",
        "disable": "STOP / Disable",
        "position": "Position (rad)",
        "velocity": "Velocity (rad/s)",
        "kp": "Kp (Nm/rad)",
        "kd": "Kd (Nm·s/rad)",
        "torque": "Torque FF (Nm)",
        "send": "Single Pulse (200 ms)",
        "hold": "Hold at 100 Hz",
        "maintenance": "Show Maintenance",
        "read_alignment": "Read Alignment Status",
        "save_alignment": "Save Rotor Alignment",
        "locked": "Torque FF locked until measured calibration",
        "status": "Zero: not set | Calibration: unknown | MIT refresh: idle",
        "need_connection": "Connect and enumerate CAN first.",
        "step": "Target position is too far from live feedback.",
        "confirm_id": "Change the active motor node ID? Save config only after verifying communication.",
    },
    "zh": {
        "title": "MIT 台架测试（OpenArmX / RS04 Type 1）",
        "zero": "设置当前位置为零",
        "enable": "使能 / 对齐",
        "disable": "停止 / 失能",
        "position": "位置（rad）",
        "velocity": "速度（rad/s）",
        "kp": "Kp（Nm/rad）",
        "kd": "Kd（Nm·s/rad）",
        "torque": "前馈力矩（Nm）",
        "send": "单次脉冲（200 ms）",
        "hold": "100 Hz 保持",
        "maintenance": "显示维护操作",
        "read_alignment": "读取对齐状态",
        "save_alignment": "保存转子对齐",
        "locked": "完成实测标定前禁止前馈力矩",
        "status": "零位：未设置 | 标定：未知 | MIT 刷新：空闲",
        "need_connection": "请先连接并枚举 CAN 电机。",
        "step": "目标位置离实时反馈太远。",
        "confirm_id": "确定修改当前电机节点 ID？验证通信后再保存配置。",
    },
}


class MitBenchPanel(ttk.LabelFrame):
    def __init__(
        self,
        master,
        *,
        language_getter: Callable[[], str],
        connected_getter: Callable[[], bool],
        feedback_getter: Callable[[], MotorFeedback | None],
        set_zero: Callable[[], None],
        read_alignment: Callable[[], None],
        save_alignment: Callable[[], None],
        enable: Callable[[], bool],
        stop: Callable[[], None],
        send_once: Callable[[MitCommand], None],
        start_hold: Callable[[MitCommand], None],
    ) -> None:
        super().__init__(master, padding=10)
        self._language_getter = language_getter
        self._connected_getter = connected_getter
        self._feedback_getter = feedback_getter
        self._set_zero = set_zero
        self._read_alignment = read_alignment
        self._save_alignment = save_alignment
        self._enable = enable
        self._stop = stop
        self._send_once = send_once
        self._start_hold = start_hold
        self.zero_valid = False
        self._zero_requested = False
        self.calibrated: bool | None = None
        self.alignment_valid: bool | None = None
        self.holding = False
        self.measured_iq: float | None = None

        self.position_var = tk.StringVar(value="0.000")
        self.velocity_var = tk.StringVar(value="0.000")
        self.kp_var = tk.StringVar(value="0.0")
        self.kd_var = tk.StringVar(value="0.0")
        self.torque_var = tk.StringVar(value="0.0")
        self.status_var = tk.StringVar()
        self.alignment_status_var = tk.StringVar()
        self.maintenance_var = tk.BooleanVar(value=False)

        self.labels: dict[str, ttk.Label] = {}
        self.buttons: dict[str, ttk.Button] = {}
        self._build()
        self.refresh_language()

    def _build(self) -> None:
        for column, key, command in (
            (0, "enable", self._on_enable),
            (1, "disable", self._on_stop),
        ):
            self.buttons[key] = ttk.Button(self, command=command, width=16)
            self.buttons[key].grid(row=0, column=column, padx=4)

        fields = (
            ("position", self.position_var),
            ("velocity", self.velocity_var),
            ("kp", self.kp_var),
            ("kd", self.kd_var),
            ("torque", self.torque_var),
        )
        for column, (key, variable) in enumerate(fields):
            self.labels[key] = ttk.Label(self)
            self.labels[key].grid(row=1, column=column, sticky="w", pady=(10, 2))
            ttk.Entry(self, textvariable=variable, width=16).grid(
                row=2, column=column, padx=(0, 8), sticky="ew"
            )
        self.buttons["send"] = ttk.Button(self, command=self._on_send_once)
        self.buttons["send"].grid(row=2, column=5, padx=3)
        self.buttons["hold"] = ttk.Button(self, command=self._on_hold)
        self.buttons["hold"].grid(row=2, column=6, padx=3)
        self.labels["locked"] = ttk.Label(self, foreground="#9A6700")
        self.labels["locked"].grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        ttk.Label(
            self,
            textvariable=self.status_var,
            anchor="w",
            font=("Consolas", 10),
        ).grid(
            row=4, column=0, columnspan=7, sticky="w", pady=(6, 0)
        )
        self.maintenance_check = ttk.Checkbutton(
            self,
            variable=self.maintenance_var,
            command=self._toggle_maintenance,
        )
        self.maintenance_check.grid(
            row=5, column=0, columnspan=7, sticky="w", pady=(10, 0)
        )
        self.maintenance_body = ttk.Frame(self, padding=(0, 8, 0, 0))
        self.buttons["zero"] = ttk.Button(
            self.maintenance_body, command=self._on_zero, width=18
        )
        self.buttons["zero"].grid(row=0, column=0, padx=(0, 6))
        self.buttons["read_alignment"] = ttk.Button(
            self.maintenance_body, command=self._on_read_alignment, width=20
        )
        self.buttons["read_alignment"].grid(row=0, column=1, padx=6)
        self.buttons["save_alignment"] = ttk.Button(
            self.maintenance_body, command=self._on_save_alignment, width=20
        )
        self.buttons["save_alignment"].grid(row=0, column=2, padx=6)
        ttk.Label(
            self.maintenance_body,
            textvariable=self.alignment_status_var,
            width=36,
            anchor="w",
            font=("Consolas", 10),
        ).grid(row=0, column=3, padx=(12, 0), sticky="w")
        for column in range(5):
            self.columnconfigure(column, weight=1)

    def _text(self, key: str) -> str:
        language = "zh" if self._language_getter().lower().startswith("zh") else "en"
        return TEXT[language][key]

    def refresh_language(self) -> None:
        self.configure(text=self._text("title"))
        for key, label in self.labels.items():
            label.configure(text=self._text(key))
        for key, button in self.buttons.items():
            button.configure(text=self._text(key))
        self.maintenance_check.configure(text=self._text("maintenance"))
        self._render_status()

    def _toggle_maintenance(self) -> None:
        if self.maintenance_var.get():
            self.maintenance_body.grid(
                row=6, column=0, columnspan=7, sticky="w"
            )
        else:
            self.maintenance_body.grid_forget()

    def _require_connection(self) -> bool:
        if self._connected_getter():
            return True
        messagebox.showwarning(self._text("title"), self._text("need_connection"), parent=self)
        return False

    def _command(self) -> MitCommand:
        command = MitCommand(
            position_rad=float(self.position_var.get()),
            velocity_rad_s=float(self.velocity_var.get()),
            kp=float(self.kp_var.get()),
            kd=float(self.kd_var.get()),
            torque_nm=float(self.torque_var.get()),
        )
        if not all(math.isfinite(value) for value in command.__dict__.values()):
            raise ValueError("MIT values must be finite")
        feedback = self._feedback_getter()
        if feedback is None or abs(command.position_rad - feedback.position_rad) > MIT_MAX_POSITION_STEP_RAD:
            raise ValueError(self._text("step"))
        return command

    def _show_action_error(self, callback: Callable[[], object]) -> bool:
        try:
            callback()
            return True
        except (ValueError, RuntimeError, OSError) as exc:
            messagebox.showerror(self._text("title"), str(exc), parent=self)
            return False

    def _run_command(self, callback: Callable[[MitCommand], None]) -> bool:
        if not self._require_connection():
            return False
        return self._show_action_error(lambda: callback(self._command()))

    def _on_send_once(self) -> None:
        self._run_command(self._send_once)

    def _on_hold(self) -> None:
        if self._run_command(self._start_hold):
            self.holding = True
            self._render_status()

    def _on_zero(self) -> None:
        if not self._require_connection():
            return
        # Arm the acknowledgement gate before sending Type 6.  Some adapters
        # return the firmware's immediate Type 2 response quickly enough for
        # it to be dispatched while the send callback is still unwinding.
        self.zero_valid = False
        self._zero_requested = True
        self._render_status()
        if not self._show_action_error(self._set_zero):
            self._zero_requested = False
            self._render_status()

    def _on_read_alignment(self) -> None:
        if self._require_connection():
            self._show_action_error(self._read_alignment)

    def _on_save_alignment(self) -> None:
        if self._require_connection():
            self._show_action_error(self._save_alignment)

    def _on_enable(self) -> None:
        if self._require_connection():
            self._show_action_error(self._enable)

    def _on_stop(self) -> None:
        self._show_action_error(self._stop)

    def update_feedback(self, feedback: MotorFeedback) -> None:
        if (
            self._zero_requested
            and abs(feedback.position_rad) <= 0.01
            and feedback.mode == 0
        ):
            self.zero_valid = True
            self._zero_requested = False
        self._render_status(feedback)

    def set_calibrated(self, value: bool) -> None:
        self.calibrated = value
        self.torque_var.set("0.0")
        self._render_status()

    def set_zero_valid(self, value: bool) -> None:
        self.zero_valid = bool(value)
        if self.zero_valid:
            self._zero_requested = False
        self._render_status()

    def set_measured_iq(self, value: float) -> None:
        self.measured_iq = value
        self._render_status()

    def set_alignment_valid(self, value: bool | None) -> None:
        self.alignment_valid = value
        self._render_status()

    def on_stop_or_disconnect(self) -> None:
        self.holding = False
        self._render_status()

    def _render_status(self, feedback: MotorFeedback | None = None) -> None:
        language = "zh" if self._language_getter().lower().startswith("zh") else "en"
        zero = ("有效" if self.zero_valid else "未设置") if language == "zh" else ("valid" if self.zero_valid else "not set")
        if self.calibrated is None:
            calibration = "未知" if language == "zh" else "unknown"
        else:
            calibration = ("有效" if self.calibrated else "未标定") if language == "zh" else ("valid" if self.calibrated else "not calibrated")
        refresh = ("运行" if self.holding else "空闲") if language == "zh" else ("active" if self.holding else "idle")
        live = ""
        if feedback is not None:
            live = (
                f" | p={feedback.position_rad: .4f} "
                f"v={feedback.velocity_rad_s: .4f} "
                f"τ={feedback.torque_nm: .3f}"
            )
        if self.measured_iq is not None:
            live += f" iq={self.measured_iq: .3f} A"
        prefixes = ("零位", "标定", "MIT 刷新") if language == "zh" else ("Zero", "Calibration", "MIT refresh")
        self.status_var.set(f"{prefixes[0]}: {zero} | {prefixes[1]}: {calibration} | {prefixes[2]}: {refresh}{live}")
        if self.alignment_valid is None:
            alignment = "未知" if language == "zh" else "unknown"
        elif self.alignment_valid:
            alignment = "有效，可保存" if language == "zh" else "valid; ready to save"
        else:
            alignment = "无效，需要对齐" if language == "zh" else "invalid; alignment required"
        prefix = "转子对齐" if language == "zh" else "Rotor alignment"
        self.alignment_status_var.set(f"{prefix}: {alignment}")
