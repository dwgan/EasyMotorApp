"""EasyMotor demonstration and engineering console.

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
  PWM_TEST     boot/verbose PWM bring-up line (freq/ARR/CCR)
  ENCODER_RT   boot line advertising rotor-frame and FOC rates
  WAVE_STREAM  binary ADC phase-current frames while "wave on" is active
  CAN_NODE     RS04 CAN node boot/status lines and debug commands

Successful `keep` is silent on the firmware side; a rejected `keep` is
visible.  The MCU remains the final authority for current limits, state
transitions, watchdogs, and faults; this GUI only automates the documented
bench procedure.
"""

from __future__ import annotations

import queue
import re
import sys
import threading
import time
import tkinter as tk
from collections import deque
from datetime import datetime
from pathlib import Path
from tkinter import messagebox as tk_messagebox, ttk
import os

try:
    import serial
    from serial.tools import list_ports
except ImportError as exc:  # Give a useful GUI-free error when launched directly.
    raise SystemExit(
        "缺少 pyserial。请执行: python -m pip install -r requirements.txt"
    ) from exc

from easymotor.branding import apply_window_icon, configure_windows_app_id
from easymotor.features.can_tool import CanToolWindow
from easymotor.features.demo import DemoView
from easymotor.features.update_dialog import UpdateDialog
from easymotor.i18n import (
    DEFAULT_LANGUAGE,
    LocalizedMessageBox,
    LocalizedStringVar,
    localize_legacy,
    tr,
)
from easymotor.protocols.can_motor import (
    CanFrame,
    MODE_CALIBRATING,
    MODE_MOTOR,
    MODE_RESET,
    format_frame,
    parse_device_id_response,
    parse_fault_report,
    parse_feedback,
)
from easymotor.services.demo_service import DemoAction, DemoError, DemoPhase, DemoService
from easymotor.theme import (
    LOG_BACKGROUND,
    LOG_ERROR,
    LOG_EVENT,
    LOG_FOREGROUND,
    LOG_TX,
    MUTED_TEXT,
    WARNING_TEXT,
    WAVE_BACKGROUND,
    WAVE_GRID,
    WAVE_LABEL,
    WAVE_U,
    WAVE_V,
    WAVE_W,
    apply_window_surface,
    configure_theme,
)
from easymotor.transports import USB_CAN_BAUD, UsbCanMotorTransport
from easymotor.updates import UpdateRelease, launch_update_helper
from easymotor.updates.installer import acknowledge_healthy_start, health_marker_from_argv
from easymotor.version import __version__, window_title


messagebox = LocalizedMessageBox(tk_messagebox)


BAUD_RATE = 2_500_000
CAN_ENUMERATION_TIMEOUT_MS = 1_000
KEEPALIVE_INTERVAL_MS = 250
START_TIMEOUT_MS = 30_000
MAX_IQ_LSB = 100
MAX_SPEED_RPM = 20
RS485_RX_QUIET_MS = 20
RS485_TX_WAIT_MAX_MS = 50
ENCODER_COUNTS_PER_REV = 16_384
NOMINAL_REDUCTION = 9.0
STOP_RETRY_INTERVAL_MS = 150
STOP_MAX_ATTEMPTS = 3
SEQ_WAIT_RUN_TIMEOUT_MS = 30_000
SEQ_WAIT_IDLE_TIMEOUT_MS = 6_000
SEQ_TICK_MS = 100
EANG_COLLECT_TIMEOUT_S = 5.0

ENGINEER_TEXT_EN = {
    "工程师模式": "Advanced Engineering",
    "返回演示模式": "Back to Demo",
    "串口连接": "RS485 Connection",
    "端口": "COM port",
    "刷新": "Refresh",
    "连接": "Connect",
    "断开": "Disconnect",
    "控制状态": "Control Status",
    "查询状态": "Query Status",
    "帮助": "Help",
    "波形窗口": "Waveform",
    "运动与 PWM 遥测": "Motion and PWM Telemetry",
    "CAN 调试 (RS04 从机 / 1Mbps 经典扩展帧)": "CAN Diagnostics (RS04 node / 1 Mbps extended)",
    "正常模式": "CAN Normal",
    "待机": "CAN Standby",
    "自回环": "Loopback",
    "编解码自检": "Codec Test",
    "上报开": "Reports On",
    "上报关": "Reports Off",
    "USB-CAN 参数验收": "USB-CAN Parameters",
    "安全控制": "Protected Controls",
    "启动与停止": "Start and Stop",
    "转矩测试": "Torque Test",
    "速度测试": "Speed Test",
    "持续运行": "Continuous Run",
    "Stage-I 验收": "Stage-I Acceptance",
    "高级诊断": "Advanced Diagnostics",
    "1. 启动并等待 RUN": "1. Enable and wait for RUN",
    "Iq (LSB，约10mA/LSB)": "Iq (LSB, approx. 10 mA/LSB)",
    "正向定时脉冲": "Forward Iq pulse",
    "反向定时脉冲": "Reverse Iq pulse",
    "Iq 归零": "Zero Iq",
    "持续运行自动 Keepalive（250ms）": "Continuous keepalive (250 ms)",
    "测试时长(ms)": "Duration (ms)",
    "单次 Keep": "Send Keep",
    "停止": "STOP",
    "速度 (电机轴 rpm)": "Speed (motor-shaft rpm)",
    "正向低速测试": "Forward timed run",
    "反向低速测试": "Reverse timed run",
    "持续正向": "Forward continuous",
    "持续反向": "Reverse continuous",
    "速度归零": "Zero speed",
    "Stage-I 双向探测": "Stage-I bidirectional probe",
    "中止序列": "Abort sequence",
    "Stage-I 完整验收": "Stage-I full acceptance",
    "验收档位 rpm(逗号分隔)": "Acceptance rpm (comma-separated)",
    "故障确认": "Acknowledge fault",
    "清空日志": "Clear log",
    "固定占空比保持(hold)": "Fixed-duty hold",
    "偏移(‰ 5..150)": "Offset (‰ 5..150)",
    "收发日志": "Communication Log",
    "独立日志窗口": "Open log window",
    "波形流期间日志与波形并行显示；可导出为文本文件。": "Logs remain available during waveform streaming and can be exported.",
    (
        "Iq 是转矩命令，不是转速命令。按 10→20→50→100 LSB 逐级测试；"
        "速度测试从 5 motor rpm 开始。定时脉冲/速度测试与 Stage-I 双向探测会自动每 500ms 发送 keep，"
        "到点自动 stop；“持续正向/持续反向”会一直运行直到按停止（长稳验收用），"
        "同样自动 keep；MCU 自身 1000ms 命令看门狗仍独立生效。"
    ): (
        "Iq is torque, not speed. Increase it stepwise (10→20→50→100 LSB); start speed tests at "
        "5 motor rpm. Timed tests and Stage-I sequences refresh keep automatically and stop at the "
        "deadline. Continuous runs last until STOP. The MCU 1000 ms watchdog remains independent."
    ),
    "开始波形": "Start waveform",
    "停止波形": "Stop waveform",
    "分频(1..100)": "Decimation (1..100)",
    "Y量程": "Y range",
    "自动保存CSV": "Auto-save CSV",
    "包络模式(减带宽)": "Envelope mode",
    "保存波形": "Save waveform",
    "导出日志": "Export log",
}

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

PWM_TEST_RE = re.compile(
    r"PWM_TEST state=(\d+) err=(\d+) freq=(\d+)Hz arr=(\d+)"
)

