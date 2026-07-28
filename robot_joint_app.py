"""RobotJointG5 commissioning console.

The GUI intentionally exposes only the bounded Stage-H UART command set.  It
does not implement the motor-control policy itself; the MCU remains the final
authority for current limits, state transitions, watchdogs, and faults.
"""

from __future__ import annotations

import queue
import re
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import messagebox, ttk

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:  # Give a useful GUI-free error when launched directly.
    raise SystemExit(
        "缺少 pyserial。请执行: python -m pip install -r requirements.txt"
    ) from exc


BAUD_RATE = 2_500_000
KEEPALIVE_INTERVAL_MS = 500
START_TIMEOUT_MS = 8_000
MAX_IQ_LSB = 100
MAX_SPEED_RPM = 20
RS485_RX_QUIET_MS = 5
RS485_TX_WAIT_MAX_MS = 50
ENCODER_COUNTS_PER_REV = 16_384
NOMINAL_REDUCTION = 9.0
STOP_RETRY_INTERVAL_MS = 150
STOP_MAX_ATTEMPTS = 3

MCI_NAMES = {
    0: "IDLE",
    2: "ALIGNMENT",
    6: "RUN",
}


class RobotJointApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RobotJointG5 调试上位机")
        self.geometry("1020x720")
        self.minsize(820, 580)

        self.serial_port: serial.Serial | None = None
        self.serial_lock = threading.Lock()
        self.rx_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.reader_stop = threading.Event()
        self.reader_thread: threading.Thread | None = None
        self.last_rx_time = 0.0

        self.connected = False
        self.mci_state: int | None = None
        self.start_waiting = False
        self.start_deadline = 0.0
        self.nonzero_iq_active = False
        self.pulse_active = False
        self.pulse_deadline = 0.0
        self.stop_pending = False
        self.stop_attempts = 0

        self.port_var = tk.StringVar()
        self.connection_var = tk.StringVar(value="未连接")
        self.state_var = tk.StringVar(value="MCI: 未知")
        self.iq_var = tk.IntVar(value=5)
        self.speed_var = tk.IntVar(value=5)
        self.pulse_duration_var = tk.IntVar(value=3000)
        self.auto_keep_var = tk.BooleanVar(value=False)
        self.keep_status_var = tk.StringVar(value="Keepalive: 关闭")
        self.motion_var = tk.StringVar(value="位置: 等待固件 MOTION 遥测")
        self.angle_var = tk.StringVar(value="电角度/速度: 未知")
        self.pwm_var = tk.StringVar(value="CCR/Clamp: 未知")
        self.speed_control_var = tk.StringVar(value="速度环: 未启用")

        self._build_ui()
        self.refresh_ports()
        self.after(20, self._process_rx_queue)
        self.after(KEEPALIVE_INTERVAL_MS, self._keepalive_tick)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        connection = ttk.LabelFrame(outer, text="串口连接", padding=10)
        connection.pack(fill=tk.X)
        ttk.Label(connection, text="端口").grid(row=0, column=0, padx=(0, 6))
        self.port_combo = ttk.Combobox(
            connection, textvariable=self.port_var, width=18, state="readonly"
        )
        self.port_combo.grid(row=0, column=1, padx=(0, 6))
        ttk.Button(connection, text="刷新", command=self.refresh_ports).grid(
            row=0, column=2, padx=(0, 12)
        )
        ttk.Label(connection, text=f"{BAUD_RATE:,} baud / 8N1 / RS485").grid(
            row=0, column=3, padx=(0, 12)
        )
        self.connect_button = ttk.Button(
            connection, text="连接", command=self.toggle_connection
        )
        self.connect_button.grid(row=0, column=4)
        ttk.Label(connection, textvariable=self.connection_var).grid(
            row=0, column=5, padx=(12, 0)
        )
        connection.columnconfigure(6, weight=1)

        state_frame = ttk.LabelFrame(outer, text="控制状态", padding=10)
        state_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(
            state_frame, textvariable=self.state_var, font=("Microsoft YaHei UI", 12)
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(state_frame, textvariable=self.keep_status_var).grid(
            row=0, column=1, padx=(24, 0), sticky="w"
        )
        ttk.Button(
            state_frame, text="查询状态", command=lambda: self.send_command("status")
        ).grid(row=0, column=2, padx=(24, 4))
        ttk.Button(
            state_frame, text="帮助", command=lambda: self.send_command("help")
        ).grid(row=0, column=3, padx=4)
        state_frame.columnconfigure(4, weight=1)

        motion_frame = ttk.LabelFrame(outer, text="运动与PWM遥测", padding=10)
        motion_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(motion_frame, textvariable=self.motion_var).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(motion_frame, textvariable=self.angle_var).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Label(motion_frame, textvariable=self.pwm_var).grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Label(motion_frame, textvariable=self.speed_control_var).grid(
            row=3, column=0, sticky="w", pady=(4, 0)
        )
        motion_frame.columnconfigure(0, weight=1)

        controls = ttk.LabelFrame(outer, text="安全控制", padding=10)
        controls.pack(fill=tk.X, pady=(10, 0))

        self.start_button = ttk.Button(
            controls, text="1. 启动并等待 RUN", command=self.start_motor
        )
        self.start_button.grid(row=0, column=0, padx=4, pady=4, sticky="ew")

        ttk.Label(controls, text="Iq (LSB，约10mA/LSB)").grid(
            row=0, column=1, padx=(18, 4)
        )
        self.iq_spin = ttk.Spinbox(
            controls,
            from_=1,
            to=MAX_IQ_LSB,
            textvariable=self.iq_var,
            width=6,
            justify="center",
        )
        self.iq_spin.grid(row=0, column=2, padx=4)

        self.positive_button = ttk.Button(
            controls, text="正向定时脉冲", command=lambda: self.start_timed_pulse(+1)
        )
        self.positive_button.grid(row=0, column=3, padx=4)
        self.negative_button = ttk.Button(
            controls, text="反向定时脉冲", command=lambda: self.start_timed_pulse(-1)
        )
        self.negative_button.grid(row=0, column=4, padx=4)
        ttk.Button(
            controls, text="Iq 归零", command=lambda: self.send_iq(0)
        ).grid(row=0, column=5, padx=4)

        self.keep_check = ttk.Checkbutton(
            controls,
            text="自动 Keepalive（500ms）",
            variable=self.auto_keep_var,
            command=self._update_keepalive_label,
        )
        self.keep_check.grid(row=1, column=0, columnspan=2, padx=4, pady=6, sticky="w")
        ttk.Label(controls, text="脉冲时长(ms)").grid(
            row=1, column=2, padx=(4, 0), sticky="e"
        )
        self.pulse_spin = ttk.Spinbox(
            controls,
            from_=500,
            to=5000,
            increment=500,
            textvariable=self.pulse_duration_var,
            width=7,
            justify="center",
        )
        self.pulse_spin.grid(row=1, column=3, padx=4)
        ttk.Button(
            controls, text="单次 Keep", command=lambda: self.send_command("keep")
        ).grid(row=1, column=4, padx=4)
        ttk.Button(
            controls, text="停止", command=self.stop_motor, style="Stop.TButton"
        ).grid(row=1, column=5, padx=4, sticky="ew")

        ttk.Label(controls, text="速度 (电机轴rpm)").grid(
            row=2, column=0, padx=4, sticky="e"
        )
        self.speed_spin = ttk.Spinbox(
            controls,
            from_=1,
            to=MAX_SPEED_RPM,
            textvariable=self.speed_var,
            width=6,
            justify="center",
        )
        self.speed_spin.grid(row=2, column=1, padx=4)
        self.speed_positive_button = ttk.Button(
            controls,
            text="正向低速测试",
            command=lambda: self.start_timed_speed(+1),
        )
        self.speed_positive_button.grid(row=2, column=2, padx=4)
        self.speed_negative_button = ttk.Button(
            controls,
            text="反向低速测试",
            command=lambda: self.start_timed_speed(-1),
        )
        self.speed_negative_button.grid(row=2, column=3, padx=4)
        ttk.Button(
            controls, text="速度归零", command=lambda: self.send_speed(0)
        ).grid(row=2, column=4, padx=4)
        ttk.Button(
            controls,
            text="故障确认",
            command=lambda: self.send_command("faultack"),
        ).grid(row=3, column=3, padx=4)
        ttk.Button(controls, text="清空日志", command=self.clear_log).grid(
            row=3, column=4, padx=4
        )

        note = (
            "Iq是转矩命令，不是转速命令。按10→20→50→100 LSB逐级测试；"
            "速度测试从5 motor rpm开始。非零命令超过1秒未刷新会由MCU自动停机。"
        )
        ttk.Label(controls, text=note, foreground="#8a4b00").grid(
            row=4, column=0, columnspan=6, pady=(6, 0), sticky="w"
        )
        for column in range(6):
            controls.columnconfigure(column, weight=1 if column in (0, 3) else 0)

        log_frame = ttk.LabelFrame(outer, text="收发日志", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.log = tk.Text(
            log_frame,
            wrap=tk.NONE,
            state=tk.DISABLED,
            font=("Consolas", 10),
            background="#101418",
            foreground="#d9e2ea",
            insertbackground="white",
        )
        y_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        x_scroll = ttk.Scrollbar(
            log_frame, orient=tk.HORIZONTAL, command=self.log.xview
        )
        self.log.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.log.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log.tag_configure("tx", foreground="#76d7ff")
        self.log.tag_configure("rx", foreground="#d9e2ea")
        self.log.tag_configure("error", foreground="#ff7777")
        self.log.tag_configure("event", foreground="#ffe08a")
        self._update_control_state()

    def refresh_ports(self) -> None:
        ports = [port.device for port in list_ports.comports()]
        self.port_combo["values"] = ports
        if self.port_var.get() not in ports:
            self.port_var.set(ports[0] if ports else "")

    def toggle_connection(self) -> None:
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def connect(self) -> None:
        port = self.port_var.get()
        if not port:
            messagebox.showwarning("没有串口", "请连接串口设备并点击刷新。")
            return
        try:
            connection = serial.Serial(
                port=port,
                baudrate=BAUD_RATE,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=0.05,
                write_timeout=0.2,
            )
            connection.reset_input_buffer()
        except serial.SerialException as exc:
            messagebox.showerror("连接失败", str(exc))
            return

        self.serial_port = connection
        self.connected = True
        self.reader_stop.clear()
        self.reader_thread = threading.Thread(
            target=self._reader_loop, name="robot-joint-uart", daemon=True
        )
        self.reader_thread.start()
        self.connection_var.set(f"已连接 {port}")
        self.connect_button.configure(text="断开")
        self._append_log("event", f"已连接 {port} @ {BAUD_RATE}\n")
        self._update_control_state()
        self.after(100, lambda: self.send_command("status"))

    def disconnect(self) -> None:
        self.reader_stop.set()
        connection = self.serial_port
        self.serial_port = None
        self.connected = False
        self.start_waiting = False
        self.nonzero_iq_active = False
        self.pulse_active = False
        self.stop_pending = False
        self.auto_keep_var.set(False)
        if connection is not None:
            try:
                connection.close()
            except serial.SerialException:
                pass
        self.connection_var.set("未连接")
        self.connect_button.configure(text="连接")
        self.mci_state = None
        self.state_var.set("MCI: 未知")
        self._update_keepalive_label()
        self._update_control_state()
        self._append_log("event", "串口已断开\n")

    def _reader_loop(self) -> None:
        pending = bytearray()
        while not self.reader_stop.is_set():
            connection = self.serial_port
            if connection is None:
                return
            try:
                chunk = connection.read(max(connection.in_waiting, 1))
            except (serial.SerialException, OSError) as exc:
                self.rx_queue.put(("error", str(exc)))
                return
            if not chunk:
                continue
            self.last_rx_time = time.monotonic()
            pending.extend(chunk)
            while b"\n" in pending:
                raw_line, _, pending = pending.partition(b"\n")
                line = raw_line.rstrip(b"\r").decode("ascii", errors="replace")
                self.rx_queue.put(("line", line))
        if pending:
            self.rx_queue.put(("line", pending.decode("ascii", errors="replace")))

    def _process_rx_queue(self) -> None:
        try:
            while True:
                kind, payload = self.rx_queue.get_nowait()
                if kind == "line":
                    line = str(payload)
                    self._append_log("rx", f"RX  {line}\n")
                    self._parse_status_line(line)
                else:
                    self._append_log("error", f"串口错误: {payload}\n")
                    if self.connected:
                        self.disconnect()
        except queue.Empty:
            pass
        self._poll_start_sequence()
        self.after(20, self._process_rx_queue)

    def _parse_status_line(self, line: str) -> None:
        match = re.search(r"(?:TORQUE_CMD.*|\bRT\b.*)\bmci=(\d+)", line)
        if match:
            previous_mci_state = self.mci_state
            self.mci_state = int(match.group(1))
            name = MCI_NAMES.get(self.mci_state, "其他")
            self.state_var.set(f"MCI: {self.mci_state} ({name})")
            if self.mci_state == 0 and self.stop_pending:
                self.stop_pending = False
                self._append_log("event", "已由MCI状态确认电机停止。\n")
            if (
                self.mci_state == 0
                and previous_mci_state not in (None, 0)
                and self.pulse_active
            ):
                self.pulse_active = False
                self.nonzero_iq_active = False
                self.auto_keep_var.set(False)
                self._update_keepalive_label()
                self._append_log(
                    "error",
                    "MCU 已主动退出 RUN，定时测试和 Keepalive 已停止。\n",
                )
            self._update_control_state()

        if "CMD start rejected" in line:
            self.start_waiting = False
            self._append_log("error", "启动被 MCU 拒绝，请查询故障状态。\n")
        if "CMD iq rejected" in line:
            self.nonzero_iq_active = False
            self.pulse_active = False
            self._update_keepalive_label()
        if "CMD speed rejected" in line:
            self.nonzero_iq_active = False
            self.pulse_active = False
            self._update_keepalive_label()
        if "CMD keep rejected" in line and self.pulse_active:
            self.pulse_active = False
            self.nonzero_iq_active = False
            self._update_keepalive_label()
            self._append_log(
                "error", "Keepalive被MCU拒绝，定时脉冲已在上位机侧终止。\n"
            )
        if "CMD stop accepted" in line:
            self.stop_pending = False
            self.nonzero_iq_active = False
            self.pulse_active = False
            self.auto_keep_var.set(False)
            self._update_keepalive_label()

        motion = re.search(
            r"MOTION valid=(\d+) active=(\d+) "
            r"pos=(-?\d+)\.\.(-?\d+) delta=(-?\d+) vel=(-?\d+) "
            r"eang=(\d+) agecy=(\d+) ccr=(\d+)/(\d+)/(\d+) "
            r"clamp=(\d+) delta=(\d+)",
            line,
        )
        if motion:
            valid, active = int(motion.group(1)), int(motion.group(2))
            start_pos, current_pos = int(motion.group(3)), int(motion.group(4))
            delta, velocity = int(motion.group(5)), int(motion.group(6))
            electrical_angle, age_cycles = int(motion.group(7)), int(motion.group(8))
            ccr_u, ccr_v, ccr_w = (
                int(motion.group(9)),
                int(motion.group(10)),
                int(motion.group(11)),
            )
            clamp_total, clamp_delta = int(motion.group(12)), int(motion.group(13))
            motor_degrees = delta * 360.0 / ENCODER_COUNTS_PER_REV
            output_degrees = motor_degrees / NOMINAL_REDUCTION
            self.motion_var.set(
                f"位置: {start_pos} → {current_pos}，Δ={delta} count，"
                f"电机Δ={motor_degrees:.3f}°，估算输出Δ={output_degrees:.3f}°，"
                f"有效={valid} 脉冲={active}"
            )
            self.angle_var.set(
                f"速度={velocity} count/s，电角度={electrical_angle}，"
                f"编码器年龄={age_cycles} cycle"
            )
            self.pwm_var.set(
                f"CCR U/V/W={ccr_u}/{ccr_v}/{ccr_w}，"
                f"Clamp总数={clamp_total}，本脉冲={clamp_delta}"
            )

        speed = re.search(
            r"SPEED mode=(\d+) rpm=(-?\d+) "
            r"target/applied/measured=(-?\d+)/(-?\d+)/(-?\d+) "
            r"err=(-?\d+) intiq=(-?\d+)(?: ctrlerr=(\d+))?",
            line,
        )
        if speed:
            mode = int(speed.group(1))
            target_rpm = int(speed.group(2))
            target_cps = int(speed.group(3))
            applied_cps = int(speed.group(4))
            measured_cps = int(speed.group(5))
            error_cps = int(speed.group(6))
            integral_iq = int(speed.group(7))
            control_error = int(speed.group(8) or 0)
            measured_rpm = measured_cps * 60.0 / ENCODER_COUNTS_PER_REV
            output_rpm = measured_rpm / NOMINAL_REDUCTION
            self.speed_control_var.set(
                f"速度环: mode={mode}，目标={target_rpm} motor rpm "
                f"({target_cps} count/s)，斜坡={applied_cps}，"
                f"实测={measured_rpm:.2f} motor rpm / "
                f"{output_rpm:.2f} output rpm，误差={error_cps}，"
                f"积分Iq={integral_iq}，控制错误={control_error}"
            )

        trace = re.search(
            r"SPEED_TRACE ms=(\d+) "
            r"target/applied/measured/avg=(-?\d+)/(-?\d+)/(-?\d+)/(-?\d+) "
            r"min/max=(-?\d+)/(-?\d+) iq/ff/p/int="
            r"(-?\d+)/(-?\d+)/(-?\d+)/(-?\d+) "
            r"pos=(-?\d+) eang=(\d+) "
            r"iqd=(-?\d+)/(-?\d+) vqd=(-?\d+)/(-?\d+)",
            line,
        )
        if trace:
            elapsed_ms = int(trace.group(1))
            target_cps = int(trace.group(2))
            applied_cps = int(trace.group(3))
            measured_cps = int(trace.group(4))
            average_cps = int(trace.group(5))
            minimum_cps = int(trace.group(6))
            maximum_cps = int(trace.group(7))
            iq_lsb = int(trace.group(8))
            feedforward_iq = int(trace.group(9))
            proportional_iq = int(trace.group(10))
            integral_iq = int(trace.group(11))
            position_delta = int(trace.group(12))
            electrical_angle = int(trace.group(13))
            measured_iq = int(trace.group(14))
            measured_id = int(trace.group(15))
            voltage_q = int(trace.group(16))
            voltage_d = int(trace.group(17))
            target_rpm = target_cps * 60.0 / ENCODER_COUNTS_PER_REV
            measured_rpm = measured_cps * 60.0 / ENCODER_COUNTS_PER_REV
            average_rpm = average_cps * 60.0 / ENCODER_COUNTS_PER_REV
            self.speed_control_var.set(
                f"速度轨迹 {elapsed_ms} ms: 目标={target_rpm:.2f} rpm，"
                f"实测={measured_rpm:.2f} rpm，平均={average_rpm:.2f} rpm，"
                f"范围={minimum_cps}..{maximum_cps} count/s，"
                f"Iq/前馈/P/积分={iq_lsb}/{feedforward_iq}/"
                f"{proportional_iq}/{integral_iq} LSB，"
                f"位置增量={position_delta}，电角度={electrical_angle}，"
                f"Iq/Id={measured_iq}/{measured_id}，"
                f"Vq/Vd={voltage_q}/{voltage_d}，斜坡={applied_cps}"
            )

    def send_command(self, command: str, quiet: bool = False) -> bool:
        if not self.connected or self.serial_port is None:
            if not quiet:
                messagebox.showwarning("未连接", "请先连接串口。")
            return False
        payload = (command.strip() + "\r\n").encode("ascii")
        wait_deadline = time.monotonic() + RS485_TX_WAIT_MAX_MS / 1000.0
        while time.monotonic() < wait_deadline:
            receive_quiet = (
                time.monotonic() - self.last_rx_time
                >= RS485_RX_QUIET_MS / 1000.0
            )
            try:
                fifo_empty = self.serial_port.in_waiting == 0
            except (serial.SerialException, OSError):
                fifo_empty = False
            if receive_quiet and fifo_empty:
                break
            time.sleep(0.001)
        try:
            with self.serial_lock:
                self.serial_port.write(payload)
                self.serial_port.flush()
        except (serial.SerialException, serial.SerialTimeoutException, OSError) as exc:
            self._append_log("error", f"发送失败: {exc}\n")
            return False
        if not quiet:
            self._append_log("tx", f"TX  {command.strip()}\n")
        return True

    def start_motor(self) -> None:
        if not self.send_command("start"):
            return
        self.start_waiting = True
        self.start_deadline = time.monotonic() + START_TIMEOUT_MS / 1000.0
        self.state_var.set("MCI: 正在启动/对齐（被动监听）…")
        self._append_log(
            "event", "已发送启动命令；被动监听RT状态，不进行周期status轮询。\n"
        )
        self._update_control_state()

    def _poll_start_sequence(self) -> None:
        if not self.start_waiting:
            return
        if self.mci_state == 6:
            self.start_waiting = False
            self._append_log("event", "MCU 已进入 RUN，可以施加 Iq。\n")
            self._update_control_state()
            return
        if time.monotonic() >= self.start_deadline:
            self.start_waiting = False
            self._append_log("error", "等待 RUN 超时，请检查状态和故障。\n")
            self._update_control_state()
            return

    def _validated_iq(self) -> int | None:
        try:
            value = int(self.iq_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("Iq 无效", "请输入 1 到 100 的整数。")
            return None
        if not 1 <= value <= MAX_IQ_LSB:
            messagebox.showwarning("Iq 超限", "当前调试阶段只允许 1 到 100 LSB。")
            return None
        return value

    def apply_iq(self, direction: int) -> None:
        value = self._validated_iq()
        if value is None:
            return
        if self.mci_state != 6:
            messagebox.showwarning("尚未 RUN", "请先启动，并等待 MCI 进入 RUN。")
            return
        self.send_iq(direction * value)

    def send_iq(self, value: int) -> bool:
        if self.send_command(f"iq {value}"):
            self.nonzero_iq_active = value != 0
            if value == 0:
                self.auto_keep_var.set(False)
            self._update_keepalive_label()
            return True
        return False

    def _validated_speed(self) -> int | None:
        try:
            value = int(self.speed_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("速度无效", "请输入1到20 rpm的整数。")
            return None
        if not 1 <= value <= MAX_SPEED_RPM:
            messagebox.showwarning("速度超限", "当前阶段只允许1到20 motor rpm。")
            return None
        return value

    def send_speed(self, value: int) -> bool:
        if self.send_command(f"speed {value}"):
            self.nonzero_iq_active = value != 0
            if value == 0:
                self.auto_keep_var.set(False)
            self._update_keepalive_label()
            return True
        return False

    def start_timed_speed(self, direction: int) -> None:
        value = self._validated_speed()
        duration_ms = self._validated_pulse_duration()
        if value is None or duration_ms is None:
            return
        if self.mci_state != 6:
            messagebox.showwarning("尚未 RUN", "请先启动，并等待 MCI 进入 RUN。")
            return
        if self.pulse_active:
            messagebox.showwarning("测试进行中", "请先停止当前测试。")
            return
        command_rpm = direction * value
        if not self.send_speed(command_rpm):
            return
        self.pulse_active = True
        self.pulse_deadline = time.monotonic() + duration_ms / 1000.0
        self.keep_status_var.set(
            f"定时速度测试: {command_rpm} motor rpm，{duration_ms} ms"
        )
        self._append_log(
            "event",
            f"定时速度测试开始: {command_rpm} motor rpm，{duration_ms} ms。\n",
        )
        self.after(20, self._pulse_watchdog_tick)

    def _validated_pulse_duration(self) -> int | None:
        try:
            duration_ms = int(self.pulse_duration_var.get())
        except (tk.TclError, ValueError):
            messagebox.showwarning("脉冲时长无效", "请输入500到5000 ms。")
            return None
        if not 500 <= duration_ms <= 5000:
            messagebox.showwarning("脉冲时长超限", "允许范围为500到5000 ms。")
            return None
        return duration_ms

    def start_timed_pulse(self, direction: int) -> None:
        value = self._validated_iq()
        duration_ms = self._validated_pulse_duration()
        if value is None or duration_ms is None:
            return
        if self.mci_state != 6:
            messagebox.showwarning("尚未 RUN", "请先启动，并等待 MCI 进入 RUN。")
            return
        if self.pulse_active:
            messagebox.showwarning("脉冲进行中", "请等待当前脉冲结束或点击停止。")
            return
        if not self.send_iq(direction * value):
            return
        self.pulse_active = True
        self.pulse_deadline = time.monotonic() + duration_ms / 1000.0
        self.keep_status_var.set(f"定时脉冲: {duration_ms} ms，自动Keepalive")
        self._append_log(
            "event",
            f"定时脉冲开始: Iq={direction * value} LSB, {duration_ms} ms。\n",
        )
        self.after(20, self._pulse_watchdog_tick)

    def _pulse_watchdog_tick(self) -> None:
        if not self.pulse_active:
            return
        if (not self.connected) or (time.monotonic() >= self.pulse_deadline):
            self._append_log("event", "定时脉冲结束，发送stop。\n")
            self.stop_motor()
            return
        self.after(20, self._pulse_watchdog_tick)

    def stop_motor(self) -> None:
        self.pulse_active = False
        self.nonzero_iq_active = False
        self.auto_keep_var.set(False)
        self._update_keepalive_label()
        self.stop_pending = True
        self.stop_attempts = 0
        self._send_stop_attempt()

    def _send_stop_attempt(self) -> None:
        if not self.stop_pending or not self.connected:
            return
        self.stop_attempts += 1
        self.send_command("stop", quiet=self.stop_attempts > 1)
        self.after(STOP_RETRY_INTERVAL_MS, self._stop_retry_tick)

    def _stop_retry_tick(self) -> None:
        if not self.stop_pending:
            return
        if self.mci_state == 0:
            self.stop_pending = False
            self._append_log("event", "已确认MCI进入IDLE。\n")
            return
        if self.stop_attempts < STOP_MAX_ATTEMPTS:
            self._append_log(
                "event", f"未收到Stop确认，第{self.stop_attempts + 1}次发送。\n"
            )
            self._send_stop_attempt()
            return
        self.stop_pending = False
        self._append_log(
            "error", "Stop未获得串口确认；停止Keepalive，等待MCU看门狗停机。\n"
        )

    def _keepalive_tick(self) -> None:
        if (
            self.connected
            and (self.auto_keep_var.get() or self.pulse_active)
            and self.nonzero_iq_active
        ):
            self.send_command("keep", quiet=True)
            if self.pulse_active:
                remaining_ms = max(
                    0, int((self.pulse_deadline - time.monotonic()) * 1000)
                )
                self.keep_status_var.set(
                    f"定时脉冲: 剩余约{remaining_ms} ms，自动Keepalive"
                )
            else:
                self.keep_status_var.set("Keepalive: 自动刷新中")
        self.after(KEEPALIVE_INTERVAL_MS, self._keepalive_tick)

    def _update_keepalive_label(self) -> None:
        if self.auto_keep_var.get() and self.nonzero_iq_active:
            self.keep_status_var.set("Keepalive: 自动刷新中")
        elif self.auto_keep_var.get():
            self.keep_status_var.set("Keepalive: 已勾选，等待非零 Iq")
        else:
            self.keep_status_var.set("Keepalive: 关闭")

    def _update_control_state(self) -> None:
        connected_state = tk.NORMAL if self.connected else tk.DISABLED
        self.start_button.configure(
            state=(
                tk.NORMAL
                if self.connected and not self.start_waiting and self.mci_state == 0
                else tk.DISABLED
            )
        )
        torque_state = (
            tk.NORMAL if self.connected and self.mci_state == 6 else tk.DISABLED
        )
        self.positive_button.configure(state=torque_state)
        self.negative_button.configure(state=torque_state)
        self.iq_spin.configure(state=connected_state)
        self.speed_spin.configure(state=connected_state)
        self.pulse_spin.configure(state=connected_state)
        self.keep_check.configure(state=connected_state)
        self.speed_positive_button.configure(state=torque_state)
        self.speed_negative_button.configure(state=torque_state)

    def clear_log(self) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)

    def _append_log(self, tag: str, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self.log.configure(state=tk.NORMAL)
        self.log.insert(tk.END, f"[{timestamp}] {text}", tag)
        self.log.see(tk.END)
        self.log.configure(state=tk.DISABLED)

    def _on_close(self) -> None:
        if self.connected and self.serial_port is not None:
            try:
                self.send_command("stop", quiet=True)
                time.sleep(0.05)
            except Exception:
                pass
        self.disconnect()
        self.destroy()


if __name__ == "__main__":
    RobotJointApp().mainloop()
