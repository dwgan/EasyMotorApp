"""RobotJointG5 / STM32G564REZ3_ICMU150 commissioning console.

Protocol-aligned with the current firmware console (motor_command_console.c):

  UART5: 2.5 Mbit/s, 8N1, RS485 half-duplex (hardware DE on PB3)
  Commands: start | iq -100..100 | speed -20..20 | keep | stop |
            status | faultack | help

Telemetry parsed:
  RT           1 s compact runtime line (pwm/enc/slk/dmiss/out/...)
  TORQUE_CMD   explicit `status` reply
  SPEED        explicit `status` reply
  MOTION       1 s motion snapshot and `status` reply
  SPEED_TRACE  100 ms trajectory while speed mode is active
  ENC_ERR      rotor AS5047P health counters
  EANG_RIPPLE  12 electrical-angle ripple bins printed after `stop`

Successful `keep` is silent on the firmware side; a rejected `keep` is
visible.  The MCU remains the final authority for current limits, state
transitions, watchdogs, and faults; this GUI only automates the documented
bench procedure.
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
START_TIMEOUT_MS = 30_000
MAX_IQ_LSB = 100
MAX_SPEED_RPM = 20
RS485_RX_QUIET_MS = 5
RS485_TX_WAIT_MAX_MS = 50
ENCODER_COUNTS_PER_REV = 16_384
NOMINAL_REDUCTION = 9.0
STOP_RETRY_INTERVAL_MS = 150
STOP_MAX_ATTEMPTS = 3
SEQ_WAIT_RUN_TIMEOUT_MS = 30_000
SEQ_WAIT_IDLE_TIMEOUT_MS = 6_000
SEQ_TICK_MS = 100
EANG_COLLECT_TIMEOUT_S = 5.0

MCI_NAMES = {
    0: "IDLE",
    2: "ALIGNMENT",
    4: "START",
    6: "RUN",
    8: "STOP",
    10: "FAULT_NOW",
    11: "FAULT_OVER",
    16: "CHARGE_BOOT_CAP",
    17: "OFFSET_CALIB",
    20: "WAIT_STOP_MOTOR",
}

TORQUE_NAMES = {
    0: "IDLE",
    1: "STARTING",
    2: "RUNNING",
    3: "STOPPING",
    4: "FAULT",
}

MODE_NAMES = {
    0: "TORQUE",
    1: "SPEED",
}

TORQUE_ERROR_BITS = (
    (1, "CMD"),
    (2, "TIMEOUT"),
    (4, "MCI_FAULT"),
    (8, "OVERSPEED"),
    (16, "STALL"),
)

# --------------------------------------------------------------------------
# Firmware telemetry formats (debug_uart.c) -- keep in sync with the console.
# --------------------------------------------------------------------------

TORQUE_CMD_RE = re.compile(
    r"TORQUE_CMD state=(\d+) mci=(\d+) flt=(\d+)/(\d+) "
    r"target/applied=(-?\d+)/(-?\d+) "
    r"cmd=(\d+) timeout=(\d+) err=(\d+)"
)

SPEED_RE = re.compile(
    r"SPEED mode=(\d+) rpm=(-?\d+) "
    r"target/applied/measured=(-?\d+)/(-?\d+)/(-?\d+) "
    r"err=(-?\d+) intiq=(-?\d+)(?: ctrlerr=(\d+))?"
)

MOTION_RE = re.compile(
    r"MOTION valid=(\d+) active=(\d+) "
    r"pos=(-?\d+)\.\.(-?\d+) delta=(-?\d+) vel=(-?\d+) "
    r"eang=(\d+) agecy=(\d+) ccr=(\d+)/(\d+)/(\d+) "
    r"clamp=(\d+) delta=(\d+)"
)

SPEED_TRACE_RE = re.compile(
    r"SPEED_TRACE ms=(\d+) "
    r"target/applied/measured/avg=(-?\d+)/(-?\d+)/(-?\d+)/(-?\d+) "
    r"min/max=(-?\d+)/(-?\d+) iq/ff/p/int="
    r"(-?\d+)/(-?\d+)/(-?\d+)/(-?\d+) "
    r"pos=(-?\d+) eang=(\d+) "
    r"iqd=(-?\d+)/(-?\d+) vqd=(-?\d+)/(-?\d+)"
)

RT_RE = re.compile(
    r"RT t=(\d+) ocp=(\d+) brk=(\d+) mci=(\d+) flt=(\d+) pst=(\d+) "
    r"cmd=(\d+) tr=(\d+) pwm=(\d+) adc=(\d+) pmiss=(\d+) "
    r"pp=(\d+)/(\d+)/(\d+) fbflt=(\d+) recon=(\d+) "
    r"irqmax=(\d+) focmax=(\d+) seg=(\d+)/(\d+)/(\d+)/(\d+)/(\d+) "
    r"slk=(\d+) dmiss=(\d+) enc=(\d+) cerr=(\d+) out=(\d+) "
    r"refq/d=(-?\d+)/(-?\d+) iq/d=(-?\d+)/(-?\d+) "
    r"vq/d=(-?\d+)/(-?\d+) satq/d=(\d+)/(\d+)"
)

ENC_ERR_RE = re.compile(
    r"ENC_ERR spi=(\d+) parity=(\d+) sensor=(\d+) pipeline=(\d+) "
    r"fast_stale=(\d+) health_stale=(\d+) stable_ms=(\d+) agecy=(\d+) "
    r"pub/on/frame/consec=(\d+)/(\d+)/(\d+)/(\d+) "
    r"usable=(\d+)/(\d+) spit=(\d+) spif=(\d+) spiflg=(\d+) snapu=(\d+)"
)

EANG_BIN_RE = re.compile(
    r"EANG_BIN i=(\d+) deg=(\d+)\.\.(\d+) n=(\d+) visits/maxrun=(\d+)/(\d+) "
    r"vel/err=(-?\d+)/(-?\d+) iqcmd/iq/id=(-?\d+)/(-?\d+)/(-?\d+) "
    r"uvw=(-?\d+)/(-?\d+)/(-?\d+) runpos=(-?\d+)\.\.(-?\d+)"
)

EANG_BIN_COUNT = 12


def format_torque_error(error_code: int) -> str:
    """Decode the TORQUE_CMD error bitmask into readable labels."""
    names = [name for bit, name in TORQUE_ERROR_BITS if error_code & bit]
    return "+".join(names) if names else "OK"


class RobotJointApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RobotJointG5 / ICMU150 调试上位机 (Stage-H/I)")
        self.geometry("1080x780")
        self.minsize(860, 640)

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

        self.sequence_active = False
        self.sequence_step = 0
        self.sequence_speed = 5
        self.sequence_duration_ms = 5000
        self.sequence_deadline = 0.0

        # Stage-I full acceptance: [+10, -10, +20, -20] rpm, one click.
        self.acc_active = False
        self.acc_phase = ""  # start | speed | idle | eang
        self.acc_index = 0
        self.acc_schedule: list[tuple[int, int]] = []  # (rpm, duration_ms)
        self.acc_data: list[dict[str, object]] = []
        self.acc_deadline = 0.0
        self.acc_eang_deadline = 0.0
        self.acc_started_mono = 0.0

        self.eangle_pending = False
        self.eangle_bins: list[tuple[int, ...]] = []
        self._rt_health_fragment = ""
        self._enc_health_fragment = ""

        self.port_var = tk.StringVar()
        self.connection_var = tk.StringVar(value="未连接")
        self.state_var = tk.StringVar(value="MCI: 未知")
        self.torque_var = tk.StringVar(value="TORQUE: 未知")
        self.sequence_var = tk.StringVar(value="Stage-I 探测: 空闲")
        self.iq_var = tk.IntVar(value=5)
        self.speed_var = tk.IntVar(value=5)
        self.pulse_duration_var = tk.IntVar(value=5000)
        self.acc_speeds_var = tk.StringVar(value="10,20")
        self.auto_keep_var = tk.BooleanVar(value=False)
        self.keep_status_var = tk.StringVar(value="Keepalive: 关闭")
        self.motion_var = tk.StringVar(value="位置: 等待固件 MOTION 遥测")
        self.angle_var = tk.StringVar(value="电角度/速度: 未知")
        self.pwm_var = tk.StringVar(value="CCR/Clamp: 未知")
        self.speed_control_var = tk.StringVar(value="速度环: 未启用")
        self.health_var = tk.StringVar(value="编码器/实时健康: 未知")
        self.eangle_var = tk.StringVar(value="电角度纹波: 等待停机后 EANG_RIPPLE")

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
        ttk.Label(state_frame, textvariable=self.sequence_var).grid(
            row=0, column=2, padx=(24, 0), sticky="w"
        )
        ttk.Button(
            state_frame, text="查询状态", command=lambda: self.send_command("status")
        ).grid(row=1, column=0, padx=(0, 4), pady=(6, 0), sticky="w")
        ttk.Button(
            state_frame, text="帮助", command=lambda: self.send_command("help")
        ).grid(row=1, column=1, padx=4, pady=(6, 0), sticky="w")
        ttk.Label(state_frame, textvariable=self.torque_var).grid(
            row=1, column=2, padx=(24, 0), pady=(6, 0), sticky="w"
        )
        state_frame.columnconfigure(3, weight=1)

        motion_frame = ttk.LabelFrame(outer, text="运动与 PWM 遥测", padding=10)
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
        ttk.Label(motion_frame, textvariable=self.health_var).grid(
            row=4, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Label(motion_frame, textvariable=self.eangle_var).grid(
            row=5, column=0, sticky="w", pady=(4, 0)
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
            text="持续运行自动 Keepalive（500ms）",
            variable=self.auto_keep_var,
            command=self._update_keepalive_label,
        )
        self.keep_check.grid(row=1, column=0, columnspan=2, padx=4, pady=6, sticky="w")
        ttk.Label(controls, text="测试时长(ms)").grid(
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

        ttk.Label(controls, text="速度 (电机轴 rpm)").grid(
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
            text="Stage-I 双向探测",
            command=self.run_stage_i_probe,
        ).grid(row=3, column=0, padx=4, pady=(6, 0))
        ttk.Button(
            controls, text="中止序列", command=self.abort_sequence
        ).grid(row=3, column=1, padx=4, pady=(6, 0))
        ttk.Button(
            controls,
            text="Stage-I 完整验收",
            command=self.run_stage_i_acceptance,
        ).grid(row=4, column=0, padx=4, pady=(6, 0))
        ttk.Label(controls, text="验收档位 rpm(逗号分隔)").grid(
            row=4, column=1, padx=(4, 0), pady=(6, 0), sticky="e"
        )
        self.acc_speeds_entry = ttk.Entry(
            controls, textvariable=self.acc_speeds_var, width=12
        )
        self.acc_speeds_entry.grid(
            row=4, column=2, padx=4, pady=(6, 0), sticky="w"
        )
        ttk.Button(
            controls,
            text="故障确认",
            command=lambda: self.send_command("faultack"),
        ).grid(row=3, column=3, padx=4, pady=(6, 0))
        ttk.Button(controls, text="清空日志", command=self.clear_log).grid(
            row=3, column=4, padx=4, pady=(6, 0)
        )

        note = (
            "Iq 是转矩命令，不是转速命令。按 10→20→50→100 LSB 逐级测试；"
            "速度测试从 5 motor rpm 开始。定时脉冲/速度测试与 Stage-I 双向探测会自动每 500ms 发送 keep，"
            "到点自动 stop；MCU 自身 1000ms 命令看门狗仍独立生效。"
        )
        ttk.Label(controls, text=note, foreground="#8a4b00").grid(
            row=5, column=0, columnspan=6, pady=(6, 0), sticky="w"
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
        self.sequence_active = False
        self._rt_health_fragment = ""
        self._enc_health_fragment = ""
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
        self.torque_var.set("TORQUE: 未知")
        self.sequence_var.set("Stage-I 探测: 空闲")
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
                self._append_log("event", "已由 MCI 状态确认电机停止。\n")
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
                if self.acc_active and self.acc_phase == "speed":
                    self.acc_data[-1]["watchdog"] = True
            self._update_control_state()

        if "CMD start rejected" in line:
            self.start_waiting = False
            self._append_log("error", "启动被 MCU 拒绝，请查询故障状态。\n")
            if self.sequence_active and self.sequence_step in (0, 3):
                self._sequence_abort("启动被拒绝")
            if self.acc_active and self.acc_phase == "start":
                self._acc_abort("启动被拒绝")
        if "CMD iq rejected" in line:
            self.nonzero_iq_active = False
            self.pulse_active = False
            self._update_keepalive_label()
        if "CMD speed rejected" in line:
            self.nonzero_iq_active = False
            self.pulse_active = False
            self._update_keepalive_label()
            if self.acc_active and self.acc_phase == "speed":
                self._acc_abort("速度命令被 MCU 拒绝")
        if "CMD keep rejected" in line and self.pulse_active:
            self.pulse_active = False
            self.nonzero_iq_active = False
            self._update_keepalive_label()
            self._append_log(
                "error", "Keepalive 被 MCU 拒绝，定时脉冲已在上位机侧终止。\n"
            )
        if "CMD stop accepted" in line:
            self.stop_pending = False
            self.nonzero_iq_active = False
            self.pulse_active = False
            self.auto_keep_var.set(False)
            self._update_keepalive_label()

        torque = TORQUE_CMD_RE.search(line)
        if torque:
            state = int(torque.group(1))
            error = int(torque.group(9))
            command_count = int(torque.group(7))
            timeout_count = int(torque.group(8))
            self.torque_var.set(
                f"TORQUE: state={state} ({TORQUE_NAMES.get(state, '?')}) "
                f"err={format_torque_error(error)} ({error}) "
                f"cmd={command_count} timeout={timeout_count}"
            )
            if (
                self.acc_active
                and self.acc_phase == "speed"
                and (timeout_count > 0 or (error & 2))
            ):
                self.acc_data[-1]["watchdog"] = True

        motion = MOTION_RE.search(line)
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
                f"Clamp总数={clamp_total}，本次脉冲={clamp_delta}"
            )

        speed = SPEED_RE.search(line)
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
                f"速度环: mode={mode}（{MODE_NAMES.get(mode, '?')}），目标 {target_rpm} motor rpm "
                f"({target_cps} count/s)，斜坡 {applied_cps}，"
                f"实测={measured_rpm:.2f} motor rpm / "
                f"{output_rpm:.2f} output rpm，误差={error_cps}，"
                f"积分Iq={integral_iq}，控制错误={control_error}"
            )
            if (
                self.acc_active
                and self.acc_phase == "speed"
                and (control_error & 2)
            ):
                self.acc_data[-1]["watchdog"] = True

        trace = SPEED_TRACE_RE.search(line)
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
            if (
                self.acc_active
                and self.acc_phase == "speed"
                and self.acc_data
            ):
                trace_record = self.acc_data[-1]["traces"]
                trace_record.append(  # type: ignore[union-attr]
                    (
                        elapsed_ms, target_cps, applied_cps, measured_cps,
                        average_cps, minimum_cps, maximum_cps,
                    )
                )

        runtime = RT_RE.search(line)
        if runtime:
            uptime_ms = int(runtime.group(1))
            pwm = int(runtime.group(9))
            pmiss = int(runtime.group(11))
            focmax = int(runtime.group(18))
            slack = int(runtime.group(24))
            dmiss = int(runtime.group(25))
            enc = int(runtime.group(26))
            out = int(runtime.group(28))
            self._update_health_label(
                f"RT t={uptime_ms}ms pwm={pwm} enc={enc} out={out} "
                f"slk={slack} dmiss={dmiss} pmiss={pmiss} focmax={focmax}"
            )

        encoder = ENC_ERR_RE.search(line)
        if encoder:
            spi = int(encoder.group(1))
            fast_stale = int(encoder.group(5))
            stable_ms = int(encoder.group(7))
            usable_fast = int(encoder.group(13))
            usable_health = int(encoder.group(14))
            spit = int(encoder.group(15))
            spif = int(encoder.group(16))
            snapu = int(encoder.group(18))
            self._update_health_label(
                f"ENC spi={spi} spit={spit} spif={spif} snapu={snapu} "
                f"fast_stale={fast_stale} stable={stable_ms / 1000.0:.1f}s "
                f"usable={usable_fast}/{usable_health}"
            )

        if line.startswith("EANG_RIPPLE"):
            self.eangle_pending = True
            self.eangle_bins = []
            self._append_log("event", "EANG_RIPPLE: 开始收集 12 个电角度区间统计。\n")

        if self.eangle_pending:
            bin_match = EANG_BIN_RE.search(line)
            if bin_match:
                values = tuple(int(g) for g in bin_match.groups())
                self.eangle_bins.append(values)
                if len(self.eangle_bins) == EANG_BIN_COUNT:
                    self._render_eangle_summary(self.eangle_bins)
                    self.eangle_pending = False
                    self.eangle_bins = []

    def _update_health_label(self, fragment: str) -> None:
        """Merge RT and ENC health fragments into one compact label."""
        if fragment.startswith("RT t="):
            self._rt_health_fragment = fragment
        elif fragment.startswith("ENC spi="):
            self._enc_health_fragment = fragment
        rt = self._rt_health_fragment
        enc = self._enc_health_fragment
        if rt and enc:
            self.health_var.set(f"{rt} | {enc}")
        elif rt or enc:
            self.health_var.set(rt or enc)
        else:
            self.health_var.set("编码器/实时健康: 未知")

    def _render_eangle_summary(
        self, bins: list[tuple[int, ...]]
    ) -> None:
        if (
            self.acc_active
            and self.acc_phase in ("idle", "eang")
            and self.acc_data
            and not self.acc_data[-1]["eang_bins"]
        ):
            self.acc_data[-1]["eang_bins"] = list(bins)
        lines = ["EANG_RIPPLE 12-bin 摘要（停机后诊断）:"]
        worst: tuple[int, int] | None = None
        for values in bins:
            (
                index, deg_start, deg_end, samples, visits, max_run,
                avg_vel, avg_err, cmd_iq, avg_iq, avg_id,
                phase_u, phase_v, phase_w, run_start, run_end,
            ) = values
            lines.append(
                f"  bin{index:02d} {deg_start:3d}-{deg_end:3d}° "
                f"n={samples:5d} visits={visits:4d} maxrun={max_run:4d} "
                f"vel={avg_vel:7d} err={avg_err:7d} iqcmd={cmd_iq:4d} "
                f"iq={avg_iq:4d} id={avg_id:4d} "
                f"uvw={phase_u:4d}/{phase_v:4d}/{phase_w:4d}"
            )
            if samples > 0 and (worst is None or abs(avg_err) > abs(worst[0])):
                worst = (avg_err, index)
        for line in lines:
            self._append_log("event", line + "\n")
        if worst is not None:
            self.eangle_var.set(
                f"电角度纹波: bin{worst[1]:02d} 平均速度误差最大 "
                f"err={worst[0]} count/s（12-bin 明细见日志）"
            )
        else:
            self.eangle_var.set("电角度纹波: 无有效样本")

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
        if self.sequence_active:
            messagebox.showwarning("序列进行中", "请先中止 Stage-I 探测序列。")
            return
        if not self.send_command("start"):
            return
        self.start_waiting = True
        self.start_deadline = time.monotonic() + START_TIMEOUT_MS / 1000.0
        self.state_var.set("MCI: 正在启动/对齐（被动监听）…")
        self._append_log(
            "event",
            "已发送启动命令；被动监听 RT 状态，不进行周期 status 轮询。\n",
        )
        self._update_control_state()

    def _poll_start_sequence(self) -> None:
        if not self.start_waiting:
            return
        if self.mci_state == 6:
            self.start_waiting = False
            self._append_log("event", "MCU 已进入 RUN，可以施加 Iq/速度。\n")
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
            messagebox.showwarning("速度无效", "请输入 1 到 20 rpm 的整数。")
            return None
        if not 1 <= value <= MAX_SPEED_RPM:
            messagebox.showwarning("速度超限", "当前阶段只允许 1 到 20 motor rpm。")
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

    def start_timed_speed(
        self,
        direction: int,
        value: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if value is None:
            value = self._validated_speed()
        if duration_ms is None:
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
            messagebox.showwarning("时长无效", "请输入 500 到 5000 ms。")
            return None
        if not 500 <= duration_ms <= 5000:
            messagebox.showwarning("时长超限", "允许范围为 500 到 5000 ms。")
            return None
        return duration_ms

    def start_timed_pulse(
        self,
        direction: int,
        value: int | None = None,
        duration_ms: int | None = None,
    ) -> None:
        if value is None:
            value = self._validated_iq()
        if duration_ms is None:
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
        self.keep_status_var.set(f"定时脉冲: {duration_ms} ms，自动 Keepalive")
        self._append_log(
            "event",
            f"定时脉冲开始: Iq={direction * value} LSB, {duration_ms} ms。\n",
        )
        self.after(20, self._pulse_watchdog_tick)

    def _pulse_watchdog_tick(self) -> None:
        if not self.pulse_active:
            return
        if (not self.connected) or (time.monotonic() >= self.pulse_deadline):
            self._append_log("event", "定时测试结束，发送 stop。\n")
            self.stop_motor(cancel_sequence=False)
            return
        self.after(20, self._pulse_watchdog_tick)

    # ------------------------------------------------------------------
    # Stage-I bidirectional probe: start -> +rpm (auto keep) -> stop
    #                                 -> -rpm (auto keep) -> stop
    # ------------------------------------------------------------------

    def run_stage_i_probe(self) -> None:
        if self.sequence_active:
            messagebox.showwarning("序列进行中", "请先中止当前序列。")
            return
        value = self._validated_speed()
        duration_ms = self._validated_pulse_duration()
        if value is None or duration_ms is None:
            return
        if self.mci_state not in (None, 0):
            messagebox.showwarning("未在 IDLE", "请先停止电机再启动双向探测。")
            return
        self.sequence_active = True
        self.sequence_step = 0
        self.sequence_speed = value
        self.sequence_duration_ms = duration_ms
        self.sequence_deadline = time.monotonic() + SEQ_WAIT_RUN_TIMEOUT_MS / 1000.0
        self.sequence_var.set("Stage-I 探测: 发送 start，等待 RUN …")
        self._append_log(
            "event",
            "Stage-I 双向探测开始: start → "
            f"{value:+d} rpm × {duration_ms} ms → stop → "
            f"start → {-value:+d} rpm × {duration_ms} ms → stop。\n",
        )
        if not self.send_command("start"):
            self.sequence_active = False
            self.sequence_var.set("Stage-I 探测: 空闲")
            return
        self._update_control_state()
        self.after(SEQ_TICK_MS, self._sequence_tick)

    # ------------------------------------------------------------------
    # Stage-I full acceptance: one click runs +10 / -10 / +20 / -20 rpm,
    # each start -> timed speed (auto keep) -> stop -> EANG collection,
    # then prints a per-step PASS/FAIL summary.
    # ------------------------------------------------------------------

    def run_stage_i_acceptance(self) -> None:
        if self.acc_active or self.sequence_active:
            messagebox.showwarning("序列进行中", "请先中止当前序列。")
            return
        if self.mci_state not in (None, 0):
            messagebox.showwarning("未在 IDLE", "请先停止电机再开始完整验收。")
            return
        duration_ms = self._validated_pulse_duration()
        if duration_ms is None:
            return
        try:
            speeds = [
                int(part.strip())
                for part in self.acc_speeds_var.get().split(",")
                if part.strip()
            ]
        except ValueError:
            messagebox.showwarning(
                "档位无效", "请用逗号分隔正整数，如 10,20。"
            )
            return
        if not speeds or any(not 1 <= value <= MAX_SPEED_RPM for value in speeds):
            messagebox.showwarning(
                "档位超限", f"每档速度须在 1..{MAX_SPEED_RPM} motor rpm。"
            )
            return
        schedule: list[tuple[int, int]] = []
        for value in speeds:
            schedule.append((+value, duration_ms))
            schedule.append((-value, duration_ms))
        self.acc_schedule = schedule
        self.acc_index = 0
        self.acc_data = []
        self.acc_active = True
        self.acc_phase = "start"
        self.acc_deadline = time.monotonic() + SEQ_WAIT_RUN_TIMEOUT_MS / 1000.0
        self.acc_started_mono = time.monotonic()
        self.eangle_bins = []
        self.eangle_pending = False
        self.sequence_var.set("Stage-I 完整验收: 发送 start，等待 RUN …")
        self._append_log(
            "event",
            "Stage-I 完整验收开始: "
            + " → ".join(
                f"{rpm:+d} rpm×{d} ms" for rpm, d in schedule
            )
            + "。每档自动 keep、自动 stop。\n",
        )
        if not self.send_command("start"):
            self.acc_active = False
            self.sequence_var.set("Stage-I 完整验收: 空闲")
            return
        self._update_control_state()
        self.after(SEQ_TICK_MS, self._acceptance_tick)

    def _acceptance_tick(self) -> None:
        if not self.acc_active:
            return
        if not self.connected:
            self._acc_abort("串口已断开")
            return
        if self.acc_phase == "start":
            if self.mci_state == 6:
                self._acc_begin_speed()
            elif time.monotonic() >= self.acc_deadline:
                self._acc_abort("等待 RUN 超时")
                return
        elif self.acc_phase == "speed":
            # Normal end is signalled by the pulse watchdog having stopped
            # the motor (pulse_active cleared); an unexpected early exit was
            # already flagged as watchdog in _parse_status_line.
            if not self.pulse_active:
                self.acc_phase = "idle"
                self.acc_deadline = (
                    time.monotonic() + SEQ_WAIT_IDLE_TIMEOUT_MS / 1000.0
                )
                self.sequence_var.set(
                    f"Stage-I 完整验收: 第 {self.acc_index + 1}/"
                    f"{len(self.acc_schedule)} 档已停，等待 IDLE …"
                )
        elif self.acc_phase == "idle":
            if self.mci_state == 0:
                self.acc_phase = "eang"
                self.acc_eang_deadline = (
                    time.monotonic() + EANG_COLLECT_TIMEOUT_S
                )
                self.sequence_var.set(
                    f"Stage-I 完整验收: 收集第 {self.acc_index + 1} 档 EANG 数据 …"
                )
            elif time.monotonic() >= self.acc_deadline:
                self._acc_abort("停止超时")
                return
        elif self.acc_phase == "eang":
            eang_done = bool(self.acc_data[-1]["eang_bins"])
            if eang_done or time.monotonic() >= self.acc_eang_deadline:
                self._acc_finish_phase(eang_done)
                self.after(SEQ_TICK_MS, self._acceptance_tick)
                return
        self.after(SEQ_TICK_MS, self._acceptance_tick)

    def _acc_begin_speed(self) -> None:
        rpm, duration_ms = self.acc_schedule[self.acc_index]
        self.acc_data.append(
            {
                "rpm": rpm,
                "duration_ms": duration_ms,
                "traces": [],
                "watchdog": False,
                "eang_bins": [],
            }
        )
        self.acc_phase = "speed"
        self.eangle_bins = []
        self.eangle_pending = False
        self.sequence_var.set(
            f"Stage-I 完整验收: 第 {self.acc_index + 1}/"
            f"{len(self.acc_schedule)} 档 {rpm:+d} rpm × "
            f"{duration_ms} ms（自动 keep）"
        )
        direction = 1 if rpm > 0 else -1
        self.start_timed_speed(
            direction, value=abs(rpm), duration_ms=duration_ms
        )

    def _acc_finish_phase(self, eang_received: bool) -> None:
        record = self.acc_data[self.acc_index]
        traces: list[tuple[int, ...]] = record["traces"]  # type: ignore
        watchdog: bool = record["watchdog"]  # type: ignore
        target_cps = abs(record["rpm"]) * ENCODER_COUNTS_PER_REV / 60.0
        reasons: list[str] = []
        if watchdog:
            reasons.append("看门狗/命令超时停机")
        avg_cps = 0.0
        min_cps = 0
        max_cps = 0
        if traces:
            _, _, _, _, last_avg, last_min, last_max = traces[-1]
            avg_cps = float(last_avg)
            min_cps = int(last_min)
            max_cps = int(last_max)
            # measured avg keeps the direction sign (negative in reverse);
            # compare magnitudes so a reverse run is evaluated against the
            # same target as the forward run.
            error_ratio = (
                abs(avg_cps) / abs(target_cps)
                if target_cps != 0.0
                else float("inf")
            )
            deviation = abs(1.0 - error_ratio)
            if deviation > 0.30:
                reasons.append(
                    f"平均速度偏差 {deviation * 100:.1f}%"
                    "（目标 ±30% 内）"
                )
        else:
            reasons.append("无 SPEED_TRACE 数据")
        eang_bins: list[tuple[int, ...]] = record["eang_bins"]  # type: ignore
        passed = not reasons
        avg_rpm = avg_cps * 60.0 / ENCODER_COUNTS_PER_REV
        lines = [
            f"验收第 {self.acc_index + 1}/{len(self.acc_schedule)} 档: "
            f"{record['rpm']:+d} rpm → {'PASS' if passed else 'FAIL'}"
        ]
        lines.append(
            f"  目标 {target_cps:.0f} count/s；avg {avg_cps:.0f}"
            f"（{avg_rpm:+.2f} rpm）；min/max {min_cps}..{max_cps} count/s"
        )
        for reason in reasons:
            lines.append(f"  失败原因: {reason}")
        if eang_received and eang_bins:
            total_samples = sum(values[3] for values in eang_bins)
            worst = max(eang_bins, key=lambda values: abs(values[6]))
            lines.append(
                f"  EANG: {len(eang_bins)}/{EANG_BIN_COUNT} "
                f"bins, total samples {total_samples}, "
                f"worst bin {worst[0]:02d} "
                f"({worst[1]:d}-{worst[2]:d} deg) err={worst[7]} count/s"
            )
        else:
            lines.append(
                "  EANG: no RIPPLE data received within "
                f"{EANG_COLLECT_TIMEOUT_S:.0f}s "
                "(not a FAIL, informational only)"
            )
        self._append_log("event", "\n".join(lines) + "\n")
        self.acc_index += 1
        if self.acc_index < len(self.acc_schedule):
            # Continue with the next segment: re-start the motor and wait for
            # RUN (the alignment zero is reused on later starts).
            self.acc_phase = "start"
            self.acc_deadline = (
                time.monotonic() + SEQ_WAIT_RUN_TIMEOUT_MS / 1000.0
            )
            self.sequence_var.set(
                f"Stage-I 完整验收: 第 {self.acc_index + 1}/"
                f"{len(self.acc_schedule)} 档，发送 start …"
            )
            if not self.send_command("start"):
                self._acc_abort("下一档 start 发送失败")
                return
        else:
            self._acc_summarize_all()

    def _acc_pass(self, record: dict[str, object]) -> bool:
        traces: list[tuple[int, ...]] = record["traces"]  # type: ignore
        if record["watchdog"] or not traces:
            return False
        target_cps = abs(record["rpm"]) * ENCODER_COUNTS_PER_REV / 60.0
        error_ratio = (
            abs(float(traces[-1][4])) / abs(target_cps)
            if target_cps != 0.0
            else float("inf")
        )
        return abs(1.0 - error_ratio) <= 0.30

    def _acc_summarize_all(self) -> None:
        passed_count = sum(
            1 for record in self.acc_data if self._acc_pass(record)
        )
        failed = [
            f"{record['rpm']:+d} rpm"
            for record in self.acc_data
            if not self._acc_pass(record)
        ]
        elapsed_s = time.monotonic() - self.acc_started_mono
        self.acc_active = False
        self.acc_phase = "done"
        self.sequence_var.set(
            f"Stage-I 完整验收: 完成（{passed_count}/{len(self.acc_data)} 通过）"
        )
        summary = (
            f"Stage-I 完整验收完成: {passed_count}/{len(self.acc_data)} 档通过"
        )
        if failed:
            summary += f"，失败: {', '.join(failed)}。"
        else:
            summary += "，全部通过。"
        summary += f"总耗时约 {elapsed_s:.0f} s。\n"
        self._append_log("event", summary)
        self._update_control_state()

    def _acc_abort(self, reason: str) -> None:
        self.acc_active = False
        self.sequence_var.set(f"Stage-I 完整验收: 中止（{reason}）")
        self._append_log("error", f"Stage-I 完整验收中止: {reason}\n")
        self.stop_motor()
        self._update_control_state()

    def _sequence_tick(self) -> None:
        if not self.sequence_active:
            return
        if not self.connected:
            self._sequence_abort("串口已断开")
            return
        if self.sequence_step == 0:  # wait for RUN
            if self.mci_state == 6:
                self.sequence_step = 1
                self._begin_sequence_phase()
            elif time.monotonic() >= self.sequence_deadline:
                self._sequence_abort("等待 RUN 超时")
                return
        elif self.sequence_step == 1:  # forward timed speed active
            if not self.pulse_active:
                self.sequence_step = 2
                self.sequence_deadline = (
                    time.monotonic() + SEQ_WAIT_IDLE_TIMEOUT_MS / 1000.0
                )
                self.sequence_var.set("Stage-I 探测: 正向已停，等待 IDLE …")
        elif self.sequence_step == 2:  # wait for IDLE after forward
            if self.mci_state == 0:
                self.sequence_step = 3
                self.sequence_deadline = (
                    time.monotonic() + SEQ_WAIT_RUN_TIMEOUT_MS / 1000.0
                )
                self.sequence_var.set("Stage-I 探测: 重新 start，等待 RUN …")
                if not self.send_command("start"):
                    self._sequence_abort("反向 start 发送失败")
                    return
            elif time.monotonic() >= self.sequence_deadline:
                self._sequence_abort("正向停止超时")
                return
        elif self.sequence_step == 3:  # wait for RUN after second start
            if self.mci_state == 6:
                self.sequence_step = 4
                self._begin_sequence_phase()
            elif time.monotonic() >= self.sequence_deadline:
                self._sequence_abort("反向启动超时")
                return
        elif self.sequence_step == 4:  # reverse timed speed active
            if not self.pulse_active:
                self.sequence_step = 5
                self.sequence_deadline = (
                    time.monotonic() + SEQ_WAIT_IDLE_TIMEOUT_MS / 1000.0
                )
                self.sequence_var.set("Stage-I 探测: 反向已停，等待 IDLE …")
        elif self.sequence_step == 5:  # wait for IDLE after reverse
            if self.mci_state == 0:
                self.sequence_active = False
                self.sequence_var.set("Stage-I 探测: 完成")
                self._append_log(
                    "event",
                    "Stage-I 双向探测完成。查看 SPEED_TRACE 与 EANG_RIPPLE 明细。\n",
                )
                self._update_control_state()
                return
            if time.monotonic() >= self.sequence_deadline:
                self._sequence_abort("反向停止超时")
                return
        self.after(SEQ_TICK_MS, self._sequence_tick)

    def _begin_sequence_phase(self) -> None:
        if self.sequence_step == 1:
            self.sequence_var.set(
                f"Stage-I 探测: +{self.sequence_speed} rpm × "
                f"{self.sequence_duration_ms} ms（自动 keep）"
            )
            self.start_timed_speed(
                +1,
                value=self.sequence_speed,
                duration_ms=self.sequence_duration_ms,
            )
        elif self.sequence_step == 4:
            self.sequence_var.set(
                f"Stage-I 探测: {self.sequence_speed} rpm × "
                f"{self.sequence_duration_ms} ms（自动 keep，反向）"
            )
            self.start_timed_speed(
                -1,
                value=self.sequence_speed,
                duration_ms=self.sequence_duration_ms,
            )

    def _sequence_abort(self, reason: str) -> None:
        self.sequence_active = False
        self.sequence_var.set(f"Stage-I 探测: 中止（{reason}）")
        self._append_log("error", f"Stage-I 探测中止: {reason}\n")
        self.stop_motor()
        self._update_control_state()

    def abort_sequence(self) -> None:
        if not self.sequence_active and not self.acc_active:
            return
        if self.sequence_active:
            self.sequence_active = False
            self.sequence_var.set("Stage-I 探测: 已手动中止")
        if self.acc_active:
            self.acc_active = False
            self.sequence_var.set("Stage-I 完整验收: 已手动中止")
        self._append_log("event", "Stage-I 探测已手动中止。\n")
        self.stop_motor()
        self._update_control_state()

    def stop_motor(self, cancel_sequence: bool = True) -> None:
        if cancel_sequence and self.sequence_active:
            self.sequence_active = False
            self.sequence_var.set("Stage-I 探测: 已中止（手动停止）")
        if cancel_sequence and self.acc_active:
            self.acc_active = False
            self.sequence_var.set("Stage-I 完整验收: 已中止（手动停止）")
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
            self._append_log("event", "已确认 MCI 进入 IDLE。\n")
            return
        if self.stop_attempts < STOP_MAX_ATTEMPTS:
            self._append_log(
                "event", f"未收到 Stop 确认，第{self.stop_attempts + 1}次发送。\n"
            )
            self._send_stop_attempt()
            return
        self.stop_pending = False
        self._append_log(
            "error", "Stop 未获得串口确认；停止 Keepalive，等待 MCU 看门狗停机。\n"
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
                    f"定时测试: 剩余约 {remaining_ms} ms，自动 Keepalive"
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
                if self.connected
                and not self.start_waiting
                and not self.sequence_active
                and not self.acc_active
                and self.mci_state == 0
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
