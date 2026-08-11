"""Independent, non-motion RS04 USB-CAN acceptance window."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

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


USB_CAN_BAUD = 921_600


class Rs04CanPanel(tk.Toplevel):
    """Safe stage-3 tooling for the official RobStride USB-CAN module."""

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.title("RS04 官方 CAN 协议参数验收")
        self.geometry("980x690")
        self.minsize(820, 580)

        self.connection: serial.Serial | None = None
        self.connection_lock = threading.Lock()
        self.reader_stop = threading.Event()
        self.reader_thread: threading.Thread | None = None
        self.rx_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.decoder = AtFrameDecoder()
        self._poll_after_id: str | None = None
        self._verify_after_id: str | None = None
        self.pending_verification: dict[int, int | float] = {}
        self.pending_rejection: dict[int, str] = {}

        self.port_var = tk.StringVar()
        self.connection_var = tk.StringVar(value="USB-CAN：未连接")
        self.node_id_var = tk.IntVar(value=0x7F)
        self.host_id_var = tk.IntVar(value=0xFD)
        self.parameter_var = tk.StringVar()
        self.value_var = tk.StringVar()
        self.parameter_help_var = tk.StringVar()
        self.last_value_var = tk.StringVar(value="当前值：尚未读取")

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
        ttk.Spinbox(
            identity_frame,
            from_=0,
            to=255,
            textvariable=self.node_id_var,
            width=7,
            justify="center",
        ).grid(row=0, column=1, padx=(6, 18))
        ttk.Label(identity_frame, text="主机 ID").grid(row=0, column=2)
        ttk.Spinbox(
            identity_frame,
            from_=0,
            to=255,
            textvariable=self.host_id_var,
            width=7,
            justify="center",
        ).grid(row=0, column=3, padx=(6, 18))
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

        warning = (
            "安全边界：此窗口不提供使能、运行、置零或修改 CAN ID。"
            "仅 run_mode=0、EPScan_time=1..200、cantimeout=0 或 20..100000 可写；"
            "通信类型 18 只返回状态帧，因此写入后会自动用类型 17 回读数值；"
            "最终接受结果还要同时查看 COM4 的 CAN_PARAM accepted/rejected。"
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
        try:
            self._send(build_device_id_request(self._node_id(), self._host_id()))
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)

    def read_parameter(self) -> None:
        try:
            parameter = self._selected_parameter()
            self._send(build_parameter_read(parameter.index, self._node_id(), self._host_id()))
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)

    def read_parameter_index(self, index: int) -> None:
        parameter = PARAMETER_BY_INDEX[index]
        self.parameter_var.set(self._parameter_label(index))
        self._on_parameter_selected()
        try:
            self._send(build_parameter_read(parameter.index, self._node_id(), self._host_id()))
        except ValueError as exc:
            messagebox.showerror("参数错误", str(exc), parent=self)

    def write_parameter(self) -> None:
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

    def _send(self, frame: CanFrame) -> None:
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
        self._append_log("rx", f"RX  {format_frame(frame)}\n")
        device = parse_device_id_response(frame, self._host_id())
        if device is not None:
            node_id, uid = device
            self.node_id_var.set(node_id)
            self._append_log(
                "event", f"检测到设备：CAN ID={node_id}，MCU UID=0x{uid:016X}\n"
            )
            return
        comm_type, _data2, _target = split_id(frame.arbitration_id)
        if comm_type == 2 and (self.pending_verification or self.pending_rejection):
            self._append_log(
                "event",
                "收到类型 2 状态反馈；官方协议未在此帧中返回参数写入成败，等待类型 17 回读。\n",
            )
        try:
            result = parse_parameter_response(frame, self._host_id())
        except ValueError as exc:
            self._append_log("error", f"参数响应失败：{exc}\n")
            return
        if result is None:
            return
        index, value = result
        parameter = PARAMETER_BY_INDEX[index]
        shown = f"{value:.7g}" if isinstance(value, float) else str(value)
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
