"""Independent, non-motion RS04 USB-CAN acceptance window."""

from __future__ import annotations

import csv
import queue
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import serial
from serial.tools import list_ports

from rs04_can import (
    AtFrameDecoder,
    CanFrame,
    PARAMETERS,
    PARAMETER_BY_INDEX,
    build_device_id_request,
    build_parameter_read,
    build_parameter_write,
    build_rejection_probe,
    encode_at_frame,
    format_frame,
    parse_device_id_response,
    parse_parameter_response,
    split_id,
)
from rs04_long_run import LongRunSession


USB_CAN_BAUD = 921_600


class Rs04CanPanel(tk.Toplevel):
    """Safe stage-3/4 tooling for the official RobStride USB-CAN module."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.title("RS04 官方 CAN 协议参数验收")
        self.geometry("980x820")
        self.minsize(820, 680)

        self.connection: serial.Serial | None = None
        self.connection_lock = threading.Lock()
        self.reader_stop = threading.Event()
        self.reader_thread: threading.Thread | None = None
        self.rx_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.decoder = AtFrameDecoder()
        self._poll_after_id: str | None = None
        self._verify_after_id: str | None = None
        self._long_run_after_id: str | None = None
        self.pending_verification: dict[int, int | float] = {}
        self.pending_rejection: dict[int, str] = {}
        self.long_run = LongRunSession()
        self._long_run_node_id = 0x7F
        self._long_run_host_id = 0xFD

        self.port_var = tk.StringVar()
        self.connection_var = tk.StringVar(value="USB-CAN：未连接")
        self.node_id_var = tk.IntVar(value=0x7F)
        self.host_id_var = tk.IntVar(value=0xFD)
        self.parameter_var = tk.StringVar()
        self.value_var = tk.StringVar()
        self.parameter_help_var = tk.StringVar()
        self.last_value_var = tk.StringVar(value="当前值：尚未读取")
        self.long_run_minutes_var = tk.IntVar(value=60)
        self.long_run_status_var = tk.StringVar(value="长稳状态：未开始")

        self._build_ui()
        self.refresh_ports()
        self._on_parameter_selected()
        self._poll_after_id = self.after(20, self._process_rx_queue)
        self.protocol("WM_DELETE_WINDOW", self.close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        connection_frame = ttk.LabelFrame(outer, text="官方 USB-CAN 模块", padding=10)
        connection_frame.pack(fill=tk.X)
        ttk.Label(connection_frame, text="串口").grid(row=0, column=0, padx=(0, 6))
        self.port_combo = ttk.Combobox(
            connection_frame, textvariable=self.port_var, width=15, state="readonly"
        )
        self.port_combo.grid(row=0, column=1, padx=(0, 6))
        ttk.Button(connection_frame, text="刷新", command=self.refresh_ports).grid(
            row=0, column=2, padx=(0, 12)
        )
        ttk.Label(connection_frame, text="921600 baud / 8N1 / AT 帧").grid(
            row=0, column=3, padx=(0, 12)
        )
        self.connect_button = ttk.Button(
            connection_frame, text="连接", command=self.toggle_connection
        )
        self.connect_button.grid(row=0, column=4, padx=(0, 12))
        ttk.Label(connection_frame, textvariable=self.connection_var).grid(
            row=0, column=5, sticky="w"
        )
        connection_frame.columnconfigure(5, weight=1)

        identity_frame = ttk.LabelFrame(outer, text="节点与枚举（通信类型 0）", padding=10)
        identity_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(identity_frame, text="电机 CAN ID").grid(row=0, column=0)
        self.node_id_spinbox = ttk.Spinbox(
            identity_frame,
            from_=0,
            to=255,
            textvariable=self.node_id_var,
            width=7,
            justify="center",
        )
        self.node_id_spinbox.grid(row=0, column=1, padx=(6, 18))
        ttk.Label(identity_frame, text="主机 ID").grid(row=0, column=2)
        self.host_id_spinbox = ttk.Spinbox(
            identity_frame,
            from_=0,
            to=255,
            textvariable=self.host_id_var,
            width=7,
            justify="center",
        )
        self.host_id_spinbox.grid(row=0, column=3, padx=(6, 18))
        ttk.Button(identity_frame, text="检测设备", command=self.enumerate_device).grid(
            row=0, column=4
        )
        ttk.Label(
            identity_frame,
            text="默认值与官方工具一致：电机 127 (0x7F)，主机 253 (0xFD)",
        ).grid(row=0, column=5, padx=(18, 0), sticky="w")
        identity_frame.columnconfigure(5, weight=1)

        parameter_frame = ttk.LabelFrame(
            outer, text="单参数读写（通信类型 17 / 18）", padding=10
        )
        parameter_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(parameter_frame, text="参数").grid(row=0, column=0, sticky="w")
        labels = [self._parameter_label(item.index) for item in PARAMETERS]
        self.parameter_combo = ttk.Combobox(
            parameter_frame,
            textvariable=self.parameter_var,
            values=labels,
            width=31,
            height=12,
            state="readonly",
        )
        self.parameter_combo.grid(row=0, column=1, padx=(6, 8), sticky="ew")
        self.parameter_combo.bind("<<ComboboxSelected>>", self._on_parameter_selected)
        if labels:
            self.parameter_var.set(labels[0])
        ttk.Button(parameter_frame, text="读取", command=self.read_parameter).grid(
            row=0, column=2, padx=4
        )
        ttk.Label(parameter_frame, text="写入值").grid(row=0, column=3, padx=(20, 4))
        self.value_entry = ttk.Entry(parameter_frame, textvariable=self.value_var, width=14)
        self.value_entry.grid(row=0, column=4, padx=4)
        self.write_button = ttk.Button(
            parameter_frame, text="安全写入并回读", command=self.write_parameter
        )
        self.write_button.grid(row=0, column=5, padx=(4, 0))
        ttk.Label(parameter_frame, text="常用验收").grid(
            row=1, column=0, pady=(8, 0), sticky="w"
        )
        quick_reads = ttk.Frame(parameter_frame)
        quick_reads.grid(row=1, column=1, columnspan=5, pady=(8, 0), sticky="w")
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

        rejection_frame = ttk.LabelFrame(
            outer, text="阶段 3 固件拒绝路径（固定无动力报文）", padding=10
        )
        rejection_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(
            rejection_frame,
            text="预期三项均被固件拒绝；发送后必须在 COM4 确认 CAN_PARAM: write rejected。",
        ).grid(row=0, column=0, columnspan=3, sticky="w")
        for column, (probe_name, caption) in enumerate(
            (
                ("readonly_mechpos", "测试只读 mechPos"),
                ("epscan_below_min", "测试 EPScan_time=0"),
                ("cantimeout_gap", "测试 cantimeout=19"),
            )
        ):
            ttk.Button(
                rejection_frame,
                text=caption,
                command=lambda selected=probe_name: self.send_rejection_probe(selected),
            ).grid(row=1, column=column, padx=(0, 8), pady=(8, 0), sticky="w")
        rejection_frame.columnconfigure(3, weight=1)

        long_run_frame = ttk.LabelFrame(
            outer, text="阶段 4：无动力 CAN 长稳（只读 Type 17）", padding=10
        )
        long_run_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(long_run_frame, text="时长（分钟）").grid(row=0, column=0)
        self.long_run_minutes = ttk.Spinbox(
            long_run_frame,
            from_=1,
            to=1440,
            textvariable=self.long_run_minutes_var,
            width=7,
            justify="center",
        )
        self.long_run_minutes.grid(row=0, column=1, padx=(6, 14))
        ttk.Label(long_run_frame, text="周期 100 ms / 响应超时 500 ms").grid(
            row=0, column=2, padx=(0, 14)
        )
        self.long_run_start_button = ttk.Button(
            long_run_frame, text="开始长稳", command=self.start_long_run
        )
        self.long_run_start_button.grid(row=0, column=3, padx=(0, 6))
        self.long_run_stop_button = ttk.Button(
            long_run_frame, text="停止", command=self.stop_long_run, state=tk.DISABLED
        )
        self.long_run_stop_button.grid(row=0, column=4, padx=(0, 6))
        ttk.Button(long_run_frame, text="导出 CSV", command=self.export_long_run_csv).grid(
            row=0, column=5
        )
        ttk.Label(long_run_frame, textvariable=self.long_run_status_var).grid(
            row=1, column=0, columnspan=6, pady=(8, 0), sticky="w"
        )
        ttk.Label(
            long_run_frame,
            text="轮询 run_mode、mechPos、mechVel、EPScan_time、cantimeout；"
            "开始前和结束后请在 COM4 点击“查询状态”保存计数器基线。",
            wraplength=920,
        ).grid(row=2, column=0, columnspan=6, pady=(5, 0), sticky="w")

        warning = (
            "安全边界：此窗口不提供使能、运行、置零或修改 CAN ID。"
            "仅 run_mode=0、EPScan_time=1..200、cantimeout=0 或 20..100000 可写；"
            "通信类型 18 只返回状态帧，因此写入后会自动用类型 17 回读数值；"
            "最终接受结果还要同时查看 COM4 的 CAN_PARAM accepted/rejected；"
            "长稳功能只发送类型 17 参数读取，不发送任何写入或运动报文。"
        )
        ttk.Label(outer, text=warning, foreground="#9b5a00", wraplength=930).pack(
            fill=tk.X, pady=(10, 0)
        )

        log_frame = ttk.LabelFrame(outer, text="CAN 收发记录", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.log = tk.Text(
            log_frame,
            wrap=tk.NONE,
            state=tk.DISABLED,
            font=("Consolas", 10),
            background="#101418",
            foreground="#d9e2ea",
        )
        y_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        x_scroll = ttk.Scrollbar(log_frame, orient=tk.HORIZONTAL, command=self.log.xview)
        self.log.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log.tag_configure("tx", foreground="#76d7ff")
        self.log.tag_configure("rx", foreground="#d9e2ea")
        self.log.tag_configure("event", foreground="#ffe08a")
        self.log.tag_configure("error", foreground="#ff7777")

    @staticmethod
    def _parameter_label(index: int) -> str:
        parameter = PARAMETER_BY_INDEX[index]
        access = "安全可写" if parameter.writable else "只读"
        return f"0x{index:04X}  {parameter.name}  [{parameter.kind}, {access}]"

    def _selected_parameter(self):
        text = self.parameter_var.get().strip()
        if not text.startswith("0x"):
            raise ValueError("请选择参数")
        return PARAMETER_BY_INDEX[int(text[2:6], 16)]

    def _on_parameter_selected(self, _event=None) -> None:
        if self.long_run.running:
            self.parameter_help_var.set("长稳运行中：手动参数操作已锁定。")
            self.write_button.configure(state=tk.DISABLED)
            self.value_entry.configure(state=tk.DISABLED)
            return
        try:
            parameter = self._selected_parameter()
        except (ValueError, KeyError):
            self.parameter_help_var.set("请选择一个参数")
            self.write_button.configure(state=tk.DISABLED)
            self.value_entry.configure(state=tk.DISABLED)
            return
        if not parameter.writable:
            self.parameter_help_var.set("当前固件阶段按只读处理；官方表中的其他 W/R 参数尚未放开。")
            self.write_button.configure(state=tk.DISABLED)
            self.value_entry.configure(state=tk.DISABLED)
        else:
            if parameter.allowed_values and parameter.minimum is None:
                bounds = "允许值：" + ", ".join(str(value) for value in parameter.allowed_values)
            else:
                bounds = f"允许范围：{parameter.minimum}..{parameter.maximum}"
                if parameter.allowed_values:
                    bounds += "，另允许 " + ", ".join(str(value) for value in parameter.allowed_values)
            self.parameter_help_var.set(bounds + "；电机必须处于 IDLE/RESET。")
            self.write_button.configure(state=tk.NORMAL)
            self.value_entry.configure(state=tk.NORMAL)

    def refresh_ports(self) -> None:
        ports = [port.device for port in list_ports.comports()]
        self.port_combo["values"] = ports
        if self.port_var.get() not in ports:
            self.port_var.set(ports[0] if ports else "")

    def toggle_connection(self) -> None:
        if self.connection is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self) -> None:
        port = self.port_var.get()
        if not port:
            messagebox.showwarning("没有串口", "请连接官方 USB-CAN 模块后刷新。", parent=self)
            return
        try:
            connection = serial.Serial(
                port=port,
                baudrate=USB_CAN_BAUD,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
                write_timeout=0.2,
            )
            connection.reset_input_buffer()
        except (serial.SerialException, OSError) as exc:
            messagebox.showerror("USB-CAN 连接失败", str(exc), parent=self)
            return
        self.connection = connection
        self.decoder = AtFrameDecoder()
        self.reader_stop.clear()
        self.reader_thread = threading.Thread(
            target=self._reader_loop, name="rs04-usb-can", daemon=True
        )
        self.reader_thread.start()
        self.connect_button.configure(text="断开")
        self.connection_var.set(f"USB-CAN：已连接 {port}")
        self._append_log("event", f"已连接 {port} @ {USB_CAN_BAUD}\n")

    def disconnect(self) -> None:
        if self.long_run.running:
            self.long_run.stop("disconnected")
            self._finish_long_run("USB-CAN 已断开，长稳测试提前停止")
        self.reader_stop.set()
        connection = self.connection
        self.connection = None
        if connection is not None:
            try:
                connection.close()
            except (serial.SerialException, OSError):
                pass
        self.pending_verification.clear()
        self.pending_rejection.clear()
        self.connect_button.configure(text="连接")
        self.connection_var.set("USB-CAN：未连接")
        self._append_log("event", "USB-CAN 已断开\n")

    def enumerate_device(self) -> None:
        if self._manual_action_blocked():
            return
        try:
            self._send(build_device_id_request(self._node_id(), self._host_id()))
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)

    def read_parameter(self) -> None:
        if self._manual_action_blocked():
            return
        try:
            parameter = self._selected_parameter()
            self._send(build_parameter_read(parameter.index, self._node_id(), self._host_id()))
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)

    def read_parameter_index(self, index: int) -> None:
        if self._manual_action_blocked():
            return
        parameter = PARAMETER_BY_INDEX[index]
        self.parameter_var.set(self._parameter_label(index))
        self._on_parameter_selected()
        try:
            self._send(build_parameter_read(parameter.index, self._node_id(), self._host_id()))
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)

    def write_parameter(self) -> None:
        if self._manual_action_blocked():
            return
        try:
            parameter = self._selected_parameter()
            raw_value = self.value_var.get().strip()
            value: int | float = float(raw_value) if parameter.kind == "float" else int(raw_value, 0)
            frame = build_parameter_write(
                parameter.index, value, self._node_id(), self._host_id()
            )
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
        try:
            self._send(frame)
        except ValueError as exc:
            messagebox.showerror("发送失败", str(exc), parent=self)
            return
        self.pending_verification[parameter.index] = value
        if self._verify_after_id is not None:
            self.after_cancel(self._verify_after_id)
        self._verify_after_id = self.after(150, lambda: self._verify_write(parameter.index))

    def _verify_write(self, index: int) -> None:
        self._verify_after_id = None
        if index not in self.pending_verification:
            return
        try:
            self._send(build_parameter_read(index, self._node_id(), self._host_id()))
        except ValueError as exc:
            self._append_log("error", f"写后回读未发送：{exc}\n")

    def send_rejection_probe(self, probe_name: str) -> None:
        if self._manual_action_blocked():
            return
        try:
            label, index, frame = build_rejection_probe(
                probe_name, self._node_id(), self._host_id()
            )
        except ValueError as exc:
            messagebox.showerror("拒绝测试参数错误", str(exc), parent=self)
            return
        if not messagebox.askokcancel(
            "确认固件拒绝路径测试",
            f"将发送固定测试：{label}\n\n"
            "该报文不包含使能或运动指令，预期固件拒绝且参数保持不变。"
            "请同时观察 COM4 日志。",
            parent=self,
        ):
            return
        try:
            self._send(frame)
        except ValueError as exc:
            messagebox.showerror("发送失败", str(exc), parent=self)
            return
        self.pending_rejection[index] = label
        self._append_log("event", f"已发送拒绝测试：{label}\n")
        if self._verify_after_id is not None:
            self.after_cancel(self._verify_after_id)
        self._verify_after_id = self.after(
            150, lambda: self._verify_rejection(index)
        )

    def _verify_rejection(self, index: int) -> None:
        self._verify_after_id = None
        if index not in self.pending_rejection:
            return
        try:
            self._send(build_parameter_read(index, self._node_id(), self._host_id()))
        except ValueError as exc:
            self._append_log("error", f"拒绝测试回读未发送：{exc}\n")

    def start_long_run(self) -> None:
        connection = self.connection
        if connection is None or not connection.is_open:
            messagebox.showwarning("USB-CAN 未连接", "请先连接官方 USB-CAN 模块。", parent=self)
            return
        if self.pending_verification or self.pending_rejection:
            messagebox.showwarning(
                "仍有验收操作未完成",
                "请等待当前写后回读或拒绝测试结束，再开始长稳测试。",
                parent=self,
            )
            return
        try:
            minutes = int(self.long_run_minutes_var.get())
            if not 1 <= minutes <= 1440:
                raise ValueError("长稳时长必须为 1..1440 分钟")
            node_id = self._node_id()
            host_id = self._host_id()
        except (tk.TclError, ValueError) as exc:
            messagebox.showerror("长稳参数错误", str(exc), parent=self)
            return
        if not messagebox.askokcancel(
            "确认开始无动力长稳",
            f"将对 CAN ID {node_id} 连续进行 {minutes} 分钟 Type 17 只读轮询。\n\n"
            "请确认功率级关闭、电机未使能、COM4 显示 mci=0，并已保存开始前 can status。\n"
            "本测试不会发送使能、运控、写参数、置零或修改 CAN ID 命令。",
            parent=self,
        ):
            return

        self.long_run = LongRunSession(duration_s=minutes * 60.0)
        self.long_run.start(time.monotonic())
        self._long_run_node_id = node_id
        self._long_run_host_id = host_id
        self.node_id_spinbox.configure(state=tk.DISABLED)
        self.host_id_spinbox.configure(state=tk.DISABLED)
        self.long_run_minutes.configure(state=tk.DISABLED)
        self.long_run_start_button.configure(state=tk.DISABLED)
        self.long_run_stop_button.configure(state=tk.NORMAL)
        self._on_parameter_selected()
        self._append_log(
            "event",
            f"阶段 4 长稳开始：{minutes} min，100 ms 周期，500 ms 超时，"
            f"node={node_id} host={host_id}；仅 Type 17 读取。\n",
        )
        self._update_long_run_status()
        self._schedule_long_run_tick()

    def stop_long_run(self) -> None:
        if not self.long_run.running:
            return
        self.long_run.stop("stopped_by_user")
        self._finish_long_run("长稳测试已由用户停止")

    def _schedule_long_run_tick(self) -> None:
        if self._long_run_after_id is None and self.long_run.running:
            self._long_run_after_id = self.after(20, self._long_run_tick)

    def _long_run_tick(self) -> None:
        self._long_run_after_id = None
        now = time.monotonic()
        timestamp = datetime.now().isoformat(timespec="milliseconds")
        previous_timeouts = self.long_run.timeout_count
        index = self.long_run.tick(now, timestamp)
        if self.long_run.timeout_count != previous_timeouts:
            self._append_log(
                "error",
                f"长稳响应超时：累计 {self.long_run.timeout_count} 次；测试继续。\n",
            )
        if index is not None:
            try:
                self._send(
                    build_parameter_read(
                        index, self._long_run_node_id, self._long_run_host_id
                    ),
                    log_frame=False,
                )
            except ValueError as exc:
                self.long_run.send_failed(str(exc), timestamp)
                self._append_log("error", f"长稳发送失败：{exc}\n")
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
        self.node_id_spinbox.configure(state=tk.NORMAL)
        self.host_id_spinbox.configure(state=tk.NORMAL)
        self.long_run_start_button.configure(state=tk.NORMAL)
        self.long_run_stop_button.configure(state=tk.DISABLED)
        self._on_parameter_selected()
        self._update_long_run_status()
        self._append_log("event", f"{message}。{self._long_run_summary()}\n")
        self._append_log(
            "event",
            "请在 COM4 再次点击“查询状态”，对比 BusOff、收发错误、FIFO 丢失和参数计数器。\n",
        )

    def _update_long_run_status(self, now: float | None = None) -> None:
        if now is None:
            now = time.monotonic()
        state = "运行中" if self.long_run.running else self.long_run.stop_reason
        elapsed = self.long_run.elapsed_s(now)
        self.long_run_status_var.set(
            f"长稳状态：{state} | 已运行 {elapsed / 60.0:.1f} min | "
            f"TX {self.long_run.tx_count} | 响应 {self.long_run.response_count} | "
            f"超时 {self.long_run.timeout_count} | 拒绝 {self.long_run.rejection_count} | "
            f"发送失败 {self.long_run.send_failure_count} | "
            f"延迟 avg/max {self.long_run.average_latency_ms:.1f}/"
            f"{self.long_run.max_latency_ms:.1f} ms"
        )

    def _long_run_summary(self) -> str:
        return (
            f"TX={self.long_run.tx_count} response={self.long_run.response_count} "
            f"timeout={self.long_run.timeout_count} rejected={self.long_run.rejection_count} "
            f"send_failed={self.long_run.send_failure_count} "
            f"latency_avg={self.long_run.average_latency_ms:.2f}ms "
            f"latency_max={self.long_run.max_latency_ms:.2f}ms"
        )

    def export_long_run_csv(self) -> None:
        if not self.long_run.records:
            messagebox.showinfo("没有长稳数据", "请先运行长稳测试。", parent=self)
            return
        default_name = datetime.now().strftime("rs04_can_long_run_%Y%m%d_%H%M%S.csv")
        path = filedialog.asksaveasfilename(
            parent=self,
            title="导出 CAN 长稳记录",
            defaultextension=".csv",
            initialfile=default_name,
            filetypes=(("CSV 文件", "*.csv"), ("所有文件", "*.*")),
        )
        if not path:
            return
        try:
            with open(path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow(("summary", self._long_run_summary()))
                writer.writerow(())
                writer.writerow(
                    (
                        "sent_at",
                        "finished_at",
                        "index",
                        "name",
                        "result",
                        "value",
                        "latency_ms",
                        "detail",
                    )
                )
                for record in self.long_run.records:
                    writer.writerow(
                        (
                            record.sent_at,
                            record.finished_at,
                            f"0x{record.index:04X}",
                            record.name,
                            record.result,
                            "" if record.value is None else record.value,
                            "" if record.latency_ms is None else f"{record.latency_ms:.3f}",
                            record.detail,
                        )
                    )
        except OSError as exc:
            messagebox.showerror("导出失败", str(exc), parent=self)
            return
        self._append_log("event", f"长稳 CSV 已导出：{path}\n")

    def _manual_action_blocked(self) -> bool:
        if not self.long_run.running:
            return False
        messagebox.showwarning(
            "长稳测试正在运行",
            "长稳期间不允许枚举、手动读写或拒绝测试；请先停止长稳。",
            parent=self,
        )
        return True

    def _node_id(self) -> int:
        value = int(self.node_id_var.get())
        if not 0 <= value <= 255:
            raise ValueError("电机 CAN ID 必须为 0..255")
        return value

    def _host_id(self) -> int:
        value = int(self.host_id_var.get())
        if not 0 <= value <= 255:
            raise ValueError("主机 ID 必须为 0..255")
        return value

    def _send(self, frame: CanFrame, *, log_frame: bool = True) -> None:
        connection = self.connection
        if connection is None or not connection.is_open:
            raise ValueError("请先连接 USB-CAN 串口")
        raw = encode_at_frame(frame)
        try:
            with self.connection_lock:
                connection.write(raw)
                connection.flush()
        except (serial.SerialException, OSError) as exc:
            raise ValueError(str(exc)) from exc
        if log_frame:
            self._append_log("tx", f"TX  {format_frame(frame)} | AT={raw.hex(' ').upper()}\n")

    def _reader_loop(self) -> None:
        while not self.reader_stop.is_set():
            connection = self.connection
            if connection is None:
                return
            try:
                chunk = connection.read(max(connection.in_waiting, 1))
            except (serial.SerialException, OSError) as exc:
                self.rx_queue.put(("error", str(exc)))
                return
            if not chunk:
                continue
            for frame in self.decoder.feed(chunk):
                self.rx_queue.put(("frame", frame))

    def _process_rx_queue(self) -> None:
        try:
            while True:
                kind, payload = self.rx_queue.get_nowait()
                if kind == "frame":
                    self._handle_frame(payload)
                else:
                    self._append_log("error", f"USB-CAN 串口错误：{payload}\n")
                    if self.connection is not None:
                        self.disconnect()
        except queue.Empty:
            pass
        if self.winfo_exists():
            self._poll_after_id = self.after(20, self._process_rx_queue)

    def _handle_frame(self, frame: CanFrame) -> None:
        response_host_id = (
            self._long_run_host_id if self.long_run.running else self._host_id()
        )
        comm_type, _data2, target = split_id(frame.arbitration_id)
        response_index = None
        if comm_type == 17 and target == response_host_id and len(frame.data) == 8:
            response_index = int.from_bytes(frame.data[0:2], "little")
        long_run_response = (
            self.long_run.running and response_index == self.long_run.pending_index
        )
        if not long_run_response:
            self._append_log("rx", f"RX  {format_frame(frame)}\n")
        device = parse_device_id_response(frame, response_host_id)
        if device is not None:
            node_id, uid = device
            self.node_id_var.set(node_id)
            self._append_log(
                "event", f"检测到设备：CAN ID={node_id}，MCU UID=0x{uid:016X}\n"
            )
            return
        if comm_type == 2 and (self.pending_verification or self.pending_rejection):
            self._append_log(
                "event",
                "收到类型 2 状态反馈；官方协议未在此帧中返回参数写入成败，等待类型 17 回读。\n",
            )
        try:
            result = parse_parameter_response(frame, response_host_id)
        except ValueError as exc:
            if long_run_response and response_index is not None:
                self.long_run.reject_response(
                    response_index,
                    str(exc),
                    datetime.now().isoformat(timespec="milliseconds"),
                )
                self._update_long_run_status()
            self._append_log("error", f"参数响应失败：{exc}\n")
            return
        if result is None:
            return
        index, value = result
        parameter = PARAMETER_BY_INDEX[index]
        shown = f"{value:.7g}" if isinstance(value, float) else str(value)
        if self.long_run.accept_response(
            index,
            value,
            time.monotonic(),
            datetime.now().isoformat(timespec="milliseconds"),
        ):
            self.last_value_var.set(f"长稳读取：0x{index:04X} {parameter.name} = {shown}")
            self._update_long_run_status()
            if self.long_run.response_count % 100 == 0:
                self._append_log("event", f"长稳进度：{self._long_run_summary()}\n")
            return
        self.last_value_var.set(f"当前值：0x{index:04X} {parameter.name} = {shown}")
        self._append_log("event", f"参数 0x{index:04X} {parameter.name} = {shown}\n")
        expected = self.pending_verification.pop(index, None)
        if expected is not None:
            if value == expected:
                self._append_log(
                    "event",
                    "写后回读数值一致；请再确认 COM4 出现 CAN_PARAM: write accepted。\n",
                )
            else:
                self._append_log(
                    "error", f"写后回读不一致：期望 {expected}，实际 {value}\n"
                )
        rejection_label = self.pending_rejection.pop(index, None)
        if rejection_label is not None:
            self._append_log(
                "event",
                f"拒绝测试已回读：{rejection_label}；请确认 COM4 为 write rejected，"
                "并检查 param_write_rejected 计数增加。\n",
            )

    def _append_log(self, tag: str, text: str) -> None:
        if not self.winfo_exists():
            return
        stamp = datetime.now().strftime("[%H:%M:%S.%f]")[:-3]
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"{stamp} {text}", tag)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def close(self) -> None:
        if self._long_run_after_id is not None:
            try:
                self.after_cancel(self._long_run_after_id)
            except tk.TclError:
                pass
            self._long_run_after_id = None
        self.long_run.stop("window_closed")
        if self._verify_after_id is not None:
            try:
                self.after_cancel(self._verify_after_id)
            except tk.TclError:
                pass
            self._verify_after_id = None
        if self._poll_after_id is not None:
            try:
                self.after_cancel(self._poll_after_id)
            except tk.TclError:
                pass
            self._poll_after_id = None
        self.disconnect()
        self.destroy()
