"""Low-energy RS04/OpenArmX MIT bench controls."""

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
        "node": "Joint node",
        "apply_id": "Apply ID",
        "save": "Save config",
        "zero": "Set current zero",
        "enable": "Enable / align",
        "disable": "STOP / Disable",
        "position": "Position (rad)",
        "velocity": "Velocity (rad/s)",
        "kp": "Kp (Nm/rad)",
        "kd": "Kd (Nm·s/rad)",
        "torque": "Torque FF (Nm)",
        "send": "Send once",
        "hold": "Hold at 100 Hz",
        "locked": "Torque FF locked until measured calibration",
        "status": "Zero: not set | Calibration: unknown | MIT refresh: idle",
        "need_connection": "Connect and enumerate CAN first.",
        "step": "Target must stay within 0.05 rad of live feedback.",
        "confirm_id": "Change the active motor node ID? Save config only after verifying communication.",
    },
    "zh": {
        "title": "MIT 台架测试（OpenArmX / RS04 Type 1）",
        "node": "关节节点",
        "apply_id": "应用 ID",
        "save": "保存配置",
        "zero": "设置当前位置为零",
        "enable": "使能 / 对齐",
        "disable": "停止 / 失能",
        "position": "位置（rad）",
        "velocity": "速度（rad/s）",
        "kp": "Kp（Nm/rad）",
        "kd": "Kd（Nm·s/rad）",
        "torque": "前馈力矩（Nm）",
        "send": "单次发送",
        "hold": "100 Hz 保持",
        "locked": "完成实测标定前禁止前馈力矩",
        "status": "零位：未设置 | 标定：未知 | MIT 刷新：空闲",
        "need_connection": "请先连接并枚举 CAN 电机。",
        "step": "目标位置必须在实时反馈的 ±0.05 rad 内。",
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
        enable: Callable[[], bool],
        stop: Callable[[], None],
        send_once: Callable[[MitCommand], None],
        start_hold: Callable[[MitCommand], None],
        set_node_id: Callable[[int], None],
        save_configuration: Callable[[], None],
    ) -> None:
        super().__init__(master, padding=10)
        self._language_getter = language_getter
        self._connected_getter = connected_getter
        self._feedback_getter = feedback_getter
        self._set_zero = set_zero
        self._enable = enable
        self._stop = stop
        self._send_once = send_once
        self._start_hold = start_hold
        self._set_node_id = set_node_id
        self._save_configuration = save_configuration
        self.zero_valid = False
        self._zero_requested = False
        self.calibrated: bool | None = None
        self.holding = False
        self.measured_iq: float | None = None

        self.node_var = tk.IntVar(value=1)
        self.position_var = tk.StringVar(value="0.000")
        self.velocity_var = tk.StringVar(value="0.000")
        self.kp_var = tk.StringVar(value="0.0")
        self.kd_var = tk.StringVar(value="0.0")
        self.torque_var = tk.StringVar(value="0.0")
        self.status_var = tk.StringVar()

        self.labels: dict[str, ttk.Label] = {}
        self.buttons: dict[str, ttk.Button] = {}
        self._build()
        self.refresh_language()

    def _build(self) -> None:
        self.labels["node"] = ttk.Label(self)
        self.labels["node"].grid(row=0, column=0, sticky="w")
        ttk.Combobox(
            self, textvariable=self.node_var, values=(1, 2), width=5, state="readonly"
        ).grid(row=0, column=1, padx=(5, 10))
        for column, key, command in (
            (2, "apply_id", self._on_set_id),
            (3, "save", self._on_save),
            (4, "zero", self._on_zero),
            (5, "enable", self._on_enable),
            (6, "disable", self._on_stop),
        ):
            self.buttons[key] = ttk.Button(self, command=command)
            self.buttons[key].grid(row=0, column=column, padx=3)

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
        ttk.Label(self, textvariable=self.status_var).grid(
            row=3, column=4, columnspan=3, sticky="e", pady=(8, 0)
        )
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
        self._render_status()

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
        if self._show_action_error(self._set_zero):
            self.zero_valid = False
            self._zero_requested = True
            self._render_status()

    def _on_enable(self) -> None:
        if self._require_connection():
            self._show_action_error(self._enable)

    def _on_stop(self) -> None:
        self._show_action_error(self._stop)

    def _on_set_id(self) -> None:
        if not self._require_connection():
            return
        if messagebox.askyesno(self._text("title"), self._text("confirm_id"), parent=self):
            self._show_action_error(lambda: self._set_node_id(self.node_var.get()))

    def _on_save(self) -> None:
        if self._require_connection():
            self._show_action_error(self._save_configuration)

    def update_feedback(self, feedback: MotorFeedback) -> None:
        self.node_var.set(feedback.node_id if feedback.node_id in (1, 2) else 1)
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

    def set_measured_iq(self, value: float) -> None:
        self.measured_iq = value
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
            live = f" | p={feedback.position_rad:.4f} v={feedback.velocity_rad_s:.4f} τ={feedback.torque_nm:.3f}"
        if self.measured_iq is not None:
            live += f" iq={self.measured_iq:.3f} A"
        prefixes = ("零位", "标定", "MIT 刷新") if language == "zh" else ("Zero", "Calibration", "MIT refresh")
        self.status_var.set(f"{prefixes[0]}: {zero} | {prefixes[1]}: {calibration} | {prefixes[2]}: {refresh}{live}")
