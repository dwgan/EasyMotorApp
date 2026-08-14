"""CAN parameter controls sharing the application's primary USB-CAN link."""

from __future__ import annotations

import csv
import time
import tkinter as tk
from collections.abc import Callable
from datetime import datetime
from tkinter import filedialog, messagebox as tk_messagebox, ttk

from easymotor.i18n import LocalizedMessageBox, LocalizedStringVar, localize_legacy
from easymotor.protocols.can_motor import (
    CanFrame,
    PARAMETERS,
    PARAMETER_BY_INDEX,
    build_parameter_read,
    build_parameter_write,
    build_rejection_probe,
    parse_parameter_response,
    split_id,
)
from easymotor.services.endurance_service import LongRunSession
from easymotor.theme import WARNING_TEXT


messagebox = LocalizedMessageBox(tk_messagebox)


class CanParameterPanel(ttk.Frame):
    """Safe parameter UI using the already connected motion CAN transport."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        language_var: tk.StringVar,
        send_frame: Callable[[CanFrame], None],
        connected_getter: Callable[[], bool],
        idle_getter: Callable[[], bool],
        log_callback: Callable[[str, str], None],
        state_callback: Callable[[], None],
        value_callback: Callable[[int, int | float], None] | None = None,
        node_id: int = 0x7F,
        host_id: int = 0xFD,
    ) -> None:
        super().__init__(master)
        self.language_var = language_var
        messagebox.set_language_getter(language_var.get)
        self._send_frame = send_frame
        self._connected_getter = connected_getter
        self._idle_getter = idle_getter
        self._log = log_callback
        self._state_changed = state_callback
        self._value_callback = value_callback
        self.node_id = node_id
        self.host_id = host_id
        self.pending_verification: dict[int, int | float] = {}
        self.pending_rejection: dict[int, str] = {}
        self._verify_after_id: str | None = None
        self._long_run_after_id: str | None = None
        self.long_run = LongRunSession()

        localized_var = lambda value="": LocalizedStringVar(
            self, self.language_var.get, value
        )
        self.expanded_var = tk.BooleanVar(value=False)
        self.validation_var = tk.BooleanVar(value=False)
        self.parameter_var = tk.StringVar()
        self.value_var = tk.StringVar()
        self.parameter_help_var = localized_var()
        self.last_value_var = localized_var("当前值：尚未读取")
        self.long_run_minutes_var = tk.IntVar(value=60)
        self.long_run_status_var = localized_var("长稳状态：未开始")
        self._build_ui()
        self._on_parameter_selected()
        self.refresh_language()

    def _build_ui(self) -> None:
        header = ttk.Frame(self)
        header.pack(fill=tk.X)
        ttk.Checkbutton(
            header,
            text="显示 CAN 参数",
            variable=self.expanded_var,
            command=self._toggle_expanded,
        ).pack(side=tk.LEFT)
        ttk.Label(
            header,
            text="复用当前 CAN Control 连接；运动期间所有参数操作锁定。",
            foreground=WARNING_TEXT,
        ).pack(side=tk.LEFT, padx=(12, 0))

        self.body = ttk.Frame(self, padding=(0, 8, 0, 0))
        parameter_frame = ttk.LabelFrame(
            self.body, text="参数读取与受限写入（Type 17 / 18）", padding=10
        )
        parameter_frame.pack(fill=tk.X)
        ttk.Label(parameter_frame, text="参数").grid(row=0, column=0, sticky="w")
        self.parameter_combo = ttk.Combobox(
            parameter_frame,
            textvariable=self.parameter_var,
            width=31,
            height=12,
            state="readonly",
        )
        self.parameter_combo.grid(row=0, column=1, padx=(6, 8), sticky="ew")
        self.parameter_combo.bind("<<ComboboxSelected>>", self._on_parameter_selected)
        self.read_button = ttk.Button(
            parameter_frame, text="读取", command=self.read_parameter
        )
        self.read_button.grid(row=0, column=2, padx=4)
        ttk.Label(parameter_frame, text="写入值").grid(
            row=0, column=3, padx=(20, 4)
        )
        self.value_entry = ttk.Entry(
            parameter_frame, textvariable=self.value_var, width=14
        )
        self.value_entry.grid(row=0, column=4, padx=4)
        self.write_button = ttk.Button(
            parameter_frame, text="安全写入并回读", command=self.write_parameter
        )
        self.write_button.grid(row=0, column=5, padx=(4, 0))

        quick_reads = ttk.Frame(parameter_frame)
        quick_reads.grid(row=1, column=0, columnspan=6, pady=(8, 0), sticky="w")
        for column, (index, caption) in enumerate(
            (
                (0x7005, "读 run_mode"),
                (0x7019, "读 mechPos"),
                (0x7026, "读 EPScan_time"),
                (0x7028, "读 cantimeout"),
            )
        ):
            ttk.Button(
                quick_reads,
                text=caption,
                command=lambda selected=index: self.read_parameter_index(selected),
            ).grid(row=0, column=column, padx=(0, 6))
        ttk.Label(parameter_frame, textvariable=self.parameter_help_var).grid(
            row=2, column=0, columnspan=6, pady=(8, 0), sticky="w"
        )
        ttk.Label(parameter_frame, textvariable=self.last_value_var).grid(
            row=3, column=0, columnspan=6, pady=(5, 0), sticky="w"
        )
        parameter_frame.columnconfigure(1, weight=1)

        validation_bar = ttk.Frame(self.body)
        validation_bar.pack(fill=tk.X, pady=(8, 0))
        ttk.Checkbutton(
            validation_bar,
            text="显示 Validation Tools",
            variable=self.validation_var,
            command=self._toggle_validation,
        ).pack(side=tk.LEFT)
        ttk.Label(
            validation_bar,
            text="参数拒绝测试和只读长稳默认隐藏。",
        ).pack(side=tk.LEFT, padx=(12, 0))

        self.validation_body = ttk.Frame(self.body, padding=(0, 8, 0, 0))
        rejection = ttk.LabelFrame(
            self.validation_body, text="固件参数拒绝路径", padding=10
        )
        rejection.pack(fill=tk.X)
        for column, (name, caption) in enumerate(
            (
                ("readonly_mechpos", "测试只读 mechPos"),
                ("epscan_below_min", "测试 EPScan_time=0"),
                ("cantimeout_gap", "测试 cantimeout=19"),
            )
        ):
            ttk.Button(
                rejection,
                text=caption,
                command=lambda selected=name: self.send_rejection_probe(selected),
            ).grid(row=0, column=column, padx=(0, 8))

        endurance = ttk.LabelFrame(
            self.validation_body, text="只读 CAN 长稳（Type 17）", padding=10
        )
        endurance.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(endurance, text="时长（分钟）").grid(row=0, column=0)
        self.long_run_minutes = ttk.Spinbox(
            endurance,
            from_=1,
            to=1440,
            textvariable=self.long_run_minutes_var,
            width=7,
            justify="center",
        )
        self.long_run_minutes.grid(row=0, column=1, padx=(6, 14))
        self.long_run_start_button = ttk.Button(
            endurance, text="开始长稳", command=self.start_long_run
        )
        self.long_run_start_button.grid(row=0, column=2, padx=(0, 6))
        self.long_run_stop_button = ttk.Button(
            endurance, text="停止", command=self.stop_long_run, state=tk.DISABLED
        )
        self.long_run_stop_button.grid(row=0, column=3, padx=(0, 6))
        ttk.Button(
            endurance, text="导出 CSV", command=self.export_long_run_csv
        ).grid(row=0, column=4)
        ttk.Label(endurance, textvariable=self.long_run_status_var).grid(
            row=1, column=0, columnspan=5, pady=(8, 0), sticky="w"
        )

    def _ui(self, text: object) -> str:
        return localize_legacy(text, self.language_var.get())

    def refresh_language(self) -> None:
        for value in vars(self).values():
            if isinstance(value, LocalizedStringVar):
                value.refresh_language()

        def visit(widget: tk.Misc) -> None:
            try:
                current = str(widget.cget("text"))
            except tk.TclError:
                current = ""
            if not hasattr(widget, "_easymotor_source_text"):
                widget._easymotor_source_text = current
            source = widget._easymotor_source_text
            if source:
                try:
                    widget.configure(text=self._ui(source))
                except tk.TclError:
                    pass
            for child in widget.winfo_children():
                visit(child)

        visit(self)
        selected = self.parameter_var.get()
        selected_index = selected[2:6] if selected.startswith("0x") else None
        labels = [self._parameter_label(item.index) for item in PARAMETERS]
        self.parameter_combo.configure(values=labels)
        if selected_index:
            match = next((item for item in labels if item[2:6] == selected_index), None)
            if match:
                self.parameter_var.set(match)
        elif labels:
            self.parameter_var.set(labels[0])
        self._on_parameter_selected()

    def _toggle_expanded(self) -> None:
        if self.expanded_var.get():
            self.body.pack(fill=tk.X)
        else:
            self.body.pack_forget()

    def _toggle_validation(self) -> None:
        if self.validation_var.get():
            self.validation_body.pack(fill=tk.X)
        else:
            self.validation_body.pack_forget()

    def _parameter_label(self, index: int) -> str:
        parameter = PARAMETER_BY_INDEX[index]
        access = "安全可写" if parameter.writable else "只读"
        return f"0x{index:04X}  {parameter.name}  [{parameter.kind}, {self._ui(access)}]"

    def _selected_parameter(self):
        text = self.parameter_var.get().strip()
        if not text.startswith("0x"):
            raise ValueError("请选择参数")
        return PARAMETER_BY_INDEX[int(text[2:6], 16)]

    def _on_parameter_selected(self, _event=None) -> None:
        try:
            parameter = self._selected_parameter()
        except (ValueError, KeyError):
            self.parameter_help_var.set("请选择一个参数")
            return
        if not parameter.writable:
            self.parameter_help_var.set("当前固件按只读处理。")
        else:
            bounds = f"允许范围：{parameter.minimum}..{parameter.maximum}"
            if parameter.allowed_values:
                bounds += "，另允许 " + ", ".join(
                    str(value) for value in parameter.allowed_values
                )
            self.parameter_help_var.set(bounds + "；电机必须处于 IDLE/RESET。")
        self.refresh_state()

    def refresh_state(self) -> None:
        connected = self._connected_getter()
        idle = connected and self._idle_getter() and not self.long_run.running
        selected = None
        try:
            selected = self._selected_parameter()
        except (ValueError, KeyError):
            pass
        self.read_button.configure(state=tk.NORMAL if idle else tk.DISABLED)
        writable = bool(idle and selected is not None and selected.writable)
        self.write_button.configure(state=tk.NORMAL if writable else tk.DISABLED)
        self.value_entry.configure(state=tk.NORMAL if writable else tk.DISABLED)

    @property
    def busy(self) -> bool:
        return bool(
            self.long_run.running
            or self.pending_verification
            or self.pending_rejection
        )

    def _require_idle(self) -> bool:
        if not self._connected_getter():
            messagebox.showwarning(
                "CAN 未连接", "请先在 CAN Control 连接并枚举电机。", parent=self
            )
            return False
        if not self._idle_getter():
            messagebox.showwarning(
                "参数操作已锁定",
                "电机运动、启动或停止期间不能进行参数操作；请等待电机进入 IDLE。",
                parent=self,
            )
            return False
        if self.long_run.running:
            messagebox.showwarning(
                "长稳测试正在运行", "请先停止长稳测试。", parent=self
            )
            return False
        return True

    def _send(self, frame: CanFrame) -> bool:
        try:
            self._send_frame(frame)
            return True
        except (OSError, RuntimeError, ValueError) as exc:
            messagebox.showerror("发送失败", str(exc), parent=self)
            self._log("error", f"CAN parameter send failed: {exc}\n")
            return False

    def read_parameter(self) -> None:
        if not self._require_idle():
            return
        try:
            parameter = self._selected_parameter()
            frame = build_parameter_read(parameter.index, self.node_id, self.host_id)
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)
            return
        self._send(frame)

    def read_parameter_index(self, index: int) -> None:
        self.parameter_var.set(self._parameter_label(index))
        self._on_parameter_selected()
        self.read_parameter()

    def write_parameter(self) -> None:
        if not self._require_idle():
            return
        try:
            parameter = self._selected_parameter()
            raw = self.value_var.get().strip()
            value: int | float = float(raw) if parameter.kind == "float" else int(raw, 0)
            frame = build_parameter_write(parameter.index, value, self.node_id, self.host_id)
        except ValueError as exc:
            messagebox.showerror("写入值不合法", str(exc), parent=self)
            return
        if not messagebox.askokcancel(
            "确认安全参数写入",
            f"确认写入 0x{parameter.index:04X} {parameter.name} = {value}？\n\n"
            "电机必须保持 IDLE/RESET；写入后将自动回读。",
            parent=self,
        ):
            return
        if not self._send(frame):
            return
        self.pending_verification[parameter.index] = value
        self._state_changed()
        if self._verify_after_id is not None:
            self.after_cancel(self._verify_after_id)
        self._verify_after_id = self.after(
            150, lambda: self._verify_parameter(parameter.index)
        )

    def send_rejection_probe(self, probe_name: str) -> None:
        if not self._require_idle():
            return
        try:
            label, index, frame = build_rejection_probe(
                probe_name, self.node_id, self.host_id
            )
        except ValueError as exc:
            messagebox.showerror("拒绝测试参数错误", str(exc), parent=self)
            return
        if not messagebox.askokcancel(
            "确认固件拒绝路径测试",
            f"将发送固定测试：{label}\n\n预期固件拒绝且参数保持不变。",
            parent=self,
        ):
            return
        if not self._send(frame):
            return
        self.pending_rejection[index] = label
        self._state_changed()
        self._verify_after_id = self.after(150, lambda: self._verify_parameter(index))

    def _verify_parameter(self, index: int) -> None:
        self._verify_after_id = None
        if index not in self.pending_verification and index not in self.pending_rejection:
            return
        if not self._send(build_parameter_read(index, self.node_id, self.host_id)):
            self.pending_verification.pop(index, None)
            self.pending_rejection.pop(index, None)
            self._state_changed()
            return
        self._verify_after_id = self.after(
            1000, lambda: self._expire_verification(index)
        )

    def _expire_verification(self, index: int) -> None:
        self._verify_after_id = None
        expected = self.pending_verification.pop(index, None)
        rejection = self.pending_rejection.pop(index, None)
        if expected is None and rejection is None:
            return
        self._log(
            "error",
            f"Parameter verification timed out: 0x{index:04X}.\n",
        )
        self._state_changed()

    def handle_frame(self, frame: CanFrame) -> bool:
        comm_type, _data2, target = split_id(frame.arbitration_id)
        if comm_type != 17 or target != self.host_id or len(frame.data) != 8:
            return False
        response_index = int.from_bytes(frame.data[0:2], "little")
        try:
            result = parse_parameter_response(frame, self.host_id)
        except ValueError as exc:
            if self.long_run.running and response_index == self.long_run.pending_index:
                self.long_run.reject_response(
                    response_index,
                    str(exc),
                    datetime.now().isoformat(timespec="milliseconds"),
                )
                self._update_long_run_status()
            else:
                self.pending_verification.pop(response_index, None)
                self.pending_rejection.pop(response_index, None)
                self._state_changed()
            self._log("error", f"参数响应失败：{exc}\n")
            return True
        if result is None:
            return False
        index, value = result
        value_callback = getattr(self, "_value_callback", None)
        if value_callback is not None:
            value_callback(index, value)
        parameter = PARAMETER_BY_INDEX[index]
        shown = f"{value:.7g}" if isinstance(value, float) else str(value)
        if self.long_run.accept_response(
            index,
            value,
            time.monotonic(),
            datetime.now().isoformat(timespec="milliseconds"),
        ):
            self.last_value_var.set(
                f"长稳读取：0x{index:04X} {parameter.name} = {shown}"
            )
            self._update_long_run_status()
            return True
        self.last_value_var.set(f"当前值：0x{index:04X} {parameter.name} = {shown}")
        self._log("event", f"参数 0x{index:04X} {parameter.name} = {shown}\n")
        expected = self.pending_verification.pop(index, None)
        if expected is not None:
            self._log(
                "event" if value == expected else "error",
                "写后回读数值一致。\n"
                if value == expected
                else f"写后回读不一致：期望 {expected}，实际 {value}\n",
            )
        rejection = self.pending_rejection.pop(index, None)
        if rejection is not None:
            self._log("event", f"拒绝测试已回读：{rejection}；参数保持不变。\n")
        if (expected is not None or rejection is not None) and self._verify_after_id is not None:
            try:
                self.after_cancel(self._verify_after_id)
            except tk.TclError:
                pass
            self._verify_after_id = None
        self._state_changed()
        return True

    def start_long_run(self) -> None:
        if not self._require_idle():
            return
        try:
            minutes = int(self.long_run_minutes_var.get())
            if not 1 <= minutes <= 1440:
                raise ValueError("长稳时长必须为 1..1440 分钟")
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("长稳参数错误", str(exc), parent=self)
            return
        if not messagebox.askokcancel(
            "确认开始无动力长稳",
            f"将连续进行 {minutes} 分钟 Type 17 只读轮询。\n\n"
            "测试期间电机必须保持 IDLE，且不会发送运动或写入报文。",
            parent=self,
        ):
            return
        self.long_run = LongRunSession(duration_s=minutes * 60.0)
        self.long_run.start(time.monotonic())
        self.long_run_minutes.configure(state=tk.DISABLED)
        self.long_run_start_button.configure(state=tk.DISABLED)
        self.long_run_stop_button.configure(state=tk.NORMAL)
        self.refresh_state()
        self._state_changed()
        self._schedule_long_run_tick()

    def stop_long_run(self) -> None:
        if self.long_run.running:
            self.long_run.stop("stopped_by_user")
            self._finish_long_run("长稳测试已由用户停止")

    def _schedule_long_run_tick(self) -> None:
        if self._long_run_after_id is None and self.long_run.running:
            self._long_run_after_id = self.after(20, self._long_run_tick)

    def _long_run_tick(self) -> None:
        self._long_run_after_id = None
        now = time.monotonic()
        stamp = datetime.now().isoformat(timespec="milliseconds")
        index = self.long_run.tick(now, stamp)
        if index is not None:
            try:
                self._send_frame(build_parameter_read(index, self.node_id, self.host_id))
            except (OSError, RuntimeError, ValueError) as exc:
                self.long_run.send_failed(str(exc), stamp)
                self._log("error", f"长稳发送失败：{exc}\n")
        self._update_long_run_status(now)
        if self.long_run.running:
            self._schedule_long_run_tick()
        else:
            self._finish_long_run("长稳测试达到设定时长")

    def _finish_long_run(self, message: str) -> None:
        if self._long_run_after_id is not None:
            try:
                self.after_cancel(self._long_run_after_id)
            except tk.TclError:
                pass
            self._long_run_after_id = None
        self.long_run_minutes.configure(state=tk.NORMAL)
        self.long_run_start_button.configure(state=tk.NORMAL)
        self.long_run_stop_button.configure(state=tk.DISABLED)
        self._update_long_run_status()
        self.refresh_state()
        self._state_changed()
        self._log("event", f"{message}。{self._long_run_summary()}\n")

    def _update_long_run_status(self, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        state = "运行中" if self.long_run.running else self.long_run.stop_reason
        self.long_run_status_var.set(
            f"长稳状态：{state} | 已运行 {self.long_run.elapsed_s(now) / 60.0:.1f} min | "
            f"TX {self.long_run.tx_count} | 响应 {self.long_run.response_count} | "
            f"超时 {self.long_run.timeout_count} | 拒绝 {self.long_run.rejection_count}"
        )

    def _long_run_summary(self) -> str:
        return (
            f"TX={self.long_run.tx_count} response={self.long_run.response_count} "
            f"timeout={self.long_run.timeout_count} rejected={self.long_run.rejection_count} "
            f"send_failed={self.long_run.send_failure_count}"
        )

    def export_long_run_csv(self) -> None:
        if not self.long_run.records:
            messagebox.showinfo("没有长稳数据", "请先运行长稳测试。", parent=self)
            return
        path = filedialog.asksaveasfilename(
            parent=self,
            title=self._ui("导出 CAN 长稳记录"),
            defaultextension=".csv",
            initialfile=datetime.now().strftime("can_long_run_%Y%m%d_%H%M%S.csv"),
            filetypes=((self._ui("CSV 文件"), "*.csv"),),
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(("summary", self._long_run_summary()))
                writer.writerow(
                    ("sent_at", "finished_at", "index", "name", "result", "value", "latency_ms", "detail")
                )
                for record in self.long_run.records:
                    writer.writerow(
                        (
                            record.sent_at,
                            record.finished_at,
                            f"0x{record.index:04X}",
                            record.name,
                            record.result,
                            record.value,
                            record.latency_ms,
                            record.detail,
                        )
                    )
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)

    def on_disconnect(self) -> None:
        if self._verify_after_id is not None:
            try:
                self.after_cancel(self._verify_after_id)
            except tk.TclError:
                pass
            self._verify_after_id = None
        self.pending_verification.clear()
        self.pending_rejection.clear()
        if self.long_run.running:
            self.long_run.stop("disconnected")
            self._finish_long_run("CAN 已断开，长稳测试提前停止")
        self.refresh_state()
        self._state_changed()