ENCODER_RT_RE = re.compile(
    r"ENCODER_RT ACTIVE: (\d+)kHz PendSV rotor frame, "
    r"(\d+)kHz FOC uses latest angle"
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

WAVE_SOF = 0xA5
WAVE_FRAME_LEN = 10
WAVE_STATS_SOF = 0xA6
WAVE_STATS_FRAME_LEN = 16
WAVE_END_SEQ = 0xFFFF
WAVE_BUFFER_MAX = 1500
WAVE_GLITCH_LSB = 60

CAN_READY_RE = re.compile(r"CAN_NODE READY")
CAN_NODE_ID_RE = re.compile(r"CAN_NODE_ID=(\d+)")
CAN_MASTER_ID_RE = re.compile(r"CAN_MASTER_ID=(\d+)")
CAN_STBY_NORMAL_RE = re.compile(r"CMD can stby 0: transceiver normal mode")
CAN_STBY_STANDBY_RE = re.compile(r"CMD can stby 1: transceiver standby")
CAN_STATUS_HEAD_RE = re.compile(r"CAN_NODE STATUS")
CAN_STATUS_ID_RE = re.compile(r"^  (node_id|master_id)=0x(\d+)$")
CAN_STATUS_FIELD_RE = re.compile(r"^  (\w+)=(\d+)$")
CAN_LOOP_PASS_RE = re.compile(r"CAN_LOOP PASS")
CAN_LOOP_FAIL_RE = re.compile(r"CAN_LOOP FAIL")
CAN_CODEC_PASS_RE = re.compile(r"CAN_CODEC PASS")
CAN_CODEC_FAIL_RE = re.compile(r"CAN_CODEC FAIL")


def format_torque_error(error_code: int) -> str:
    """Decode the TORQUE_CMD error bitmask into readable labels."""
    names = [name for bit, name in TORQUE_ERROR_BITS if error_code & bit]
    return "+".join(names) if names else "OK"


class EasyMotorApp(tk.Tk):
    def __init__(self) -> None:
        configure_windows_app_id()
        super().__init__()
        apply_window_icon(self, set_default=True)
        configure_theme(self)
        self.title(window_title())
        self.geometry("900x680")
        self.minsize(820, 620)

        self.serial_port: serial.Serial | None = None
        self.serial_lock = threading.Lock()
        self.rx_queue: queue.Queue[tuple[str, object]] = queue.Queue()
        self.reader_stop = threading.Event()
        self.reader_thread: threading.Thread | None = None
        self.last_rx_time = 0.0

        self.connected = False
        self.interface_var = tk.StringVar(value="can")
        self.language_var = tk.StringVar(value=DEFAULT_LANGUAGE)
        messagebox.set_language_getter(self.language_var.get)
        self.active_interface: str | None = None
        self.can_transport: UsbCanMotorTransport | None = None
        self.can_uid: int | None = None
        self.can_last_feedback_time = 0.0
        self.can_last_feedback_log_time = 0.0
        self.can_command_rpm = 0
        self.can_enumeration_deadline = 0.0
        self.can_stop_not_before = 0.0
        self.mci_state: int | None = None
        self.start_waiting = False
        self.start_deadline = 0.0
        self.nonzero_iq_active = False
        self.pulse_active = False
        self.continuous_active = False
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
        localized_var = lambda value="": LocalizedStringVar(self, self.language_var.get, value)
        self.connection_var = localized_var(tr(DEFAULT_LANGUAGE, "not_connected"))
        self.state_var = localized_var("MCI: 未知")
        self.torque_var = localized_var("TORQUE: 未知")
        self.sequence_var = localized_var("Stage-I 探测: 空闲")
        self.iq_var = tk.IntVar(value=5)
        self.speed_var = tk.IntVar(value=5)
        self.pulse_duration_var = tk.IntVar(value=5000)
        self.hold_offset_var = tk.IntVar(value=10)
        self.acc_speeds_var = tk.StringVar(value="10,20")
        self.auto_keep_var = tk.BooleanVar(value=False)
        self.keep_status_var = localized_var("Keepalive: 关闭")
        self.keep_sent_count = 0
        self.keep_busy_retry_count = 0
        self.keep_forced_count = 0
        self.freq_var = localized_var("PWM/FOC: 未知")
        self._freq_pwm_hz: int | None = None
        self._freq_foc_hz: int | None = None
        self._freq_enc_rt_hz: int | None = None
        self._rt_last_t_ms: int | None = None
        self._rt_last_adc: int | None = None
        self.motion_var = localized_var("位置: 等待固件 MOTION 遥测")
        self.angle_var = localized_var("电角度/速度: 未知")
        self.pwm_var = localized_var("CCR/Clamp: 未知")
        self.speed_control_var = localized_var("速度环: 未启用")
        self.health_var = localized_var("编码器/实时健康: 未知")
        self.eangle_var = localized_var("电角度纹波: 等待停机后 EANG_RIPPLE")

        self.streaming = False
        self.wave_dec_var = tk.IntVar(value=10)
        self.wave_u_var = tk.BooleanVar(value=True)
        self.wave_v_var = tk.BooleanVar(value=True)
        self.wave_w_var = tk.BooleanVar(value=True)
        self.wave_stats_var = tk.BooleanVar(value=False)
        self.wave_scale_var = localized_var("自动")
        self.wave_status_var = localized_var("波形: 停止")
        self._wave_buffers: dict[str, deque] = {
            "u": deque(maxlen=WAVE_BUFFER_MAX),
            "v": deque(maxlen=WAVE_BUFFER_MAX),
            "w": deque(maxlen=WAVE_BUFFER_MAX),
        }
        self._wave_stats_entries: deque = deque(maxlen=WAVE_BUFFER_MAX)
        self._wave_last_seq: int | None = None
        self._wave_frame_count = 0
        self._wave_lost_count = 0
        self._wave_glitch_count = 0
        self._wave_prev_raw: tuple[int, int, int] | None = None
        self._wave_off_deadline: float | None = None
        self.wave_save_var = tk.BooleanVar(value=False)
        self._wave_csv_file = None
        self._wave_csv_rows: list[str] = []
        self._wave_csv_path: str | None = None
        self.wave_popup: tk.Toplevel | None = None
        self.wave_popup_canvas: tk.Canvas | None = None
        self.wave_toggle_button: ttk.Button | None = None
        self.log_popup: tk.Toplevel | None = None
        self.log_popup_text: tk.Text | None = None
        self._log_entries: list[tuple[str, str, str]] = []
        self.can_ready = False
        self.can_node_id: int | None = None
        self.can_master_id: int | None = None
        self.can_normal = False
        self.can_rx_frames = 0
        self.can_tx_requests = 0
        self.can_tx_fail = 0
        self.can_tx_err = 0
        self.can_rx_err = 0
        self.can_bus_off = 0
        self.can_accepted = 0
        self.can_active_report = False
        self.can_status_var = localized_var("CAN: 未初始化")
        self.can_tool_window: CanToolWindow | None = None
        self.update_dialog: UpdateDialog | None = None
        self.demo_service = DemoService()
        self.app_mode = "demo"

        self._build_ui()
        self.refresh_ports()
        self.after(20, self._process_rx_queue)
        self.after(KEEPALIVE_INTERVAL_MS, self._keepalive_tick)
        self.after(50, self._wave_redraw)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self.copyright_var = tk.StringVar(
            value=tr(self.language_var.get(), "copyright")
        )
        footer = ttk.Frame(self, padding=(12, 2))
        footer.pack(side=tk.BOTTOM, fill=tk.X)
        self.update_button = ttk.Button(
            footer,
            text=tr(self.language_var.get(), "check_updates"),
            command=self.open_update_dialog,
        )
        self.update_button.pack(side=tk.LEFT)
        ttk.Label(
            footer,
            textvariable=self.copyright_var,
            foreground=MUTED_TEXT,
            anchor=tk.E,
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True)

        self.demo_view = DemoView(
            self,
            port_var=self.port_var,
            connection_var=self.connection_var,
            interface_var=self.interface_var,
            language_var=self.language_var,
            on_refresh=self.refresh_ports,
            on_toggle_connection=self.toggle_connection,
            on_interface_changed=self.on_interface_changed,
            on_language_changed=self.on_language_changed,
            on_run=self.start_demo_run,
            on_stop=self.stop_motor,
            on_engineer_mode=self.show_engineer_mode,
        )
        self.engineer_view = ttk.Frame(self, padding=12)
        self._build_engineer_ui(self.engineer_view)
        self.show_demo_mode(force=True)

    def _build_engineer_ui(self, outer: ttk.Frame) -> None:
        engineer_header = ttk.Frame(outer)
        engineer_header.pack(fill=tk.X)
        ttk.Label(
            engineer_header,
            text="工程师模式",
            font=("Microsoft YaHei UI", 16, "bold"),
        ).pack(side=tk.LEFT)
        ttk.Button(
            engineer_header, text="返回演示模式", command=self.show_demo_mode
        ).pack(side=tk.RIGHT)

        connection = ttk.LabelFrame(outer, text="串口连接", padding=10)
        connection.pack(fill=tk.X, pady=(10, 0))
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

        self.engineer_notebook = ttk.Notebook(outer)
        self.engineer_notebook.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.engineer_control_tab = ttk.Frame(self.engineer_notebook)
        self.engineer_monitor_tab = ttk.Frame(self.engineer_notebook)
        self.engineer_can_tab = ttk.Frame(self.engineer_notebook)
        self.engineer_log_tab = ttk.Frame(self.engineer_notebook)
        self._engineer_tab_titles = (
            (self.engineer_control_tab, "安全控制", "Control"),
            (self.engineer_monitor_tab, "状态与遥测", "Monitor"),
            (self.engineer_can_tab, "CAN 调试", "CAN"),
            (self.engineer_log_tab, "收发日志", "Logs"),
        )
        for tab, chinese, _english in self._engineer_tab_titles:
            self.engineer_notebook.add(tab, text=chinese)

        state_frame = ttk.LabelFrame(
            self.engineer_monitor_tab, text="控制状态", padding=10
        )
        state_frame.pack(fill=tk.X, padx=8, pady=8)
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

        motion_frame = ttk.LabelFrame(
            self.engineer_monitor_tab, text="运动与 PWM 遥测", padding=10
        )
        motion_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
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
        ttk.Label(motion_frame, textvariable=self.freq_var).grid(
            row=6, column=0, sticky="w", pady=(4, 0)
        )
        motion_frame.columnconfigure(0, weight=1)

        can_frame = ttk.LabelFrame(
            self.engineer_can_tab,
            text="CAN 调试 (RS04 从机 / 1Mbps 经典扩展帧)",
            padding=10,
        )
        can_frame.pack(fill=tk.X, padx=8, pady=8)
        can_tools = ttk.Frame(can_frame)
        can_tools.pack(fill=tk.X)
        ttk.Button(
            can_tools, text="查询状态",
            command=lambda: self.send_command("can status"),
        ).grid(row=0, column=0, padx=(0, 4))
        ttk.Button(
            can_tools, text="正常模式",
            command=lambda: self.send_command("can stby 0"),
        ).grid(row=0, column=1, padx=4)
        ttk.Button(
            can_tools, text="待机",
            command=lambda: self.send_command("can stby 1"),
        ).grid(row=0, column=2, padx=4)
        ttk.Button(
            can_tools, text="自回环",
            command=lambda: self.send_command("can loop"),
        ).grid(row=0, column=3, padx=4)
        ttk.Button(
            can_tools, text="编解码自检",
            command=lambda: self.send_command("can codec"),
        ).grid(row=0, column=4, padx=4)
        ttk.Button(
            can_tools, text="上报开",
            command=lambda: self.send_command("can report on"),
        ).grid(row=0, column=5, padx=4)
        ttk.Button(
            can_tools, text="上报关",
            command=lambda: self.send_command("can report off"),
        ).grid(row=0, column=6, padx=4)
        ttk.Button(
            can_tools, text="USB-CAN 参数验收",
            command=self.open_can_tool_window,
        ).grid(row=0, column=7, padx=(12, 4))
        can_tools.columnconfigure(8, weight=1)
        ttk.Label(can_frame, textvariable=self.can_status_var).pack(
            fill=tk.X, pady=(6, 0)
        )

        controls = ttk.Frame(self.engineer_control_tab, padding=8)
        controls.pack(fill=tk.BOTH, expand=True)

        lifecycle = ttk.LabelFrame(controls, text="启动与停止", padding=8)
        lifecycle.pack(fill=tk.X)
        self.start_button = ttk.Button(
            lifecycle, text="1. 启动并等待 RUN", command=self.start_motor
        )
        self.start_button.grid(row=0, column=0, padx=4, sticky="ew")
        ttk.Button(
            lifecycle, text="停止", command=self.stop_motor, style="Stop.TButton"
        ).grid(row=0, column=1, padx=4, sticky="ew")
        ttk.Label(lifecycle, text="测试时长(ms)").grid(
            row=0, column=2, padx=(20, 4)
        )
        self.pulse_spin = ttk.Spinbox(
            lifecycle,
            from_=500,
            to=5000,
            increment=500,
            textvariable=self.pulse_duration_var,
            width=7,
            justify="center",
        )
        self.pulse_spin.grid(row=0, column=3, padx=4)
        ttk.Button(
            lifecycle, text="单次 Keep", command=lambda: self.send_command("keep")
        ).grid(row=0, column=4, padx=4)
        ttk.Button(
            lifecycle,
            text="故障确认",
            command=lambda: self.send_command("faultack"),
        ).grid(row=0, column=5, padx=4)
        lifecycle.columnconfigure(0, weight=1)
        lifecycle.columnconfigure(1, weight=1)

        test_row = ttk.Frame(controls)
        test_row.pack(fill=tk.X, pady=(8, 0))
        torque = ttk.LabelFrame(test_row, text="转矩测试", padding=8)
        speed = ttk.LabelFrame(test_row, text="速度测试", padding=8)
        torque.grid(row=0, column=0, padx=(0, 4), sticky="nsew")
        speed.grid(row=0, column=1, padx=(4, 0), sticky="nsew")
        test_row.columnconfigure(0, weight=1)
        test_row.columnconfigure(1, weight=1)

        ttk.Label(torque, text="Iq (LSB，约10mA/LSB)").grid(
            row=0, column=0, padx=(0, 4), sticky="e"
        )
        self.iq_spin = ttk.Spinbox(
            torque,
            from_=1,
            to=MAX_IQ_LSB,
            textvariable=self.iq_var,
            width=6,
            justify="center",
        )
        self.iq_spin.grid(row=0, column=1, padx=4, sticky="w")
        self.positive_button = ttk.Button(
            torque, text="正向定时脉冲", command=lambda: self.start_timed_pulse(+1)
        )
        self.positive_button.grid(row=1, column=0, padx=4, pady=(8, 0), sticky="ew")
        self.negative_button = ttk.Button(
            torque, text="反向定时脉冲", command=lambda: self.start_timed_pulse(-1)
        )
        self.negative_button.grid(row=1, column=1, padx=4, pady=(8, 0), sticky="ew")
        ttk.Button(
            torque, text="Iq 归零", command=lambda: self.send_iq(0)
        ).grid(row=1, column=2, padx=4, pady=(8, 0), sticky="ew")
        for column in range(3):
            torque.columnconfigure(column, weight=1)

        ttk.Label(speed, text="速度 (电机轴 rpm)").grid(
            row=0, column=0, padx=(0, 4), sticky="e"
        )
        self.speed_spin = ttk.Spinbox(
            speed,
            from_=1,
            to=MAX_SPEED_RPM,
            textvariable=self.speed_var,
            width=6,
            justify="center",
        )
        self.speed_spin.grid(row=0, column=1, padx=4, sticky="w")
        self.speed_positive_button = ttk.Button(
            speed,
            text="正向低速测试",
            command=lambda: self.start_timed_speed(+1),
        )
        self.speed_positive_button.grid(
            row=1, column=0, padx=4, pady=(8, 0), sticky="ew"
        )
        self.speed_negative_button = ttk.Button(
            speed,
            text="反向低速测试",
            command=lambda: self.start_timed_speed(-1),
        )
        self.speed_negative_button.grid(
            row=1, column=1, padx=4, pady=(8, 0), sticky="ew"
        )
        ttk.Button(
            speed, text="速度归零", command=lambda: self.send_speed(0)
        ).grid(row=1, column=2, padx=4, pady=(8, 0), sticky="ew")
        for column in range(3):
            speed.columnconfigure(column, weight=1)

        continuous = ttk.LabelFrame(controls, text="持续运行", padding=8)
        continuous.pack(fill=tk.X, pady=(8, 0))
        self.keep_check = ttk.Checkbutton(
            continuous,
            text="持续运行自动 Keepalive（250ms）",
            variable=self.auto_keep_var,
            command=self._update_keepalive_label,
        )
        self.keep_check.grid(row=0, column=0, padx=4, sticky="w")
        ttk.Button(
            continuous,
            text="持续正向",
            command=lambda: self.start_continuous_speed(+1),
        ).grid(row=0, column=1, padx=4, sticky="ew")
        ttk.Button(
            continuous,
            text="持续反向",
            command=lambda: self.start_continuous_speed(-1),
        ).grid(row=0, column=2, padx=4, sticky="ew")
        continuous.columnconfigure(0, weight=1)
        continuous.columnconfigure(1, weight=1)
        continuous.columnconfigure(2, weight=1)

        lower_row = ttk.Frame(controls)
        lower_row.pack(fill=tk.X, pady=(8, 0))
        acceptance = ttk.LabelFrame(lower_row, text="Stage-I 验收", padding=8)
        diagnostics = ttk.LabelFrame(lower_row, text="高级诊断", padding=8)
        acceptance.grid(row=0, column=0, padx=(0, 4), sticky="nsew")
        diagnostics.grid(row=0, column=1, padx=(4, 0), sticky="nsew")
        lower_row.columnconfigure(0, weight=3)
        lower_row.columnconfigure(1, weight=2)

        ttk.Button(
            acceptance,
            text="Stage-I 双向探测",
            command=self.run_stage_i_probe,
        ).grid(row=0, column=0, padx=4, sticky="ew")
        ttk.Button(
            acceptance, text="Stage-I 完整验收", command=self.run_stage_i_acceptance
        ).grid(row=0, column=1, padx=4, sticky="ew")
        ttk.Button(
            acceptance, text="中止序列", command=self.abort_sequence
        ).grid(row=0, column=2, padx=4, sticky="ew")
        ttk.Label(acceptance, text="验收档位 rpm(逗号分隔)").grid(
            row=1, column=0, padx=4, pady=(8, 0), sticky="e"
        )
        self.acc_speeds_entry = ttk.Entry(
            acceptance, textvariable=self.acc_speeds_var, width=14
        )
        self.acc_speeds_entry.grid(
            row=1, column=1, columnspan=2, padx=4, pady=(8, 0), sticky="ew"
        )
        for column in range(3):
            acceptance.columnconfigure(column, weight=1)

        ttk.Label(diagnostics, text="偏移(‰ 5..150)").grid(
            row=0, column=0, padx=(0, 4), sticky="e"
        )
        self.hold_offset_spin = ttk.Spinbox(
            diagnostics,
            from_=5,
            to=150,
            textvariable=self.hold_offset_var,
            width=6,
            justify="center",
        )
        self.hold_offset_spin.grid(row=0, column=1, padx=4, sticky="w")
        ttk.Button(
            diagnostics, text="固定占空比保持(hold)", command=self.start_scope_hold
        ).grid(row=1, column=0, columnspan=2, padx=4, pady=(8, 0), sticky="ew")
        ttk.Button(
            diagnostics, text="波形窗口", command=self.open_wave_popup
        ).grid(row=2, column=0, padx=4, pady=(8, 0), sticky="ew")
        ttk.Button(diagnostics, text="清空日志", command=self.clear_log).grid(
            row=2, column=1, padx=4, pady=(8, 0), sticky="ew"
        )
        diagnostics.columnconfigure(0, weight=1)
        diagnostics.columnconfigure(1, weight=1)

        note = (
            "Iq 是转矩命令，不是转速命令。按 10→20→50→100 LSB 逐级测试；"
            "速度测试从 5 motor rpm 开始。定时脉冲/速度测试与 Stage-I 双向探测会自动每 500ms 发送 keep，"
            "到点自动 stop；“持续正向/持续反向”会一直运行直到按停止（长稳验收用），"
            "同样自动 keep；MCU 自身 1000ms 命令看门狗仍独立生效。"
        )
        ttk.Label(
            controls,
            text=note,
            foreground=WARNING_TEXT,
            wraplength=1020,
            justify=tk.LEFT,
        ).pack(
            fill=tk.X, pady=(8, 0)
        )

        log_frame = ttk.LabelFrame(
            self.engineer_log_tab, text="收发日志", padding=6
        )
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        log_tools = ttk.Frame(log_frame)
        log_tools.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(
            log_tools, text="独立日志窗口", command=self.open_log_popup
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Label(
            log_tools, text="波形流期间日志与波形并行显示；可导出为文本文件。"
        ).grid(row=0, column=1, sticky="w")
        log_tools.columnconfigure(2, weight=1)
        self.log = tk.Text(
            log_frame,
            wrap=tk.NONE,
            state=tk.DISABLED,
            font=("Consolas", 10),
            background=LOG_BACKGROUND,
            foreground=LOG_FOREGROUND,
            insertbackground=LOG_FOREGROUND,
        )
        y_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log.yview)
        x_scroll = ttk.Scrollbar(
            log_frame, orient=tk.HORIZONTAL, command=self.log.xview
        )
        self.log.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        self.log.grid(row=1, column=0, sticky="nsew")
        y_scroll.grid(row=1, column=1, sticky="ns")
        x_scroll.grid(row=2, column=0, sticky="ew")
        log_frame.rowconfigure(1, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self.log.tag_configure("tx", foreground=LOG_TX)
        self.log.tag_configure("rx", foreground=LOG_FOREGROUND)
        self.log.tag_configure("error", foreground=LOG_ERROR)
        self.log.tag_configure("event", foreground=LOG_EVENT)
        self._translate_engineer_widgets()
        self._update_control_state()

    def _translate_engineer_widgets(self) -> None:
        """Translate static engineering labels; raw protocol tokens stay unchanged."""
        self._translate_widget_tree(self.engineer_view)
        if hasattr(self, "_engineer_tab_titles"):
            english = self.language_var.get() == "en"
            for tab, chinese, translated in self._engineer_tab_titles:
                self.engineer_notebook.tab(
                    tab, text=translated if english else chinese
                )
        self.connect_button.configure(
            text=tr(
                self.language_var.get(),
                "disconnect" if self.connected else "connect",
            )
        )

    def _translate_widget_tree(self, root: tk.Misc) -> None:
        language = self.language_var.get()
        def visit(widget: tk.Misc) -> None:
            try:
                current = str(widget.cget("text"))
            except tk.TclError:
                current = ""
            if not hasattr(widget, "_easymotor_source_text"):
                widget._easymotor_source_text = current
            source = widget._easymotor_source_text
            if source:
                translated = (
                    ENGINEER_TEXT_EN.get(source, localize_legacy(source, language))
                    if language == "en"
                    else source
                )
                try:
                    widget.configure(text=translated)
                except tk.TclError:
                    pass
            for child in widget.winfo_children():
                visit(child)

        visit(root)

    def _ui(self, text: object) -> str:
        return localize_legacy(text, self.language_var.get())

    def _refresh_localized_vars(self) -> None:
        for value in vars(self).values():
            if isinstance(value, LocalizedStringVar):
                value.refresh_language()

    def show_demo_mode(self, force: bool = False) -> None:
        if not force and self._motor_activity_active():
            messagebox.showwarning(
                "请先停止电机",
                "电机或测试仍在运行。请先点击停止，确认设备回到待机后再切换模式。",
                parent=self,
            )
            return
        self.engineer_view.pack_forget()
        self.demo_view.pack(fill=tk.BOTH, expand=True)
        self.app_mode = "demo"
        self.geometry("900x680")
        self.minsize(820, 620)
        self._render_demo_view()

    def show_engineer_mode(self) -> None:
        if self._motor_activity_active():
            messagebox.showwarning(
                "请先停止电机",
                "进入工程师模式前必须先停止当前运行并确认设备回到待机。",
                parent=self,
            )
            return
        if self.connected and self.active_interface == "can":
            messagebox.showwarning(
                tr(self.language_var.get(), "stop_first"),
                tr(self.language_var.get(), "disconnect_for_advanced"),
                parent=self,
            )
            return
        if not messagebox.askokcancel(
            tr(self.language_var.get(), "advanced_confirm_title"),
            tr(self.language_var.get(), "advanced_confirm"),
            parent=self,
        ):
            return
        self._translate_engineer_widgets()
        self.demo_view.pack_forget()
        self.engineer_view.pack(fill=tk.BOTH, expand=True)
        self.app_mode = "engineer"
        self.engineer_notebook.select(self.engineer_control_tab)
        self.geometry("1120x760")
        self.minsize(920, 650)

    def _motor_activity_active(self) -> bool:
        return bool(
            self.start_waiting
            or self.pulse_active
            or self.continuous_active
            or self.nonzero_iq_active
            or self.stop_pending
            or self.sequence_active
            or self.acc_active
            or self.mci_state == 6
            or self.demo_service.phase != DemoPhase.IDLE
        )

    def start_demo_run(self, direction: int, speed_rpm: int, continuous: bool) -> None:
        other_active = bool(
            self.start_waiting
            or self.pulse_active
            or self.continuous_active
            or self.sequence_active
            or self.acc_active
            or self.stop_pending
        )
        try:
            action = self.demo_service.request_run(
                direction=direction,
                speed_rpm=speed_rpm,
                continuous=continuous,
                connected=self.connected,
                mci_state=self.mci_state,
                another_operation_active=other_active,
            )
        except DemoError as exc:
            messagebox.showwarning(
                tr(self.language_var.get(), "cannot_start"),
                tr(self.language_var.get(), exc.message_key),
                parent=self,
            )
            self._render_demo_view()
            return
        if action == DemoAction.START_MOTOR:
            if not self.start_motor():
                self.demo_service.cancel()
        else:
            self._execute_demo_action(action)
        self._render_demo_view()

    def _execute_demo_action(self, action: DemoAction) -> None:
        plan = self.demo_service.plan
        if plan is None:
            return
        if action == DemoAction.RUN_CONTINUOUS:
            self.start_continuous_speed(plan.direction, value=plan.speed_rpm)
            started = self.continuous_active
        else:
            self.start_timed_speed(
                plan.direction,
                value=plan.speed_rpm,
                duration_ms=plan.duration_ms,
            )
            started = self.pulse_active
        if not started:
            self.demo_service.cancel()
            self._append_log("error", "演示速度命令未启动，演示计划已取消。\n")
        self._render_demo_view()

    def _render_demo_view(self) -> None:
        if not hasattr(self, "demo_view"):
            return
        language = self.language_var.get()
        if not self.connected:
            text = tr(language, "connect_first")
        elif self.stop_pending:
            text = tr(language, "stopping")
        elif self.start_waiting or self.demo_service.phase == DemoPhase.PREPARING:
            text = tr(language, "preparing")
        elif self.demo_service.phase == DemoPhase.RUNNING and self.demo_service.plan:
            plan = self.demo_service.plan
            direction = tr(language, "forward_word" if plan.direction > 0 else "reverse_word")
            mode = tr(language, "continuous_word" if plan.continuous else "timed_word")
            text = tr(language, "running", direction=direction, rpm=plan.speed_rpm, mode=mode)
        elif self.active_interface == "can" and self.can_uid is None:
            text = tr(language, "reading")
        elif self.mci_state == 0:
            text = tr(language, "ready")
        elif self.mci_state == 6:
            text = tr(language, "enabled")
        elif self.mci_state is None:
            text = tr(language, "reading")
        else:
            text = tr(language, "unavailable")
        activity = self._motor_activity_active()
        run_enabled = bool(
            self.connected
            and self.mci_state in (0, 6)
            and (self.active_interface != "can" or self.can_uid is not None)
            and not activity
        )
        self.demo_view.render(
            connected=self.connected,
            status_text=text,
            run_enabled=run_enabled,
            stop_enabled=self.connected,
            settings_enabled=not activity,
        )

    def on_language_changed(self) -> None:
        self.title(window_title(tr(self.language_var.get(), "app_title")))
        self.copyright_var.set(tr(self.language_var.get(), "copyright"))
        self.update_button.configure(text=tr(self.language_var.get(), "check_updates"))
        self._refresh_localized_vars()
        self.connection_var.set(
            (
                f"{self.port_var.get()} | {self.active_interface.upper()}"
                if self.connected and self.active_interface
                else tr(self.language_var.get(), "not_connected")
            )
        )
        self.demo_view.rebuild()
        self.demo_view.set_ports(port.device for port in list_ports.comports())
        self._translate_engineer_widgets()
        if self.wave_popup is not None:
            try:
                if self.wave_popup.winfo_exists():
                    self.wave_popup.title(self._ui("电流波形 (独立显示)"))
                    self._translate_widget_tree(self.wave_popup)
                    self.wave_scale_combo.configure(
                        values=(self._ui("自动"), "±50", "±100", "±200", "±500")
                    )
                    if self.wave_toggle_button is not None:
                        self.wave_toggle_button.configure(
                            text=self._ui("停止波形" if self.streaming else "开始波形")
                        )
            except tk.TclError:
                pass
        if self.log_popup is not None:
            try:
                if self.log_popup.winfo_exists():
                    self.log_popup.title(self._ui("收发日志 (独立显示)"))
            except tk.TclError:
                pass
        if self.can_tool_window is not None:
            try:
                if self.can_tool_window.winfo_exists():
                    self.can_tool_window.refresh_language()
            except tk.TclError:
                pass
        if self.update_dialog is not None:
            try:
                if self.update_dialog.winfo_exists():
                    self.update_dialog.refresh_language()
            except tk.TclError:
                pass
        self._rerender_logs()
        self._render_demo_view()

    def on_interface_changed(self) -> None:
        if self.connected:
            return
        self.refresh_ports()

    def refresh_ports(self) -> None:
        ports = [port.device for port in list_ports.comports()]
        self.port_combo["values"] = ports
        self.demo_view.set_ports(ports)
        if self.port_var.get() not in ports:
            self.port_var.set(ports[0] if ports else "")

    def toggle_connection(self) -> None:
        if self.connected:
            self.disconnect()
        else:
            self.connect()

    def _connection_interface(self) -> str:
        """Keep the demo selection independent from the RS485-only engineer page."""
        if self.app_mode == "engineer":
            return "rs485"
        return self.interface_var.get()

    def connect(self) -> None:
        port = self.port_var.get()
        if not port:
            messagebox.showwarning(
                tr(self.language_var.get(), "no_port_title"),
                tr(self.language_var.get(), "no_port"),
            )
            return
        if self._connection_interface() == "can":
            self._connect_can(port)
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
            messagebox.showerror(tr(self.language_var.get(), "connection_failed"), str(exc))
            return

        self.serial_port = connection
        self.connected = True
        self.active_interface = "rs485"
        self.keep_sent_count = 0
        self.keep_busy_retry_count = 0
        self.keep_forced_count = 0
        self._freq_pwm_hz = None
        self._freq_foc_hz = None
        self._freq_enc_rt_hz = None
        self._rt_last_t_ms = None
        self._rt_last_adc = None
        self.freq_var.set("PWM/FOC: 未知")
        self.reader_stop.clear()
        self.reader_thread = threading.Thread(
            target=self._reader_loop, name="robot-joint-uart", daemon=True
        )
        self.reader_thread.start()
        self.connection_var.set(f"{port} | RS485 @ {BAUD_RATE:,}")
        self.connect_button.configure(text=tr(self.language_var.get(), "disconnect"))
        self._append_log("event", f"已连接 {port} @ {BAUD_RATE}\n")
        self._update_control_state()
        self.after(100, lambda: self.send_command("status"))

    def _connect_can(self, port: str) -> None:
        transport = UsbCanMotorTransport(self.rx_queue)
        try:
            transport.connect(port)
            transport.set_active_report(True)
            transport.enumerate()
        except (serial.SerialException, OSError, RuntimeError) as exc:
            transport.close()
            messagebox.showerror(tr(self.language_var.get(), "connection_failed"), str(exc))
            return
        self.can_transport = transport
        self.connected = True
        self.active_interface = "can"
        self.mci_state = None
        self.can_uid = None
        self.can_last_feedback_time = 0.0
        self.can_command_rpm = 0
        self.can_enumeration_deadline = time.monotonic() + CAN_ENUMERATION_TIMEOUT_MS / 1000.0
        self.connection_var.set(f"{port} | CAN 1 Mbps / USB {USB_CAN_BAUD:,}")
        self._append_log("event", f"EasyMotor CAN connected {port} @ {USB_CAN_BAUD}\n")
        self._update_control_state()
        self.after(CAN_ENUMERATION_TIMEOUT_MS, self._check_can_enumeration)

    def _check_can_enumeration(self) -> None:
        if (
            self.connected
            and self.active_interface == "can"
            and self.can_uid is None
            and time.monotonic() >= self.can_enumeration_deadline
        ):
            messagebox.showwarning(
                tr(self.language_var.get(), "connection_failed"),
                tr(self.language_var.get(), "can_standby_hint"),
                parent=self,
            )

    def disconnect(self) -> None:
        activity_before_disconnect = self._motor_activity_active()
        if (
            activity_before_disconnect
            and self.active_interface == "rs485"
            and self.serial_port is not None
        ):
            try:
                self.send_command("stop", quiet=True)
                time.sleep(0.05)
            except Exception:
                pass
        self.reader_stop.set()
        self.demo_service.cancel()
        self.demo_view.reset_continuous()
        connection = self.serial_port
        self.serial_port = None
        can_transport = self.can_transport
        self.can_transport = None
        if can_transport is not None:
            try:
                for _attempt in range(3 if activity_before_disconnect else 1):
                    can_transport.stop()
                can_transport.set_active_report(False)
            except (serial.SerialException, OSError, RuntimeError):
                pass
            can_transport.close()
        self.connected = False
        self.active_interface = None
        self.start_waiting = False
        self.nonzero_iq_active = False
        self.pulse_active = False
        self.continuous_active = False
        self.stop_pending = False
        self.sequence_active = False
        self._rt_health_fragment = ""
        self._enc_health_fragment = ""
        self._freq_pwm_hz = None
        self._freq_foc_hz = None
        self._freq_enc_rt_hz = None
        self._rt_last_t_ms = None
        self._rt_last_adc = None
        self.freq_var.set("PWM/FOC: 未知")
        self.can_ready = False
        self.can_node_id = None
        self.can_master_id = None
        self.can_normal = False
        self.can_rx_frames = 0
        self.can_tx_requests = 0
        self.can_tx_fail = 0
        self.can_tx_err = 0
        self.can_rx_err = 0
        self.can_bus_off = 0
        self.can_accepted = 0
        self.can_active_report = False
        self.can_status_var.set("CAN: 未初始化")
        self.streaming = False
        self._wave_off_deadline = None
        self._close_wave_csv()
        for buf in self._wave_buffers.values():
            buf.clear()
        self._wave_stats_entries.clear()
        self.wave_status_var.set("波形: 停止")
        self.auto_keep_var.set(False)
        if connection is not None:
            try:
                connection.close()
            except serial.SerialException:
                pass
        self.connection_var.set(tr(self.language_var.get(), "not_connected"))
        self.connect_button.configure(text=tr(self.language_var.get(), "connect"))
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
            if self.streaming:
                self._extract_wave_frames(pending)
            else:
                while b"\n" in pending:
                    raw_line, _, pending = pending.partition(b"\n")
                    line = raw_line.rstrip(b"\r").decode("ascii", errors="replace")
                    self.rx_queue.put(("line", line))
        if pending and not self.streaming:
            self.rx_queue.put(("line", pending.decode("ascii", errors="replace")))

    def _emit_text_lines(self, raw: bytes) -> None:
        """Emit complete printable ASCII lines (used while streaming)."""
        start = 0
        while True:
            newline = raw.find(b"\n", start)
            if newline < 0:
                return
            line = raw[start:newline].rstrip(b"\r")
            start = newline + 1
            if line and all(32 <= byte < 127 for byte in line):
                self.rx_queue.put(("line", line.decode("ascii", errors="replace")))

    def _extract_wave_frames(self, pending: bytearray) -> None:
        """Parse raw (0xA5) and envelope (0xA6) frames; salvage text lines."""
        while True:
            raw_start = pending.find(bytes([WAVE_SOF]))
            stats_start = pending.find(bytes([WAVE_STATS_SOF]))
            candidates = [pos for pos in (raw_start, stats_start) if pos >= 0]
            if not candidates:
                last_newline = pending.rfind(b"\n")
                if last_newline >= 0:
                    self._emit_text_lines(bytes(pending[: last_newline + 1]))
                    del pending[: last_newline + 1]
                return
            start = min(candidates)
            if start > 0:
                self._emit_text_lines(bytes(pending[:start]))
                del pending[:start]
            is_stats = pending[0] == WAVE_STATS_SOF
            length = WAVE_STATS_FRAME_LEN if is_stats else WAVE_FRAME_LEN
            if len(pending) < length:
                return
            frame = bytes(pending[:length])
            del pending[:length]
            checksum = 0
            for byte in frame[1 : length - 1]:
                checksum ^= byte
            if checksum != frame[length - 1]:
                continue
            seq = frame[1] | (frame[2] << 8)
            if is_stats:
                values = tuple(
                    int.from_bytes(
                        frame[3 + 2 * index : 5 + 2 * index],
                        "little",
                        signed=True,
                    )
                    for index in range(6)
                )
                self.rx_queue.put(("wave_stats", (seq, values)))
            else:
                iu = int.from_bytes(frame[3:5], "little", signed=True)
                iv = int.from_bytes(frame[5:7], "little", signed=True)
                iw = int.from_bytes(frame[7:9], "little", signed=True)
                self.rx_queue.put(("wave", (seq, iu, iv, iw)))

    def _process_rx_queue(self) -> None:
        try:
            while True:
                kind, payload = self.rx_queue.get_nowait()
                if kind == "line":
                    line = str(payload)
                    self._append_log("rx", f"RX  {line}\n")
                    try:
                        self._parse_status_line(line)
                    except Exception as exc:  # Keep the UI alive on parse bugs.
                        self._append_log("error", f"解析异常: {exc!r}\n")
                elif kind == "wave":
                    self._on_wave_frame(payload)
                elif kind == "wave_stats":
                    self._on_wave_stats_frame(payload)
                elif kind == "can_tx":
                    self._append_log("tx", f"CAN TX {format_frame(payload)}\n")
                elif kind == "can_frame":
                    self._handle_can_frame(payload)
                elif kind == "can_error":
                    self._append_log("error", f"USB-CAN error: {payload}\n")
                    if self.connected and self.active_interface == "can":
                        self.disconnect()
                else:
                    self._append_log("error", f"串口错误: {payload}\n")
                    if self.connected:
                        self.disconnect()
        except queue.Empty:
            pass
        try:
            self._poll_start_sequence()
        except Exception as exc:  # Keep the keepalive/sequence loop alive.
            self._append_log("error", f"轮询异常: {exc!r}\n")
        self.after(20, self._process_rx_queue)

    def _handle_can_frame(self, frame: CanFrame) -> None:
        device = parse_device_id_response(frame)
        if device is not None:
            self._append_log("rx", f"CAN RX {format_frame(frame)}\n")
            node_id, uid = device
            self.can_uid = uid
            self._append_log("event", f"CAN node {node_id} UID=0x{uid:016X}\n")
            return
        feedback = parse_feedback(frame)
        if feedback is not None:
            now = time.monotonic()
            self.can_last_feedback_time = now
            if now - self.can_last_feedback_log_time >= 0.25:
                self.can_last_feedback_log_time = now
                self._append_log("rx", f"CAN RX {format_frame(frame)}\n")
            if feedback.mode == MODE_RESET:
                self.mci_state = 0
            elif feedback.mode == MODE_CALIBRATING:
                self.mci_state = 2
            elif feedback.mode == MODE_MOTOR:
                self.mci_state = 6
            else:
                self.mci_state = None
            self.state_var.set(
                f"CAN mode={feedback.mode} faults=0x{feedback.faults:02X} "
                f"pos={feedback.position_rad:.4f} rad vel={feedback.velocity_rad_s:.4f} rad/s"
            )
            if feedback.faults and self._motor_activity_active() and not self.stop_pending:
                self._append_log("error", "CAN feedback reported a fault; requesting stop.\n")
                self.stop_motor()
            self._update_control_state()
            return
        self._append_log("rx", f"CAN RX {format_frame(frame)}\n")
        fault = parse_fault_report(frame)
        if fault is not None:
            self._append_log(
                "error" if fault.fault else "event",
                f"CAN fault=0x{fault.fault:08X} warning=0x{fault.warning:08X}\n",
            )
            if fault.fault and self._motor_activity_active() and not self.stop_pending:
                self.stop_motor()

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
                and (self.pulse_active or self.continuous_active)
            ):
                self.pulse_active = False
                self.continuous_active = False
                self.nonzero_iq_active = False
                self.demo_service.cancel()
                self.auto_keep_var.set(False)
                self._update_keepalive_label()
                self._append_log(
                    "error",
                    "MCU 已主动退出 RUN，测试和 Keepalive 已停止。\n",
                )
                if self.acc_active and self.acc_phase == "speed":
                    self.acc_data[-1]["watchdog"] = True
            self._update_control_state()

        if "CMD start rejected" in line:
            self.start_waiting = False
            self.demo_service.cancel()
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

        pwm_test = PWM_TEST_RE.search(line)
        if pwm_test:
            self._freq_pwm_hz = int(pwm_test.group(3))
            self._update_freq_label()

        encoder_rt = ENCODER_RT_RE.search(line)
        if encoder_rt:
            self._freq_enc_rt_hz = int(encoder_rt.group(1))
            self._freq_foc_hz = int(encoder_rt.group(2))
            self._update_freq_label()

        if CAN_READY_RE.search(line):
            self.can_ready = True
            self._update_can_label()
        node_id_match = CAN_NODE_ID_RE.search(line)
        if node_id_match:
            self.can_node_id = int(node_id_match.group(1))
            self._update_can_label()
        master_id_match = CAN_MASTER_ID_RE.search(line)
        if master_id_match:
            self.can_master_id = int(master_id_match.group(1))
            self._update_can_label()
        if CAN_STBY_NORMAL_RE.search(line):
            self.can_normal = True
            self._update_can_label()
        if CAN_STBY_STANDBY_RE.search(line):
            self.can_normal = False
            self._update_can_label()
        id_field = CAN_STATUS_ID_RE.match(line)
        if id_field:
            if id_field.group(1) == "node_id":
                self.can_node_id = int(id_field.group(2))
            else:
                self.can_master_id = int(id_field.group(2))
            self._update_can_label()
        field = CAN_STATUS_FIELD_RE.match(line)
        if field:
            key = field.group(1)
            value = int(field.group(2))
            if key == "normal":
                self.can_normal = value != 0
            elif key == "rx_frames":
                self.can_rx_frames = value
            elif key == "tx_requests":
                self.can_tx_requests = value
            elif key == "tx_fail":
                self.can_tx_fail = value
            elif key == "tx_err_cnt":
                self.can_tx_err = value
            elif key == "rx_err_cnt":
                self.can_rx_err = value
            elif key == "bus_off":
                self.can_bus_off = value
            elif key == "accepted":
                self.can_accepted = value
            elif key == "active_report":
                self.can_active_report = value != 0
            self._update_can_label()
        if CAN_LOOP_PASS_RE.search(line):
            self._append_log("event", "CAN 自回环通过\n")
        elif CAN_LOOP_FAIL_RE.search(line):
            self._append_log("error", "CAN 自回环失败\n")
        if CAN_CODEC_PASS_RE.search(line):
            self._append_log("event", "CAN 编解码自检通过\n")
        elif CAN_CODEC_FAIL_RE.search(line):
            self._append_log("error", "CAN 编解码自检失败\n")

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
            adc = int(runtime.group(10))
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
            if (
                self._rt_last_t_ms is not None
                and self._rt_last_adc is not None
            ):
                dt_ms = uptime_ms - self._rt_last_t_ms
                adc_delta = adc - self._rt_last_adc
                if 500 <= dt_ms <= 5000 and adc_delta > 0:
                    derived_khz = round(adc_delta * 1000.0 / dt_ms / 1000.0)
                    if derived_khz > 0 and self._freq_pwm_hz is None:
                        self._freq_pwm_hz = derived_khz * 1000
                        self._update_freq_label()
            self._rt_last_t_ms = uptime_ms
            self._rt_last_adc = adc

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

    def _update_freq_label(self) -> None:
        """Show PWM/FOC/rotor-frame rates once known (boot banner or RT-derived)."""
        parts: list[str] = []
        if self._freq_pwm_hz is not None:
            parts.append(f"PWM {self._freq_pwm_hz / 1000.0:g} kHz")
        if self._freq_foc_hz is not None:
            parts.append(f"FOC {self._freq_foc_hz} kHz")
        if self._freq_enc_rt_hz is not None:
            parts.append(f"ENC_RT {self._freq_enc_rt_hz} kHz")
        self.freq_var.set(" | ".join(parts) if parts else "PWM/FOC: 未知")

    def _update_can_label(self) -> None:
        """Compose the compact CAN node status label."""
        if not self.can_ready:
            self.can_status_var.set("CAN: 未初始化")
            return
        mode = "正常模式" if self.can_normal else "待机(静默)"
        node = f"0x{self.can_node_id:X}" if self.can_node_id is not None else "?"
        master = f"0x{self.can_master_id:X}" if self.can_master_id is not None else "?"
        report = "开" if self.can_active_report else "关"
        self.can_status_var.set(
            f"CAN 就绪 | 节点={node} 主机={master} | {mode} "
            f"| RX {self.can_rx_frames} TX {self.can_tx_requests} "
            f"(TX失败 {self.can_tx_fail}) "
            f"| 错误 T/R={self.can_tx_err}/{self.can_rx_err} "
            f"| BusOff {self.can_bus_off} | 上报 {report}"
        )

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

    def send_command(self, command: str, quiet: bool = False,
                     strict_quiet: bool = False) -> bool:
        if self.active_interface == "can":
            if not quiet:
                self._append_log(
                    "error", f"RS485 command '{command}' is unavailable on the CAN demo connection.\n"
                )
            return False
        if not self.connected or self.serial_port is None:
            if not quiet:
                messagebox.showwarning("未连接", "请先连接串口。")
            return False
        payload = (command.strip() + "\r\n").encode("ascii")
        wait_deadline = time.monotonic() + RS485_TX_WAIT_MAX_MS / 1000.0
        bus_quiet = False
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
                bus_quiet = True
                break
            time.sleep(0.001)
        if strict_quiet and not bus_quiet:
            # Never transmit into a busy half-duplex bus for keepalive; the
            # caller retries shortly so the 1000 ms firmware watchdog stays
            # comfortably refreshed.
            return False
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

    def start_scope_hold(self) -> None:
        """Send the 100 kHz fixed-duty scope hold command."""
        offset = max(5, min(150, self.hold_offset_var.get()))
        self.hold_offset_var.set(offset)
        if self.send_command(f"hold {offset}"):
            self._append_log(
                "event",
                f"已发送固定占空比保持 hold {offset}‰；PWM 持续输出，按停止退出。\n",
            )

    def toggle_wave_stream(self) -> None:
        """Start/stop the firmware ADC phase-current waveform stream."""
        if not self.connected:
            messagebox.showwarning("未连接", "请先连接串口。")
            return
        if not self.streaming:
            dec = max(1, min(100, self.wave_dec_var.get()))
            self.wave_dec_var.set(dec)
            use_stats = self.wave_stats_var.get()
            command = (
                f"wave stats on 100" if use_stats else f"wave on {dec}"
            )
            if self.send_command(command):
                self.streaming = True
                self._wave_last_seq = None
                self._wave_frame_count = 0
                self._wave_lost_count = 0
                self._wave_glitch_count = 0
                self._wave_prev_raw = None
                self._wave_off_deadline = None
                for buf in self._wave_buffers.values():
                    buf.clear()
                self._wave_stats_entries.clear()
                self._open_wave_csv()
                if use_stats:
                    self.wave_status_var.set("波形: 启动中 (包络, 500 Hz)")
                else:
                    self.wave_status_var.set(
                        f"波形: 启动中 (dec={dec}, 标称 {50000 // dec} Hz)"
                    )
                self.wave_toggle_button.configure(text=self._ui("停止波形"))
        else:
            if self.send_command("wave off"):
                self._wave_off_deadline = time.monotonic() + 2.0
                self.wave_status_var.set("波形: 停止中...")

    def _on_wave_frame(self, frame: tuple[int, int, int, int]) -> None:
        seq, iu, iv, iw = frame
        if seq == WAVE_END_SEQ:
            self.streaming = False
            self._wave_off_deadline = None
            self.wave_status_var.set("波形: 已停止")
            if self.wave_toggle_button is not None:
                try:
                    self.wave_toggle_button.configure(text=self._ui("开始波形"))
                except tk.TclError:
                    pass
            self._close_wave_csv()
            return
        if self._wave_last_seq is not None:
            expected = (self._wave_last_seq + 1) & 0xFFFF
            if seq != expected:
                self._wave_lost_count += 1
        self._wave_last_seq = seq
        self._wave_frame_count += 1
        if self._wave_prev_raw is not None:
            prev_u, prev_v, prev_w = self._wave_prev_raw
            if (
                abs(iu - prev_u) > WAVE_GLITCH_LSB
                or abs(iv - prev_v) > WAVE_GLITCH_LSB
                or abs(iw - prev_w) > WAVE_GLITCH_LSB
            ):
                self._wave_glitch_count += 1
        self._wave_prev_raw = (iu, iv, iw)
        self._wave_buffers["u"].append((seq, iu))
        self._wave_buffers["v"].append((seq, iv))
        self._wave_buffers["w"].append((seq, iw))
        if self._wave_csv_file is not None:
            self._wave_csv_rows.append(f"{seq},{iu},{iv},{iw}\n")
            if len(self._wave_csv_rows) >= 500:
                self._flush_wave_csv()

    def _on_wave_stats_frame(
        self, frame: tuple[int, tuple[int, int, int, int, int, int]]
    ) -> None:
        seq, values = frame
        if self._wave_last_seq is not None:
            expected = (self._wave_last_seq + 1) & 0xFFFF
            if seq != expected:
                self._wave_lost_count += 1
        self._wave_last_seq = seq
        self._wave_frame_count += 1
        self._wave_stats_entries.append((seq, values))

    def _open_wave_csv(self) -> None:
        if not self.wave_save_var.get():
            return
        try:
            folder = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "wave_data"
            )
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(
                folder, "wave_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv"
            )
            self._wave_csv_file = open(path, "w", encoding="ascii", newline="")
            self._wave_csv_file.write("seq,iu,iv,iw\n")
            self._wave_csv_path = path
            self._wave_csv_rows = []
            self._append_log("event", f"波形自动保存: {path}\n")
        except OSError as exc:
            self._wave_csv_file = None
            self._wave_csv_path = None
            self._append_log("error", f"波形文件打开失败: {exc}\n")

    def _flush_wave_csv(self) -> None:
        if self._wave_csv_file is None or not self._wave_csv_rows:
            return
        try:
            self._wave_csv_file.write("".join(self._wave_csv_rows))
            self._wave_csv_rows.clear()
        except OSError as exc:
            self._append_log("error", f"波形文件写入失败: {exc}\n")

    def _close_wave_csv(self) -> None:
        if self._wave_csv_file is not None:
            try:
                self._flush_wave_csv()
                self._wave_csv_file.close()
            except OSError as exc:
                self._append_log("error", f"波形文件关闭失败: {exc}\n")
            finally:
                self._wave_csv_file = None
        if self._wave_csv_path is not None:
            self._append_log("event", f"波形已保存: {self._wave_csv_path}\n")
            self._wave_csv_path = None

    def open_wave_popup(self) -> None:
        """Open the waveform in a dedicated, resizable window."""
        if self.wave_popup is not None and self.wave_popup.winfo_exists():
            self.wave_popup.lift()
            self.wave_popup.focus_force()
            return
        win = tk.Toplevel(self)
        apply_window_icon(win)
        apply_window_surface(win)
        win.title(self._ui("电流波形 (独立显示)"))
        win.geometry("1000x560")
        win.minsize(720, 320)
        tools = ttk.Frame(win, padding=6)
        tools.pack(fill=tk.X)
        self.wave_toggle_button = ttk.Button(
            tools, text="开始波形", command=self.toggle_wave_stream
        )
        self.wave_toggle_button.grid(row=0, column=0, padx=(0, 8))
        ttk.Label(tools, text="分频(1..100)").grid(
            row=0, column=1, padx=(0, 4)
        )
        self.wave_dec_spin = ttk.Spinbox(
            tools,
            from_=1,
            to=100,
            textvariable=self.wave_dec_var,
            width=5,
            justify="center",
        )
        self.wave_dec_spin.grid(row=0, column=2, padx=(0, 12))
        ttk.Label(tools, text="Y量程").grid(
            row=0, column=3, padx=(4, 4)
        )
        self.wave_scale_combo = ttk.Combobox(
            tools,
            textvariable=self.wave_scale_var,
            values=(self._ui("自动"), "±50", "±100", "±200", "±500"),
            width=7,
            state="readonly",
        )
        self.wave_scale_combo.grid(row=0, column=4, padx=(0, 8))
        ttk.Checkbutton(tools, text="U", variable=self.wave_u_var).grid(
            row=0, column=5, padx=2
        )
        ttk.Checkbutton(tools, text="V", variable=self.wave_v_var).grid(
            row=0, column=6, padx=2
        )
        ttk.Checkbutton(tools, text="W", variable=self.wave_w_var).grid(
            row=0, column=7, padx=2
        )
        ttk.Label(tools, textvariable=self.wave_status_var).grid(
            row=0, column=8, padx=(12, 0), sticky="w"
        )
        ttk.Checkbutton(
            tools, text="自动保存CSV", variable=self.wave_save_var
        ).grid(row=1, column=4, padx=(8, 4), pady=(4, 0))
        ttk.Checkbutton(
            tools, text="包络模式(减带宽)", variable=self.wave_stats_var
        ).grid(row=1, column=2, padx=(8, 4), pady=(4, 0))
        ttk.Button(
            tools, text="保存波形", command=self.save_wave_snapshot
        ).grid(row=1, column=0, padx=(0, 8), pady=(4, 0))
        ttk.Button(
            tools, text="导出日志", command=self.export_log
        ).grid(row=1, column=1, padx=(0, 8), pady=(4, 0))
        tools.columnconfigure(9, weight=1)
        self.wave_popup = win
        self.wave_popup_canvas = tk.Canvas(
            win, background=WAVE_BACKGROUND, highlightthickness=0
        )
        self.wave_popup_canvas.pack(
            fill=tk.BOTH, expand=True, padx=6, pady=(0, 6)
        )
        self._translate_widget_tree(win)
        win.protocol("WM_DELETE_WINDOW", self._close_wave_popup)

    def _close_wave_popup(self) -> None:
        if self.wave_popup is not None:
            try:
                self.wave_popup.destroy()
            except tk.TclError:
                pass
            self.wave_popup = None
            self.wave_popup_canvas = None
            self.wave_toggle_button = None

    def save_wave_snapshot(self) -> None:
        """Save the currently buffered waveform window to a CSV file."""
        try:
            folder = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "wave_data"
            )
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(
                folder,
                "wave_snapshot_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".csv",
            )
            buffers = [list(self._wave_buffers[ch]) for ch in ("u", "v", "w")]
            count = min((len(buf) for buf in buffers), default=0)
            with open(path, "w", encoding="ascii", newline="") as handle:
                handle.write("seq,iu,iv,iw\n")
                for index in range(count):
                    seq_u, iu = buffers[0][index]
                    _, iv = buffers[1][index]
                    _, iw = buffers[2][index]
                    handle.write(f"{seq_u},{iu},{iv},{iw}\n")
            self._append_log(
                "event", f"波形快照已保存: {path} ({count} 帧)\n"
            )
        except OSError as exc:
            self._append_log("error", f"波形快照保存失败: {exc}\n")

    def export_log(self) -> None:
        """Save the current log panel content to a text file."""
        try:
            folder = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "logs"
            )
            os.makedirs(folder, exist_ok=True)
            path = os.path.join(
                folder, "log_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt"
            )
            content = self.log.get("1.0", tk.END)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(content)
            self._append_log("event", f"日志已导出: {path}\n")
        except (OSError, tk.TclError) as exc:
            self._append_log("error", f"日志导出失败: {exc}\n")

    def _wave_redraw(self) -> None:
        canvases: list[tk.Canvas] = []
        try:
            popup = self.wave_popup
            if (
                popup is not None
                and popup.winfo_exists()
                and self.wave_popup_canvas is not None
            ):
                canvases.append(self.wave_popup_canvas)
        except tk.TclError:
            canvases = []
        try:
            if (
                self.streaming
                and self._wave_off_deadline is not None
                and time.monotonic() >= self._wave_off_deadline
            ):
                self.streaming = False
                self._wave_off_deadline = None
                self.wave_status_var.set("波形: 已停止(未收到结束帧)")
                if self.wave_toggle_button is not None:
                    try:
                        self.wave_toggle_button.configure(text=self._ui("开始波形"))
                    except tk.TclError:
                        pass
                self._close_wave_csv()
            if self._wave_csv_file is not None and self._wave_csv_rows:
                self._flush_wave_csv()
            colors = {"u": WAVE_U, "v": WAVE_V, "w": WAVE_W}
            show = {
                "u": self.wave_u_var.get(),
                "v": self.wave_v_var.get(),
                "w": self.wave_w_var.get(),
            }
            series: dict[str, list[tuple[int, int]]] = {}
            values: list[int] = []
            for channel in ("u", "v", "w"):
                buf = self._wave_buffers[channel]
                if show[channel] and buf:
                    series[channel] = list(buf)
                    values.extend(value for _, value in buf)
            stats_entries = list(self._wave_stats_entries)
            for _, stats_values in stats_entries:
                values.extend(stats_values)
            scale_text = self.wave_scale_var.get()
            if scale_text.startswith("±"):
                fixed_scale = float(scale_text[1:])
                y_min, y_max = -fixed_scale, fixed_scale
            elif values:
                low = min(values)
                high = max(values)
                y_span = max(high - low, 80)
                margin = y_span * 0.15
                y_min = low - margin
                y_max = high + margin
            else:
                y_min, y_max = -100.0, 100.0
            for canvas in canvases:
                try:
                    canvas.delete("wave")
                except tk.TclError:
                    continue
                width = max(canvas.winfo_width(), 200)
                height = max(canvas.winfo_height(), 160)
                zero_y = height - (0 - y_min) / (y_max - y_min) * height
                canvas.create_line(
                    0, zero_y, width, zero_y, fill=WAVE_GRID, tags="wave"
                )
                canvas.create_text(
                    6, 8, anchor="nw", text=f"{y_max:.0f}",
                    fill=WAVE_LABEL, tags="wave",
                )
                canvas.create_text(
                    6, height - 8, anchor="sw", text=f"{y_min:.0f}",
                    fill=WAVE_LABEL, tags="wave",
                )
                for channel, points in series.items():
                    count = len(points)
                    if count < 2:
                        continue
                    coords: list[float] = []
                    prev_seq = points[0][0]
                    for index, (seq, value) in enumerate(points):
                        x = index / (count - 1) * width
                        y = (
                            height
                            - (value - y_min) / (y_max - y_min) * height
                        )
                        if index > 0 and ((seq - prev_seq) & 0xFFFF) != 1:
                            if len(coords) >= 4:
                                canvas.create_line(
                                    coords, fill=colors[channel], width=1,
                                    tags="wave",
                                )
                            coords = []
                        coords.extend((x, y))
                        prev_seq = seq
                    if len(coords) >= 4:
                        canvas.create_line(
                            coords, fill=colors[channel], width=1, tags="wave"
                        )
                stats_count = len(stats_entries)
                if stats_count >= 2:
                    for index, (_, stats_values) in enumerate(stats_entries):
                        x = index / (stats_count - 1) * width
                        for ch_index, channel in enumerate(("u", "v", "w")):
                            if not show[channel]:
                                continue
                            min_value = stats_values[ch_index * 2]
                            max_value = stats_values[ch_index * 2 + 1]
                            y1 = (
                                height
                                - (min_value - y_min) / (y_max - y_min) * height
                            )
                            y2 = (
                                height
                                - (max_value - y_min) / (y_max - y_min) * height
                            )
                            canvas.create_line(
                                x, y1, x, y2, fill=colors[channel], width=2,
                                tags="wave",
                            )
            if self.streaming:
                self.wave_status_var.set(
                    "波形: 运行 "
                    f"| 帧 {self._wave_frame_count} "
                    f"| 丢帧 {self._wave_lost_count} "
                    f"| 毛刺(>{WAVE_GLITCH_LSB}) {self._wave_glitch_count}"
                )
        except Exception as exc:  # Keep the redraw loop alive on UI hiccups.
            self._append_log("error", f"波形绘制异常: {exc!r}\n")
        self.after(50, self._wave_redraw)

    def start_motor(self) -> bool:
        if self.sequence_active:
            messagebox.showwarning("序列进行中", "请先中止 Stage-I 探测序列。")
            return False
        if self.active_interface == "can":
            if self.can_transport is None:
                return False
            try:
                self.can_transport.enable()
            except (serial.SerialException, OSError, RuntimeError) as exc:
                self._append_log("error", f"CAN enable failed: {exc}\n")
                return False
            self._append_log("event", "CAN Type 3 enable sent; waiting for Type 2 MOTOR feedback.\n")
        elif not self.send_command("start"):
            return False
        self.start_waiting = True
        self.start_deadline = time.monotonic() + START_TIMEOUT_MS / 1000.0
        self.state_var.set("MCI: 正在启动/对齐（被动监听）…")
        self._append_log(
            "event",
            "已发送启动命令；被动监听 RT 状态，不进行周期 status 轮询。\n",
        )
        self._update_control_state()
        return True

    def _poll_start_sequence(self) -> None:
        if not self.start_waiting:
            return
        if (
            self.active_interface == "can"
            and self.can_last_feedback_time > 0.0
            and time.monotonic() - self.can_last_feedback_time > 0.75
        ):
            self.start_waiting = False
            self.demo_service.cancel()
            self._append_log("error", "CAN feedback lost during enable/alignment; requesting stop.\n")
            self.stop_motor()
            return
        if self.mci_state == 6:
            self.start_waiting = False
            self._append_log("event", "MCU 已进入 RUN，可以施加 Iq/速度。\n")
            demo_action = self.demo_service.motor_ready()
            if demo_action is not None:
                self._execute_demo_action(demo_action)
            self._update_control_state()
            return
        if time.monotonic() >= self.start_deadline:
            self.start_waiting = False
            self.demo_service.cancel()
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
        sent = False
        if self.active_interface == "can":
            if self.can_transport is None:
                return False
            try:
                self.can_transport.command_velocity(value)
                self.can_command_rpm = value
                sent = True
            except (ValueError, serial.SerialException, OSError, RuntimeError) as exc:
                self._append_log("error", f"CAN velocity command failed: {exc}\n")
        else:
            sent = self.send_command(f"speed {value}")
        if sent:
            self.nonzero_iq_active = value != 0
            if value == 0:
                self.auto_keep_var.set(False)
                self.continuous_active = False
            self._update_keepalive_label()
            return True
        return False

    def start_continuous_speed(
        self,
        direction: int,
        value: int | None = None,
    ) -> None:
        """Run speed continuously until the user presses stop. Keepalive is
        refreshed automatically every 300 ms, so the firmware command watchdog
        never fires; suitable for the >=10 min long-run acceptance."""
        if value is None:
            value = self._validated_speed()
        if value is None:
            return
        if self.mci_state != 6:
            messagebox.showwarning("尚未 RUN", "请先启动，并等待 MCI 进入 RUN。")
            return
        if self.pulse_active or self.continuous_active:
            messagebox.showwarning("测试进行中", "请先停止当前运行。")
            return
        command_rpm = direction * value
        if not self.send_speed(command_rpm):
            return
        self.continuous_active = True
        self.keep_status_var.set(
            f"持续速度运行: {command_rpm} motor rpm，自动 keep，按停止结束"
        )
        self._append_log(
            "event",
            f"持续速度运行开始: {command_rpm} motor rpm，自动 keep 250ms；"
            "按“停止”结束。\n",
        )

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
        self.demo_service.cancel()
        self.demo_view.reset_continuous()
        if cancel_sequence and self.sequence_active:
            self.sequence_active = False
            self.sequence_var.set("Stage-I 探测: 已中止（手动停止）")
        if cancel_sequence and self.acc_active:
            self.acc_active = False
            self.sequence_var.set("Stage-I 完整验收: 已中止（手动停止）")
        self.pulse_active = False
        self.continuous_active = False
        self.nonzero_iq_active = False
        self.auto_keep_var.set(False)
        self._update_keepalive_label()
        self.stop_pending = True
        self.stop_attempts = 0
        self.can_stop_not_before = time.monotonic() + 0.3
        self._send_stop_attempt()

    def _send_stop_attempt(self) -> None:
        if not self.stop_pending or not self.connected:
            return
        self.stop_attempts += 1
        if self.active_interface == "can" and self.can_transport is not None:
            try:
                self.can_transport.stop()
                self.can_command_rpm = 0
            except (serial.SerialException, OSError, RuntimeError) as exc:
                self._append_log("error", f"CAN stop failed: {exc}\n")
        else:
            self.send_command("stop", quiet=self.stop_attempts > 1)
        self.after(STOP_RETRY_INTERVAL_MS, self._stop_retry_tick)

    def _stop_retry_tick(self) -> None:
        if not self.stop_pending:
            return
        if self.mci_state == 0 and (
            self.active_interface != "can"
            or time.monotonic() >= self.can_stop_not_before
        ):
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
        try:
            if (
                self.connected
                and self.active_interface == "can"
                and self.nonzero_iq_active
                and self.can_last_feedback_time > 0.0
                and time.monotonic() - self.can_last_feedback_time > 0.75
            ):
                self._append_log("error", "CAN feedback timeout; stopping command refresh.\n")
                self.stop_motor()
            if (
                self.connected
                and (self.auto_keep_var.get() or self.pulse_active
                     or self.continuous_active)
                and self.nonzero_iq_active
            ):
                if self.active_interface == "can" and self.can_transport is not None:
                    try:
                        self.can_transport.command_velocity(self.can_command_rpm)
                        refreshed = True
                    except (ValueError, serial.SerialException, OSError, RuntimeError):
                        refreshed = False
                else:
                    refreshed = self.send_command(
                        "keep", quiet=True, strict_quiet=not self.streaming
                    )
                if not refreshed:
                    self.keep_busy_retry_count += 1
                    if self.active_interface != "can":
                        self.after(100, self._keepalive_retry)
                else:
                    self.keep_sent_count += 1
                if self.pulse_active:
                    remaining_ms = max(
                        0, int((self.pulse_deadline - time.monotonic()) * 1000)
                    )
                    self.keep_status_var.set(
                        f"定时测试: 剩余约 {remaining_ms} ms，自动 Keepalive"
                    )
                else:
                    self.keep_status_var.set(
                        f"Keepalive: 自动刷新中 "
                        f"(sent={self.keep_sent_count}, "
                        f"retry={self.keep_busy_retry_count}, "
                        f"forced={self.keep_forced_count})"
                    )
        except Exception as exc:  # never let the keep chain die silently
            self._append_log("error", f"Keepalive 异常: {exc}\n")
        self.after(KEEPALIVE_INTERVAL_MS, self._keepalive_tick)

    def _keepalive_retry(self) -> None:
        try:
            if not (self.connected and self.nonzero_iq_active):
                return
            if self.send_command(
                "keep", quiet=True, strict_quiet=not self.streaming
            ):
                self.keep_sent_count += 1
                return
            self.keep_busy_retry_count += 1
            if self.keep_busy_retry_count % 10 == 0:
                # The bus stayed busy for many retries. Log it but keep
                # waiting: forcing a half-duplex transmission into an active
                # burst corrupts both directions and makes things worse.
                # With a 300 ms keep period the 1000 ms firmware watchdog
                # still has plenty of margin for a short busy window.
                self.keep_forced_count += 1
                self._append_log(
                    "warn", f"keep 总线持续繁忙 {self.keep_busy_retry_count} "
                    "次，继续等待静默\n"
                )
            self.after(80, self._keepalive_retry)
        except Exception as exc:
            self._append_log("error", f"Keepalive 重试异常: {exc}\n")

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
        self._render_demo_view()

    def clear_log(self) -> None:
        self._log_entries.clear()
        self.log.configure(state=tk.NORMAL)
        self.log.delete("1.0", tk.END)
        self.log.configure(state=tk.DISABLED)
        if self.log_popup_text is not None:
            self.log_popup_text.configure(state=tk.NORMAL)
            self.log_popup_text.delete("1.0", tk.END)
            self.log_popup_text.configure(state=tk.DISABLED)

    def _append_log(self, tag: str, text: str) -> None:
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log_entries.append((tag, timestamp, text))
        line = f"[{timestamp}] {self._ui(text)}"
        self._append_log_widget(self.log, tag, line)
        if self.log_popup_text is not None:
            try:
                self._append_log_widget(self.log_popup_text, tag, line)
            except tk.TclError:
                self.log_popup_text = None
                self.log_popup = None

    def _rerender_logs(self) -> None:
        widgets = [self.log]
        if self.log_popup_text is not None:
            widgets.append(self.log_popup_text)
        for widget in widgets:
            try:
                widget.configure(state=tk.NORMAL)
                widget.delete("1.0", tk.END)
                for tag, timestamp, source in self._log_entries:
                    widget.insert(tk.END, f"[{timestamp}] {self._ui(source)}", tag)
                widget.see(tk.END)
                widget.configure(state=tk.DISABLED)
            except tk.TclError:
                pass

    @staticmethod
    def _append_log_widget(widget: tk.Text, tag: str, text: str) -> None:
        widget.configure(state=tk.NORMAL)
        widget.insert(tk.END, text, tag)
        widget.see(tk.END)
        widget.configure(state=tk.DISABLED)

    def open_log_popup(self) -> None:
        """Open the log in a dedicated, resizable window."""
        if self.log_popup is not None and self.log_popup.winfo_exists():
            self.log_popup.lift()
            self.log_popup.focus_force()
            return
        win = tk.Toplevel(self)
        apply_window_icon(win)
        apply_window_surface(win)
        win.title(self._ui("收发日志 (独立显示)"))
        win.geometry("920x520")
        win.minsize(520, 240)
        text = tk.Text(
            win,
            wrap=tk.NONE,
            state=tk.DISABLED,
            font=("Consolas", 10),
            background=LOG_BACKGROUND,
            foreground=LOG_FOREGROUND,
            insertbackground=LOG_FOREGROUND,
        )
        y_scroll = ttk.Scrollbar(win, orient=tk.VERTICAL, command=text.yview)
        x_scroll = ttk.Scrollbar(win, orient=tk.HORIZONTAL, command=text.xview)
        text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        win.rowconfigure(0, weight=1)
        win.columnconfigure(0, weight=1)
        for tag, color in (
            ("tx", LOG_TX),
            ("rx", LOG_FOREGROUND),
            ("error", LOG_ERROR),
            ("event", LOG_EVENT),
        ):
            text.tag_configure(tag, foreground=color)
        try:
            content = self.log.get("1.0", tk.END)
            if content:
                text.configure(state=tk.NORMAL)
                text.insert(tk.END, content)
                text.see(tk.END)
                text.configure(state=tk.DISABLED)
        except tk.TclError:
            pass
        self.log_popup = win
        self.log_popup_text = text
        win.protocol("WM_DELETE_WINDOW", self._close_log_popup)

    def _close_log_popup(self) -> None:
        if self.log_popup is not None:
            try:
                self.log_popup.destroy()
            except tk.TclError:
                pass
            self.log_popup = None
            self.log_popup_text = None

    def open_can_tool_window(self) -> None:
        if self.can_tool_window is not None:
            try:
                if self.can_tool_window.winfo_exists():
                    self.can_tool_window.deiconify()
                    self.can_tool_window.lift()
                    self.can_tool_window.focus_force()
                    return
            except tk.TclError:
                pass
        self.can_tool_window = CanToolWindow(self, language_var=self.language_var)

    def open_update_dialog(self) -> None:
        if self.update_dialog is not None:
            try:
                if self.update_dialog.winfo_exists():
                    self.update_dialog.lift()
                    self.update_dialog.focus_force()
                    return
            except tk.TclError:
                pass
        self.update_dialog = UpdateDialog(
            self,
            language_getter=self.language_var.get,
            current_version=__version__,
            on_install_ready=self._install_downloaded_update,
        )

    def _install_downloaded_update(self, path: Path, release: UpdateRelease) -> None:
        del release
        language = self.language_var.get()
        if self.connected or self._motor_activity_active():
            messagebox.showwarning(
                tr(language, "update_install_title"),
                tr(language, "update_requires_idle"),
            )
            return
        if not messagebox.askyesno(
            tr(language, "update_install_title"),
            tr(language, "update_install_confirm"),
        ):
            return
        try:
            launch_update_helper(path, Path(sys.executable))
        except Exception as exc:
            messagebox.showerror(
                tr(language, "update_install_title"),
                tr(language, "update_install_failed", error=str(exc)),
            )
            return
        if self.update_dialog is not None:
            try:
                self.update_dialog.destroy()
            except tk.TclError:
                pass
            self.update_dialog = None
        self.after(100, self._on_close)

    def _on_close(self) -> None:
        if self.update_dialog is not None:
            try:
                self.update_dialog.cancel_event.set()
                self.update_dialog.destroy()
            except tk.TclError:
                pass
            self.update_dialog = None
        if self.can_tool_window is not None:
            try:
                self.can_tool_window.close()
            except tk.TclError:
                pass
            self.can_tool_window = None
        if self.connected and self.serial_port is not None:
            try:
                self.send_command("stop", quiet=True)
                time.sleep(0.05)
            except Exception:
                pass
        self.disconnect()
        self.destroy()


if __name__ == "__main__":
    health_marker = health_marker_from_argv()
    app = EasyMotorApp()
    if health_marker is not None:
        app.after(500, acknowledge_healthy_start, health_marker)
    app.mainloop()
