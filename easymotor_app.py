"""EasyMotor CAN demonstration and engineering diagnostics application.

Motion control uses the validated CAN Type 3/1/4 path. UART5 is an optional,
read-only engineering channel for status, logs, and waveform samples. The
firmware remains the authority for limits, watchdogs, state transitions, and
faults.
"""

from __future__ import annotations

import queue
import re
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from itertools import islice
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
from easymotor.controllers import WaveformStore
from easymotor.features.can_parameters import CanParameterPanel
from easymotor.features.mit_bench import MitBenchPanel
from easymotor.features.demo import DemoView
from easymotor.features.update_dialog import UpdateDialog
from easymotor.i18n import (
    DEFAULT_LANGUAGE,
    LocalizedMessageBox,
    LocalizedStringVar,
    localize_legacy,
    tr,
)
from easymotor.models.telemetry import TelemetryModel
from easymotor.protocols.can_motor import (
    CanFrame,
    MitCommand,
    MODE_CALIBRATING,
    MODE_MOTOR,
    MODE_RESET,
    build_parameter_read,
    format_frame,
    parse_device_id_response,
    parse_fault_report,
    parse_feedback,
)
from easymotor.protocols.waveform import (
    WAVE_END_SEQ,
    WAVE_FRAME_LEN,
    WAVE_SINGLE_BLOCK_SAMPLES,
    WAVE_SINGLE_SOF,
    WAVE_SOF,
    WAVE_STATS_FRAME_LEN,
    WAVE_STATS_SOF,
    WaveFrameDecoder,
)
from easymotor.services.demo_service import DemoAction, DemoError, DemoPhase, DemoService
from easymotor.services.can_command_refresher import CanCommandRefresher
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
from easymotor.updates.installer import (
    acknowledge_healthy_start,
    apply_update_from_argv,
    health_marker_from_argv,
)
from easymotor.version import __version__, window_title


messagebox = LocalizedMessageBox(tk_messagebox)


DEFAULT_DEBUG_BAUD = 4_000_000
DEBUG_BAUD_PRESETS = (
    "921600",
    "1000000",
    "2000000",
    "2500000",
    "3000000",
    "4000000",
)
CAN_ENUMERATION_RETRY_MS = 1_000
CAN_ENUMERATION_GUIDANCE_MS = 10_000
CAN_DETECTION_PROBE_MS = 10
CAN_DETECTION_NODE_IDS = (0x7F, 1, 2) + tuple(
    node_id for node_id in range(0x80) if node_id not in (0x7F, 1, 2)
)
COMMAND_REFRESH_INTERVAL_MS = 250
START_TIMEOUT_MS = 30_000
START_ENABLE_RETRY_INTERVAL_MS = 300
START_ENABLE_MAX_ATTEMPTS = 3
MAX_SPEED_RPM = 20
RS485_RX_QUIET_MS = 20
RS485_TX_WAIT_MAX_MS = 50
ENCODER_COUNTS_PER_REV = 16_384
NOMINAL_REDUCTION = 9.0
STOP_RETRY_INTERVAL_MS = 150
STOP_MAX_ATTEMPTS = 3

ENGINEER_TEXT_EN = {
    "工程师模式": "Advanced Engineering",
    "返回演示模式": "Back to Demo",
    "接口状态": "Interface Status",
    "CAN 运动接口": "CAN Motion Interface",
    "RS485 Debug 接口": "RS485 Debug Interface",
    "电机与安全状态": "Motor and Safety Status",
    "运动只由 CAN Control 发起；RS485 Debug 只读取内部状态和波形。": "Motion is initiated only from CAN Control; RS485 Debug only reads internal status and waveforms.",
    "USB-CAN 串口": "USB-CAN COM Port",
    "检测 CAN ID": "Detect CAN ID",
    "新 CAN ID": "New CAN ID",
    "设置 CAN ID": "Set CAN ID",
    "设置 ID 会在验证成功后自动写入 Flash；掉电重启后请重新检测。": "After verification, setting the ID automatically writes it to flash; detect again after a power cycle.",
    "RS485 Debug 连接": "RS485 Debug Connection",
    "调试串口": "Debug COM Port",
    "RS485 Debug 状态": "RS485 Debug Status",
    "CAN Type 2 反馈": "CAN Type 2 Feedback",
    "串口连接": "RS485 Connection",
    "RS485 调试连接": "RS485 Debug Connection",
    "通信连接": "Communication Connections",
    "CAN 运动连接": "CAN Motion Connection",
    "RS485 调试端口": "RS485 Debug Port",
    "打开波形窗口": "Open Waveform Window",
    "端口": "COM port",
    "刷新": "Refresh",
    "连接": "Connect",
    "断开": "Disconnect",
    "控制状态": "Control Status",
    "查询状态": "Query Status",
    "RS485 状态": "RS485 Status",
    "波形窗口": "Waveform",
    "RS485 Debug 运动与 PWM 遥测": "RS485 Debug Motion and PWM Telemetry",
    "实时健康状态": "Real-time Health",
    "运动快照（只读）": "Motion Snapshot (Read-only)",
    "编码器状态": "Encoder Status",
    "显示原始诊断详情": "Show Raw Diagnostic Details",
    "隐藏原始诊断详情": "Hide Raw Diagnostic Details",
    "原始诊断详情": "Raw Diagnostic Details",
    "RS485 Debug 电流波形": "RS485 Debug Current Waveform",
    "停止": "STOP",
    "收发日志": "Communication Log",
    "独立日志窗口": "Open log window",
    "清空日志": "Clear logs",
    "波形流期间日志与波形并行显示；可导出为文本文件。": "Logs remain available during waveform streaming and can be exported.",
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

WAVE_BUFFER_MAX = 250_000
WAVE_GLITCH_LSB = 60
WAVE_TIME_WINDOWS = ("30 ms", "200 ms", "1 s", "2 s", "5 s")
WAVE_REDRAW_INTERVAL_MS = 100
WAVE_DISPLAY_ENVELOPE_HZ = 1_000
RX_QUEUE_INTERVAL_MS = 20
RX_QUEUE_BUSY_INTERVAL_MS = 1
RX_QUEUE_BUDGET_MS = 6.0
RX_QUEUE_MAX_ITEMS = 64
RS485_EVENT_QUEUE_MAX = 1024
WAVE_BATCH_QUEUE_MAX = 8


def parse_debug_baud(text: str) -> int:
    """Parse an editable baud-rate field while enforcing a practical range."""
    try:
        baud_rate = int(text.strip().replace(",", "").replace("_", ""))
    except ValueError as exc:
        raise ValueError("debug baud rate must be an integer") from exc
    if not 9_600 <= baud_rate <= 12_000_000:
        raise ValueError("debug baud rate must be between 9600 and 12000000")
    return baud_rate


def parse_wave_time_window(text: str) -> float:
    value, unit = text.strip().split(maxsplit=1)
    seconds = float(value)
    if unit == "ms":
        seconds /= 1000.0
    elif unit != "s":
        raise ValueError("unsupported waveform time unit")
    if seconds <= 0.0:
        raise ValueError("waveform time window must be positive")
    return seconds


def compress_wave_points(
    points: list[tuple[int, int]], max_columns: int
) -> list[tuple[int, int]]:
    """Preserve per-column extrema while bounding the number of canvas points."""
    if max_columns <= 0 or len(points) <= max_columns * 2:
        return points
    result: list[tuple[int, int]] = []
    for column in range(max_columns):
        start = column * len(points) // max_columns
        end = (column + 1) * len(points) // max_columns
        bucket = points[start:end]
        if not bucket:
            continue
        low_index = 0
        high_index = 0
        for index in range(1, len(bucket)):
            if bucket[index][1] < bucket[low_index][1]:
                low_index = index
            if bucket[index][1] > bucket[high_index][1]:
                high_index = index
        for index in sorted({low_index, high_index}):
            result.append(bucket[index])
    return result


def compress_envelope_entries(
    entries: list[tuple[int, int, int]], max_columns: int
) -> list[tuple[int, int, int]]:
    """Merge time-ordered min/max entries into at most one item per pixel."""
    if max_columns <= 0 or len(entries) <= max_columns:
        return entries
    result: list[tuple[int, int, int]] = []
    for column in range(max_columns):
        start = column * len(entries) // max_columns
        end = (column + 1) * len(entries) // max_columns
        if start >= end:
            continue
        bucket = entries[start:end]
        result.append(
            (
                bucket[0][0],
                min(item[1] for item in bucket),
                max(item[2] for item in bucket),
            )
        )
    return result


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
        self.rs485_rx_queue: queue.Queue[tuple[str, object]] = queue.Queue(
            maxsize=RS485_EVENT_QUEUE_MAX
        )
        self._rs485_queue_drop_count = 0
        self.wave_batch_queue: queue.Queue[list[tuple[str, object]]] = queue.Queue(
            maxsize=WAVE_BATCH_QUEUE_MAX
        )
        self._wave_host_queue_drop_count = 0
        self.can_command_refresher = CanCommandRefresher(
            self.rx_queue,
            interval_s=COMMAND_REFRESH_INTERVAL_MS / 1000.0,
        )
        self.mit_command_refresher = CanCommandRefresher(
            self.rx_queue,
            interval_s=0.01,
            feedback_timeout_s=0.20,
        )
        self.telemetry_model = TelemetryModel()
        self.wave_decoder = WaveFrameDecoder()
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
        self.can_active_report_enabled = False
        self.can_command_rpm = 0
        self.can_enumeration_started = 0.0
        self.can_enumeration_generation = 0
        self.can_enumeration_guidance_shown = False
        self.can_enumeration_probe_index = 0
        self.can_detection_active = False
        self.can_detection_generation = 0
        self.can_node_change_pending: tuple[int, int] | None = None
        self.can_node_change_generation = 0
        self.can_stop_not_before = 0.0
        self.mci_state: int | None = None
        self.start_waiting = False
        self.start_deadline = 0.0
        self.start_enable_attempts = 0
        self.start_enable_retry_at = 0.0
        self.motion_command_active = False
        self.pulse_active = False
        self.continuous_active = False
        self.pulse_deadline = 0.0
        self.stop_pending = False
        self.stop_attempts = 0

        self.eangle_pending = False
        self.eangle_bins: list[tuple[int, ...]] = []
        self._rt_health_fragment = ""
        self._enc_health_fragment = ""

        self.port_var = tk.StringVar()
        self.debug_port_var = tk.StringVar()
        self.debug_baud_var = tk.StringVar(value=str(DEFAULT_DEBUG_BAUD))
        localized_var = lambda value="": LocalizedStringVar(self, self.language_var.get, value)
        self.connection_var = localized_var(tr(DEFAULT_LANGUAGE, "not_connected"))
        self.can_device_var = tk.StringVar(value="CAN ID: not detected")
        self.can_new_node_id_var = tk.IntVar(value=1)
        self.debug_connection_var = localized_var(tr(DEFAULT_LANGUAGE, "not_connected"))
        self.engineer_can_status_var = localized_var(tr(DEFAULT_LANGUAGE, "connect_first"))
        self.can_feedback_var = localized_var("CAN Type 2 反馈：尚未收到")
        self.temperature_var = tk.StringVar()
        self.board_temperature_c: float | None = None
        self.motor_temperature_c: float | None = None
        self._last_can_feedback = None
        self._temperature_poll_index = 0
        self.rotor_alignment_valid: bool | None = None
        self.state_var = localized_var("MCI: 未知")
        self.rs485_state_var = localized_var("RS485 MCI：尚未读取")
        self.torque_var = localized_var("TORQUE: 未知")
        self.command_refresh_var = localized_var("CAN command refresh: idle")
        self.command_refresh_count = 0
        self.rs485_details_visible = False
        self.freq_var = localized_var("PWM/FOC: 未知")
        self._freq_pwm_hz: int | None = None
        self._freq_foc_hz: int | None = None
        self._freq_enc_rt_hz: int | None = None
        self._legacy_foc_peak_percent: float | None = None
        self._status_request_generation = 0
        self._rt_last_t_ms: int | None = None
        self._rt_last_adc: int | None = None
        self.motion_var = localized_var("位置: 等待固件 MOTION 遥测")
        self.angle_var = localized_var("电角度/速度: 未知")
        self.pwm_var = localized_var("CCR/Clamp: 未知")
        self.speed_control_var = localized_var("速度环: 未启用")
        self.health_var = localized_var("编码器/实时健康: 未知")
        self.cpu_load_var = localized_var("CPU real-time load: unknown")
        self.encoder_models_var = localized_var("Encoder models: unknown")
        self.eangle_var = localized_var(
            "电角度纹波: 尚未测量（请先运行电机，再停止）"
        )

        self.streaming = False
        self.wave_dec_var = tk.IntVar(value=10)
        self.wave_u_var = tk.BooleanVar(value=True)
        self.wave_v_var = tk.BooleanVar(value=True)
        self.wave_w_var = tk.BooleanVar(value=True)
        self.wave_stats_var = tk.BooleanVar(value=False)
        self.wave_single_var = tk.BooleanVar(value=False)
        self.wave_single_channel_var = tk.StringVar(value="U")
        self.wave_time_window_var = tk.StringVar(value="1 s")
        self._wave_sample_rate_hz = 5_000.0
        self.wave_scale_var = localized_var("自动")
        self.wave_status_var = localized_var("波形: 停止")
        self.waveform_store = WaveformStore(
            raw_capacity=WAVE_BUFFER_MAX,
            display_capacity=5 * WAVE_DISPLAY_ENVELOPE_HZ,
            glitch_threshold=WAVE_GLITCH_LSB,
        )
        self._sync_waveform_store()
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
        self._log_entries: list[tuple[str, str, str, str]] = []
        self._active_log_channel = "app"
        self.update_dialog: UpdateDialog | None = None
        self.demo_service = DemoService()
        self.demo_speed_mode_selected = False
        self.app_mode = "demo"

        self._build_ui()
        self.refresh_ports()
        self.after(20, self._process_rx_queue)
        self.after(WAVE_REDRAW_INTERVAL_MS, self._wave_redraw)
        self.after(1000, self._temperature_poll_tick)
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
            device_var=self.can_device_var,
            temperature_var=self.temperature_var,
            interface_var=self.interface_var,
            language_var=self.language_var,
            on_refresh=self.refresh_ports,
            on_toggle_connection=self.toggle_connection,
            on_detect_device=self.detect_can_device,
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

        self.engineer_notebook = ttk.Notebook(outer)
        self.engineer_notebook.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.engineer_overview_tab = ttk.Frame(self.engineer_notebook)
        self.engineer_can_tab = ttk.Frame(self.engineer_notebook)
        self.engineer_rs485_tab = ttk.Frame(self.engineer_notebook)
        self.engineer_log_tab = ttk.Frame(self.engineer_notebook)
        self._engineer_tab_titles = (
            (self.engineer_overview_tab, "总览", "Overview"),
            (self.engineer_can_tab, "CAN 控制", "CAN Control"),
            (self.engineer_rs485_tab, "RS485 调试", "RS485 Debug"),
            (self.engineer_log_tab, "收发日志", "Logs"),
        )
        for tab, chinese, _english in self._engineer_tab_titles:
            self.engineer_notebook.add(tab, text=chinese)

        overview_interfaces = ttk.LabelFrame(
            self.engineer_overview_tab, text="接口状态", padding=12
        )
        overview_interfaces.pack(fill=tk.X, padx=8, pady=8)
        ttk.Label(overview_interfaces, text="CAN 运动接口").grid(
            row=0, column=0, padx=(0, 18), sticky="w"
        )
        ttk.Label(overview_interfaces, textvariable=self.connection_var).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(overview_interfaces, text="RS485 Debug 接口").grid(
            row=1, column=0, padx=(0, 18), pady=(8, 0), sticky="w"
        )
        ttk.Label(overview_interfaces, textvariable=self.debug_connection_var).grid(
            row=1, column=1, pady=(8, 0), sticky="w"
        )
        overview_interfaces.columnconfigure(2, weight=1)

        overview_motor = ttk.LabelFrame(
            self.engineer_overview_tab, text="电机与安全状态", padding=12
        )
        overview_motor.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(
            overview_motor,
            textvariable=self.engineer_can_status_var,
            font=("Microsoft YaHei UI", 12),
        ).pack(anchor="w")
        ttk.Label(overview_motor, textvariable=self.can_feedback_var).pack(
            anchor="w", pady=(8, 0)
        )
        ttk.Label(overview_motor, textvariable=self.temperature_var).pack(
            anchor="w", pady=(8, 0)
        )
        ttk.Label(
            overview_motor,
            text="运动只由 CAN Control 发起；RS485 Debug 只读取内部状态和波形。",
            foreground=WARNING_TEXT,
        ).pack(anchor="w", pady=(10, 0))

        can_connection = ttk.LabelFrame(
            self.engineer_can_tab, text="CAN 运动连接", padding=10
        )
        can_connection.pack(fill=tk.X, padx=8, pady=8)
        ttk.Label(can_connection, text="USB-CAN 串口").grid(
            row=0, column=0, padx=(0, 6)
        )
        self.advanced_can_port_combo = ttk.Combobox(
            can_connection, textvariable=self.port_var, width=18, state="readonly"
        )
        self.advanced_can_port_combo.grid(row=0, column=1, padx=(0, 6))
        self.advanced_can_refresh_button = ttk.Button(
            can_connection, text="刷新", command=self.refresh_ports
        )
        self.advanced_can_refresh_button.grid(row=0, column=2, padx=(0, 12))
        self.advanced_can_connect_button = ttk.Button(
            can_connection, text="连接", command=self.toggle_connection
        )
        self.advanced_can_connect_button.grid(row=0, column=3)
        self.advanced_can_detect_button = ttk.Button(
            can_connection, text="检测 CAN ID", command=self.detect_can_device
        )
        self.advanced_can_detect_button.grid(row=0, column=4, padx=(8, 0))
        ttk.Label(can_connection, textvariable=self.connection_var).grid(
            row=0, column=5, padx=(12, 0), sticky="w"
        )
        ttk.Label(can_connection, textvariable=self.can_device_var).grid(
            row=0, column=6, padx=(12, 0), sticky="w"
        )
        ttk.Label(can_connection, text="新 CAN ID").grid(
            row=1, column=0, pady=(8, 0), sticky="w"
        )
        self.advanced_can_node_id_spinbox = ttk.Spinbox(
            can_connection,
            from_=1,
            to=127,
            textvariable=self.can_new_node_id_var,
            width=7,
        )
        self.advanced_can_node_id_spinbox.grid(
            row=1, column=1, pady=(8, 0), sticky="w"
        )
        self.advanced_can_set_id_button = ttk.Button(
            can_connection, text="设置 CAN ID", command=self.set_detected_can_id
        )
        self.advanced_can_set_id_button.grid(
            row=1, column=2, padx=(0, 12), pady=(8, 0)
        )
        ttk.Label(
            can_connection,
            text="设置 ID 会在验证成功后自动写入 Flash；掉电重启后请重新检测。",
            foreground=WARNING_TEXT,
        ).grid(row=1, column=3, columnspan=4, padx=(12, 0), pady=(8, 0), sticky="w")
        ttk.Label(
            can_connection,
            textvariable=self.can_feedback_var,
            width=88,
            anchor="w",
            font=("Consolas", 10),
        ).grid(
            row=2, column=0, columnspan=7, pady=(10, 0), sticky="w"
        )
        ttk.Label(
            can_connection,
            textvariable=self.temperature_var,
            width=88,
            anchor="w",
            font=("Consolas", 10),
        ).grid(
            row=3, column=0, columnspan=7, pady=(5, 0), sticky="w"
        )
        can_connection.columnconfigure(7, weight=1)

        debug_connection = ttk.LabelFrame(
            self.engineer_rs485_tab, text="RS485 Debug 连接", padding=10
        )
        debug_connection.pack(fill=tk.X, padx=8, pady=8)
        ttk.Label(debug_connection, text="调试串口").grid(
            row=0, column=0, padx=(0, 6)
        )
        self.port_combo = ttk.Combobox(
            debug_connection,
            textvariable=self.debug_port_var,
            width=18,
            state="readonly",
        )
        self.port_combo.grid(row=0, column=1, padx=(0, 6))
        ttk.Button(debug_connection, text="刷新", command=self.refresh_ports).grid(
            row=0, column=2, padx=(0, 12)
        )
        ttk.Label(debug_connection, text="波特率").grid(
            row=0, column=3, padx=(0, 4)
        )
        self.debug_baud_combo = ttk.Combobox(
            debug_connection,
            textvariable=self.debug_baud_var,
            values=DEBUG_BAUD_PRESETS,
            width=11,
            state="normal",
        )
        self.debug_baud_combo.grid(row=0, column=4, padx=(0, 4))
        ttk.Label(debug_connection, text="baud / 8N1 / RS485").grid(
            row=0, column=5, padx=(0, 12)
        )
        self.connect_button = ttk.Button(
            debug_connection, text="连接", command=self.toggle_debug_connection
        )
        self.connect_button.grid(row=0, column=6)
        ttk.Label(debug_connection, textvariable=self.debug_connection_var).grid(
            row=0, column=7, padx=(12, 0), sticky="w"
        )
        debug_connection.columnconfigure(8, weight=1)

        state_frame = ttk.LabelFrame(
            self.engineer_rs485_tab,
            text="RS485 Debug 状态",
            padding=10,
        )
        state_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(
            state_frame,
            textvariable=self.rs485_state_var,
            font=("Microsoft YaHei UI", 12),
        ).grid(row=0, column=0, sticky="w")
        ttk.Button(
            state_frame, text="RS485 状态", command=self._request_debug_status
        ).grid(row=1, column=0, padx=(0, 4), pady=(6, 0), sticky="w")
        state_frame.columnconfigure(3, weight=1)

        health_frame = ttk.LabelFrame(
            self.engineer_rs485_tab, text="实时健康状态", padding=10
        )
        health_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(health_frame, textvariable=self.cpu_load_var).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(health_frame, textvariable=self.health_var).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        ttk.Label(health_frame, textvariable=self.temperature_var).grid(
            row=2, column=0, sticky="w", pady=(4, 0)
        )
        health_frame.columnconfigure(0, weight=1)

        motion_frame = ttk.LabelFrame(
            self.engineer_rs485_tab, text="运动快照（只读）", padding=10
        )
        motion_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(motion_frame, textvariable=self.motion_var).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(motion_frame, textvariable=self.speed_control_var).grid(
            row=1, column=0, sticky="w", pady=(4, 0)
        )
        motion_frame.columnconfigure(0, weight=1)

        encoder_frame = ttk.LabelFrame(
            self.engineer_rs485_tab, text="编码器状态", padding=10
        )
        encoder_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Label(encoder_frame, textvariable=self.encoder_models_var).grid(
            row=0, column=0, sticky="w"
        )
        encoder_frame.columnconfigure(0, weight=1)

        self.rs485_details_button = ttk.Button(
            self.engineer_rs485_tab,
            text="显示原始诊断详情",
            command=self._toggle_rs485_details,
        )
        self.rs485_details_button.pack(anchor="w", padx=8, pady=(0, 8))
        self.rs485_details_frame = ttk.LabelFrame(
            self.engineer_rs485_tab, text="原始诊断详情", padding=10
        )
        for row, variable in enumerate(
            (self.torque_var, self.angle_var, self.pwm_var, self.eangle_var, self.freq_var)
        ):
            ttk.Label(self.rs485_details_frame, textvariable=variable).grid(
                row=row, column=0, sticky="w", pady=((0 if row == 0 else 4), 0)
            )
        self.rs485_details_frame.columnconfigure(0, weight=1)

        waveform_frame = ttk.LabelFrame(
            self.engineer_rs485_tab, text="RS485 Debug 电流波形", padding=10
        )
        self.rs485_waveform_frame = waveform_frame
        waveform_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        ttk.Button(
            waveform_frame, text="波形窗口", command=self.open_wave_popup
        ).grid(row=0, column=0, padx=(0, 12), sticky="w")
        ttk.Label(waveform_frame, textvariable=self.wave_status_var).grid(
            row=0, column=1, sticky="w"
        )
        ttk.Label(
            waveform_frame,
            text="波形流期间日志与波形并行显示；可导出为文本文件。",
        ).grid(row=0, column=2, padx=(20, 0), sticky="w")
        waveform_frame.columnconfigure(3, weight=1)

        self.mit_bench_panel = MitBenchPanel(
            self.engineer_can_tab,
            language_getter=self.language_var.get,
            connected_getter=self._parameter_connection_ready,
            feedback_getter=lambda: self._last_can_feedback,
            set_zero=self._mit_set_zero,
            read_alignment=self._read_rotor_alignment,
            save_alignment=self._save_rotor_alignment,
            enable=self.start_motor,
            stop=self.stop_motor,
            send_once=self._mit_send_once,
            start_hold=self._mit_start_hold,
        )
        self.mit_bench_panel.pack(fill=tk.X, padx=8, pady=(0, 8))

        parameter_frame = ttk.LabelFrame(
            self.engineer_can_tab, text="CAN 参数", padding=10
        )
        parameter_frame.pack(fill=tk.X, padx=8, pady=(0, 8))
        self.can_parameter_panel = CanParameterPanel(
            parameter_frame,
            language_var=self.language_var,
            send_frame=self._send_parameter_frame,
            connected_getter=self._parameter_connection_ready,
            idle_getter=self._parameter_operation_idle,
            log_callback=lambda level, text: self._append_log(level, text, "can"),
            state_callback=self._update_control_state,
            value_callback=self._on_parameter_value,
        )
        self.can_parameter_panel.pack(fill=tk.X)

        log_frame = ttk.LabelFrame(
            self.engineer_log_tab, text="收发日志", padding=6
        )
        log_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        log_tools = ttk.Frame(log_frame)
        log_tools.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Button(
            log_tools, text="独立日志窗口", command=self.open_log_popup
        ).grid(row=0, column=0, padx=(0, 8))
        ttk.Button(
            log_tools, text="清空日志", command=self.clear_log
        ).grid(row=0, column=1, padx=(0, 8))
        ttk.Label(
            log_tools, text="波形流期间日志与波形并行显示；可导出为文本文件。"
        ).grid(row=0, column=2, sticky="w")
        log_tools.columnconfigure(3, weight=1)
        self.log_notebook = ttk.Notebook(log_frame)
        self.log_notebook.grid(row=1, column=0, columnspan=2, sticky="nsew")
        self.log_widgets: dict[str, tk.Text] = {}
        self._log_tab_titles = (
            ("all", "全部", "All"),
            ("can", "CAN", "CAN"),
            ("rs485", "RS485 Debug", "RS485 Debug"),
            ("app", "应用", "Application"),
            ("operation", "操作", "Operation"),
        )
        self._log_tabs: dict[str, ttk.Frame] = {}
        for channel, chinese, _english in self._log_tab_titles:
            tab = ttk.Frame(self.log_notebook)
            text = tk.Text(
                tab,
                wrap=tk.NONE,
                state=tk.DISABLED,
                font=("Consolas", 10),
                background=LOG_BACKGROUND,
                foreground=LOG_FOREGROUND,
                insertbackground=LOG_FOREGROUND,
            )
            y_scroll = ttk.Scrollbar(tab, orient=tk.VERTICAL, command=text.yview)
            x_scroll = ttk.Scrollbar(tab, orient=tk.HORIZONTAL, command=text.xview)
            text.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
            text.grid(row=0, column=0, sticky="nsew")
            y_scroll.grid(row=0, column=1, sticky="ns")
            x_scroll.grid(row=1, column=0, sticky="ew")
            tab.rowconfigure(0, weight=1)
            tab.columnconfigure(0, weight=1)
            for tag, color in (
                ("tx", LOG_TX),
                ("rx", LOG_FOREGROUND),
                ("error", LOG_ERROR),
                ("event", LOG_EVENT),
            ):
                text.tag_configure(tag, foreground=color)
            self.log_notebook.add(tab, text=chinese)
            self._log_tabs[channel] = tab
            self.log_widgets[channel] = text
        self.log = self.log_widgets["all"]
        log_frame.rowconfigure(1, weight=1)
        log_frame.columnconfigure(0, weight=1)
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
        if hasattr(self, "_log_tab_titles"):
            english = self.language_var.get() == "en"
            for channel, chinese, translated in self._log_tab_titles:
                self.log_notebook.tab(
                    self._log_tabs[channel], text=translated if english else chinese
                )
        self.connect_button.configure(
            text=tr(
                self.language_var.get(),
                "disconnect" if self.serial_port is not None else "connect",
            )
        )
        self.advanced_can_connect_button.configure(
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

    def _toggle_rs485_details(self) -> None:
        self.rs485_details_visible = not self.rs485_details_visible
        if self.rs485_details_visible:
            self.rs485_details_frame.pack(
                fill=tk.X,
                padx=8,
                pady=(0, 8),
                before=self.rs485_waveform_frame,
            )
            source = "隐藏原始诊断详情"
        else:
            self.rs485_details_frame.pack_forget()
            source = "显示原始诊断详情"
        self.rs485_details_button._easymotor_source_text = source
        self.rs485_details_button.configure(
            text=ENGINEER_TEXT_EN.get(source, source)
            if self.language_var.get() == "en"
            else source
        )

    def _render_can_connection_text(self) -> None:
        if not self.connected or self.active_interface != "can":
            self.connection_var.set(tr(self.language_var.get(), "not_connected"))
            return
        port = self.port_var.get()
        self.connection_var.set(
            tr(
                self.language_var.get(),
                "can_connection_summary",
                port=port,
                baud=f"{USB_CAN_BAUD:,}",
            )
        )

    def _refresh_localized_vars(self) -> None:
        for value in vars(self).values():
            if isinstance(value, LocalizedStringVar):
                value.refresh_language()

    def show_demo_mode(self, force: bool = False) -> None:
        self.engineer_view.pack_forget()
        self.demo_view.pack(fill=tk.BOTH, expand=True)
        self.app_mode = "demo"
        self.geometry("900x680")
        self.minsize(820, 620)
        self._render_demo_view()

    def show_engineer_mode(self) -> None:
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
        self.engineer_notebook.select(self.engineer_overview_tab)
        self.geometry("1120x760")
        self.minsize(920, 650)

    def _motor_activity_active(self) -> bool:
        return bool(
            self.start_waiting
            or self.pulse_active
            or self.continuous_active
            or self.motion_command_active
            or self.stop_pending
            or self.mci_state == 6
            or self.demo_service.phase != DemoPhase.IDLE
        )

    def start_demo_run(self, direction: int, speed_rpm: int, continuous: bool) -> None:
        other_active = bool(
            self.start_waiting
            or self.pulse_active
            or self.continuous_active
            or self.stop_pending
            or self.can_parameter_panel.busy
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
            try:
                assert self.can_transport is not None
                self.can_transport.select_speed_mode()
                self.demo_speed_mode_selected = True
                self._append_log(
                    "event",
                    "CAN Type 18 selected explicit run_mode=2 speed PI demo.\n",
                )
            except (ValueError, serial.SerialException, OSError, RuntimeError) as exc:
                self.demo_service.cancel()
                self._append_log("error", f"CAN speed-mode select failed: {exc}\n")
                self._render_demo_view()
                return
            if not self.start_motor():
                self.demo_service.cancel()
                self.stop_motor()
        else:
            self._execute_demo_action(action)
        self._render_demo_view()

    def _restore_mit_mode_after_demo(self) -> None:
        if not self.demo_speed_mode_selected or self.can_transport is None:
            return
        try:
            self.can_transport.select_mit_mode()
            self.demo_speed_mode_selected = False
            self._append_log(
                "event", "已恢复 run_mode=0 MIT 安全默认模式。\n"
            )
        except (ValueError, serial.SerialException, OSError, RuntimeError) as exc:
            self._append_log("error", f"恢复 run_mode=0 失败: {exc}\n")

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
        self._render_temperature_text()
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
            text = tr(
                language,
                "can_waiting_help"
                if self.can_enumeration_guidance_shown
                else "can_waiting_power",
            )
        elif self.mci_state == 0:
            text = tr(language, "ready")
        elif self.mci_state == 6:
            text = tr(language, "enabled")
        elif self.mci_state is None:
            text = tr(language, "reading")
        else:
            text = tr(language, "unavailable")
        activity = self._motor_activity_active() or self.can_parameter_panel.busy
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
            detect_enabled=bool(
                self.connected and not activity and not self.can_detection_active
            ),
        )
        if hasattr(self, "advanced_can_connect_button"):
            self.advanced_can_connect_button.configure(
                text=tr(language, "disconnect" if self.connected else "connect"),
                state=(tk.NORMAL if self.connected or not activity else tk.DISABLED),
            )
            can_port_state = (
                "readonly" if not self.connected and not activity else tk.DISABLED
            )
            self.advanced_can_port_combo.configure(state=can_port_state)
            self.advanced_can_refresh_button.configure(
                state=tk.NORMAL if not self.connected and not activity else tk.DISABLED
            )
            self.advanced_can_detect_button.configure(
                state=(
                    tk.NORMAL
                    if self.connected and not activity and not self.can_detection_active
                    else tk.DISABLED
                )
            )
            node_management_enabled = bool(
                self.connected
                and self.can_uid is not None
                and self.mci_state == 0
                and not activity
                and not self.can_detection_active
                and self.can_node_change_pending is None
            )
            self.advanced_can_node_id_spinbox.configure(
                state=tk.NORMAL if node_management_enabled else tk.DISABLED
            )
            self.advanced_can_set_id_button.configure(
                state=tk.NORMAL if node_management_enabled else tk.DISABLED
            )
            self.engineer_can_status_var.set(text)
        if hasattr(self, "can_parameter_panel"):
            self.can_parameter_panel.refresh_state()

    def on_language_changed(self) -> None:
        self.title(window_title(tr(self.language_var.get(), "app_title")))
        self.copyright_var.set(tr(self.language_var.get(), "copyright"))
        self.update_button.configure(text=tr(self.language_var.get(), "check_updates"))
        self._refresh_localized_vars()
        self.cpu_load_var.set(self._format_cpu_load())
        self.encoder_models_var.set(self._format_encoder_models())
        self._render_temperature_text()
        self._render_can_feedback()
        self._render_can_connection_text()
        if self.serial_port is None:
            self.debug_connection_var.set(tr(self.language_var.get(), "not_connected"))
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
        self.can_parameter_panel.refresh_language()
        self.mit_bench_panel.refresh_language()
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

    def _format_cpu_load(self) -> str:
        value = self.telemetry_model.cpu_load
        language = self.language_var.get()
        if value is None:
            if self._legacy_foc_peak_percent is not None:
                return tr(
                    language,
                    "cpu_load_legacy",
                    foc_peak=self._legacy_foc_peak_percent,
                )
            return tr(language, "cpu_load_unknown")
        completion = value.encoder_completion_percent
        if completion is None:
            encoder_rate = tr(language, "encoder_rate_unknown")
        else:
            encoder_rate = tr(
                language,
                "encoder_rate_format",
                actual=value.encoder_completed_hz / 1000.0,
                target=value.encoder_requested_hz / 1000.0,
                percent=completion,
                status=tr(
                    language,
                    "encoder_rate_low" if value.encoder_rate_low else "encoder_rate_ok",
                ),
            )
        return tr(
            language,
            "cpu_load_format",
            rt=value.realtime_permille / 10.0,
            foc_avg=value.foc_average_permille / 10.0,
            foc_peak=value.foc_peak_permille / 10.0,
            enc_avg=value.encoder_average_permille / 10.0,
            enc_peak=value.encoder_peak_permille / 10.0,
            encoder_rate=encoder_rate,
        )

    def _format_encoder_models(self) -> str:
        values = self.telemetry_model.encoder_values()
        language = self.language_var.get()
        if not values:
            if self.telemetry_model.as5047p_health_seen:
                return tr(language, "encoder_models_legacy")
            return tr(language, "encoder_models_unknown")
        return " | ".join(
            tr(
                language,
                "encoder_model_item",
                role=value.role,
                model=value.model_name,
                port=value.port,
            )
            for value in values
        )

    def refresh_ports(self) -> None:
        ports = [port.device for port in list_ports.comports()]
        self.port_combo["values"] = ports
        self.advanced_can_port_combo["values"] = ports
        self.demo_view.set_ports(ports)
        if self.port_var.get() not in ports:
            self.port_var.set(ports[0] if ports else "")
        if self.serial_port is None and self.debug_port_var.get() not in ports:
            available_debug_ports = [port for port in ports if port != self.port_var.get()]
            self.debug_port_var.set(
                available_debug_ports[0] if available_debug_ports else ""
            )

    def toggle_connection(self) -> None:
        if self.connected:
            self._disconnect_can()
        else:
            self.connect()

    def toggle_debug_connection(self) -> None:
        if self.serial_port is not None:
            self._disconnect_debug()
        else:
            self._connect_debug()

    def _connection_interface(self) -> str:
        """Customer motion always uses CAN, independent of the visible page."""
        return "can"

    def connect(self) -> None:
        port = self.port_var.get()
        if not port:
            messagebox.showwarning(
                tr(self.language_var.get(), "no_port_title"),
                tr(self.language_var.get(), "no_port"),
            )
            return
        self._connect_can(port)

    def _connect_debug(self) -> None:
        port = self.debug_port_var.get()
        if not port:
            messagebox.showwarning(
                tr(self.language_var.get(), "no_port_title"),
                tr(self.language_var.get(), "no_port"),
            )
            return
        if self.connected and port.casefold() == self.port_var.get().casefold():
            english = self.language_var.get() == "en"
            messagebox.showwarning(
                "Port conflict" if english else "端口冲突",
                (
                    "CAN and RS485 Debug must use two different COM ports. "
                    "Select the other port for RS485 Debug."
                    if english
                    else "CAN 和 RS485 调试必须选择两个不同的串口。请为 RS485 调试选择另一个端口。"
                ),
                parent=self,
            )
            return
        try:
            baud_rate = parse_debug_baud(self.debug_baud_var.get())
        except ValueError:
            messagebox.showerror(
                "无效波特率",
                "请输入 9600 到 12000000 之间的整数波特率。",
                parent=self,
            )
            return
        self.debug_baud_var.set(str(baud_rate))
        try:
            connection = serial.Serial(
                port=port,
                baudrate=baud_rate,
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
        self.telemetry_model = TelemetryModel()
        self.cpu_load_var.set(self._format_cpu_load())
        self.encoder_models_var.set(self._format_encoder_models())
        self.rs485_state_var.set("RS485 MCI：等待状态")
        self._freq_pwm_hz = None
        self._freq_foc_hz = None
        self._freq_enc_rt_hz = None
        self._legacy_foc_peak_percent = None
        self._status_request_generation += 1
        self._rt_last_t_ms = None
        self._rt_last_adc = None
        self.freq_var.set("PWM/FOC: 未知")
        self.reader_stop.clear()
        self.reader_thread = threading.Thread(
            target=self._reader_loop, name="robot-joint-uart", daemon=True
        )
        self.reader_thread.start()
        self.debug_baud_combo.configure(state="disabled")
        self.debug_connection_var.set(f"{port} | RS485 Debug @ {baud_rate:,}")
        self.connect_button.configure(text=tr(self.language_var.get(), "disconnect"))
        self._append_log("event", f"RS485 Debug connected {port} @ {baud_rate}\n")
        self._update_control_state()
        self.after(100, self._request_debug_status)

    def _request_debug_status(self) -> None:
        before_cpu_updates = self.telemetry_model.cpu_update_count
        before_encoder_updates = self.telemetry_model.encoder_update_count
        self._status_request_generation += 1
        generation = self._status_request_generation
        if not self.send_command("status"):
            return

        def verify_summary() -> None:
            if generation != self._status_request_generation:
                return
            cpu_missing = (
                self.telemetry_model.cpu_update_count == before_cpu_updates
            )
            encoders_missing = (
                self.telemetry_model.encoder_update_count
                == before_encoder_updates
            )
            if cpu_missing or encoders_missing:
                message = tr(self.language_var.get(), "telemetry_summary_missing")
                self._append_log("error", message + "\n", "rs485")
                self.cpu_load_var.set(self._format_cpu_load())
                self.encoder_models_var.set(self._format_encoder_models())

        self.after(1200, verify_summary)

    def _connect_can(self, port: str) -> None:
        # Discovery must not trust a node selected during an earlier session.
        # The official factory ID is probed first; the detector can scan all
        # legal IDs without changing the active motion target.
        transport = UsbCanMotorTransport(self.rx_queue, node_id=0x7F)
        try:
            transport.connect(port)
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
        self.rotor_alignment_valid = None
        self.mit_bench_panel.set_alignment_valid(None)
        self.can_last_feedback_time = 0.0
        self.can_active_report_enabled = False
        self.can_feedback_var.set("CAN Type 2 反馈：等待设备枚举")
        self.can_command_rpm = 0
        self.can_enumeration_started = time.monotonic()
        self.can_enumeration_generation += 1
        generation = self.can_enumeration_generation
        self.can_enumeration_guidance_shown = False
        self.can_enumeration_probe_index = 0
        self.can_detection_active = False
        self.can_detection_generation += 1
        self.can_node_change_pending = None
        self.can_node_change_generation += 1
        self.can_device_var.set("CAN ID: detecting...")
        self._render_can_connection_text()
        self._append_log("event", f"EasyMotor CAN connected {port} @ {USB_CAN_BAUD}\n")
        self._operation_log("event", f"CAN connected {port}.\n")
        self._append_log(
            "event",
            "Waiting for CAN motor power; device discovery will retry automatically.\n",
        )
        self._update_control_state()
        self.after(
            CAN_ENUMERATION_RETRY_MS,
            lambda: self._retry_can_enumeration(generation),
        )

    def _retry_can_enumeration(self, generation: int) -> None:
        """Keep the adapter connected and discover a motor powered later."""
        if (
            generation != self.can_enumeration_generation
            or not self.connected
            or self.active_interface != "can"
            or self.can_uid is not None
        ):
            return
        transport = self.can_transport
        if transport is None:
            return
        try:
            probe_ids = (getattr(transport, "node_id", 0x7F), 1, 2, 0x7F)
            probe_index = self.__dict__.get("can_enumeration_probe_index", 0)
            probe_id = probe_ids[probe_index % len(probe_ids)]
            self.can_enumeration_probe_index = probe_index + 1
            transport.enumerate(probe_id)
        except (serial.SerialException, OSError, RuntimeError) as exc:
            self._append_log("error", f"USB-CAN enumeration retry failed: {exc}\n")
            self._disconnect_can()
            return
        if (
            not self.can_enumeration_guidance_shown
            and time.monotonic() - self.can_enumeration_started
            >= CAN_ENUMERATION_GUIDANCE_MS / 1000.0
        ):
            self.can_enumeration_guidance_shown = True
            self._append_log(
                "event", tr(self.language_var.get(), "can_standby_hint") + "\n"
            )
        self._render_demo_view()
        self.after(
            CAN_ENUMERATION_RETRY_MS,
            lambda: self._retry_can_enumeration(generation),
        )

    def detect_can_device(self) -> None:
        """Scan all valid node IDs using read-only Type 0 probes."""
        if (
            not self.connected
            or self.active_interface != "can"
            or self.can_transport is None
        ):
            messagebox.showwarning(
                "CAN", "Connect the USB-CAN adapter before detecting a device."
            )
            return
        if self._motor_activity_active() or self.can_parameter_panel.busy:
            messagebox.showwarning(
                "CAN", "Stop the motor and parameter operations before detection."
            )
            return
        self.can_enumeration_generation += 1
        self.can_detection_generation += 1
        generation = self.can_detection_generation
        self.can_detection_active = True
        self.can_uid = None
        self.rotor_alignment_valid = None
        self.mit_bench_panel.set_alignment_valid(None)
        self.mci_state = None
        self.can_device_var.set("CAN ID: detecting...")
        self._append_log(
            "event", "CAN device detection started (Type 0 read-only scan).\n", "can"
        )
        self._operation_log("event", "CAN device detection started.\n")
        self._render_demo_view()
        self._detect_can_node(generation, 0)

    def _detect_can_node(self, generation: int, index: int) -> None:
        if (
            generation != self.can_detection_generation
            or not self.can_detection_active
            or not self.connected
            or self.can_transport is None
        ):
            return
        if index >= len(CAN_DETECTION_NODE_IDS):
            self.can_detection_active = False
            self.can_device_var.set("CAN ID: not detected")
            self._append_log(
                "error", "CAN device detection completed: no node responded.\n", "can"
            )
            self._operation_log("error", "CAN device detection found no node.\n")
            self._render_demo_view()
            return
        node_id = CAN_DETECTION_NODE_IDS[index]
        try:
            self.can_transport.enumerate(node_id)
        except (serial.SerialException, OSError, RuntimeError) as exc:
            self.can_detection_active = False
            self.can_device_var.set("CAN ID: detection failed")
            self._append_log("error", f"CAN device detection failed: {exc}\n", "can")
            self._render_demo_view()
            return
        self.after(
            CAN_DETECTION_PROBE_MS,
            lambda: self._detect_can_node(generation, index + 1),
        )

    def _disconnect_can(self) -> None:
        activity_before_disconnect = self._motor_activity_active()
        self.can_command_refresher.stop()
        self.mit_command_refresher.stop()
        if hasattr(self, "can_parameter_panel"):
            self.can_parameter_panel.on_disconnect()
        if hasattr(self, "mit_bench_panel"):
            self.mit_bench_panel.on_stop_or_disconnect()
        self.demo_service.cancel()
        self.demo_speed_mode_selected = False
        self.demo_view.reset_continuous()
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
        self.can_enumeration_generation += 1
        self.can_enumeration_guidance_shown = False
        self.can_detection_active = False
        self.can_detection_generation += 1
        self.can_node_change_pending = None
        self.can_node_change_generation += 1
        self.can_device_var.set("CAN ID: not detected")
        self.can_active_report_enabled = False
        self.active_interface = None
        self.start_waiting = False
        self.motion_command_active = False
        self.pulse_active = False
        self.continuous_active = False
        self.stop_pending = False
        self._rt_health_fragment = ""
        self._enc_health_fragment = ""
        self._freq_pwm_hz = None
        self._freq_foc_hz = None
        self._freq_enc_rt_hz = None
        self._rt_last_t_ms = None
        self._rt_last_adc = None
        self.freq_var.set("PWM/FOC: 未知")
        self.connection_var.set(tr(self.language_var.get(), "not_connected"))
        self.mci_state = None
        self.state_var.set("MCI: 未知")
        self.can_feedback_var.set("CAN Type 2 反馈：尚未收到")
        self.board_temperature_c = None
        self.motor_temperature_c = None
        self._last_can_feedback = None
        self.rotor_alignment_valid = None
        self.mit_bench_panel.set_alignment_valid(None)
        self._render_temperature_text()
        self.command_refresh_count = 0
        self.command_refresh_var.set("CAN command refresh: idle")
        self._update_control_state()
        self._append_log("event", "CAN motion connection disconnected\n")
        self._operation_log("event", "CAN disconnected.\n")

    def _disconnect_debug(self) -> None:
        connection = self.serial_port
        if connection is None:
            return
        reader_thread = self.reader_thread
        self.reader_thread = None
        if self.streaming:
            try:
                self.send_command("wave off", quiet=True)
                time.sleep(0.05)
            except Exception:
                pass
        self.reader_stop.set()
        self.serial_port = None
        try:
            connection.close()
        except (serial.SerialException, OSError):
            pass
        if reader_thread is not None and reader_thread is not threading.current_thread():
            reader_thread.join(timeout=0.25)
        self.streaming = False
        self._wave_off_deadline = None
        self._close_wave_csv()
        self.waveform_store.reset()
        self._reset_wave_display_envelopes()
        self.wave_status_var.set("波形: 停止")
        self.rs485_state_var.set("RS485 MCI：调试端口未连接")
        self.torque_var.set("TORQUE: 未知")
        self.debug_connection_var.set(tr(self.language_var.get(), "not_connected"))
        self.debug_baud_combo.configure(state="normal")
        self.connect_button.configure(text=tr(self.language_var.get(), "connect"))
        self._update_control_state()
        self._append_log("event", "RS485 Debug disconnected\n")

    def disconnect(self) -> None:
        """Close both independent interfaces during shutdown or update install."""
        self._disconnect_can()
        self._disconnect_debug()

    def _reader_loop(self) -> None:
        pending = bytearray()
        while not self.reader_stop.is_set():
            connection = self.serial_port
            if connection is None:
                return
            try:
                chunk = connection.read(max(connection.in_waiting, 1))
            except (serial.SerialException, OSError) as exc:
                self._put_rs485_event(("error", str(exc)))
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
                    self._put_rs485_event(("line", line))
        if pending and not self.streaming:
            self._put_rs485_event(
                ("line", pending.decode("ascii", errors="replace"))
            )

    def _put_rs485_event(self, event: tuple[str, object]) -> None:
        """Never let debug text block or starve safety-relevant CAN events."""
        target = self.__dict__.get("rs485_rx_queue", self.rx_queue)
        try:
            target.put_nowait(event)
            return
        except queue.Full:
            pass
        try:
            target.get_nowait()
            self._rs485_queue_drop_count = (
                self.__dict__.get("_rs485_queue_drop_count", 0) + 1
            )
        except queue.Empty:
            pass
        target.put_nowait(event)

    def _extract_wave_frames(self, pending: bytearray) -> None:
        """Parse raw, envelope, and single-channel block frames."""
        decoder = self.__dict__.get("wave_decoder")
        if decoder is None:  # Supports lightweight parser unit tests.
            decoder = WaveFrameDecoder()
            self.wave_decoder = decoder
        events = decoder.extract(pending)
        wave_events = [event for event in events if event[0].startswith("wave")]
        for event in events:
            if not event[0].startswith("wave"):
                self._put_rs485_event(event)
        batch_queue = self.__dict__.get("wave_batch_queue")
        if batch_queue is None:  # Supports lightweight parser unit tests.
            for event in wave_events:
                self.rx_queue.put(event)
            return
        if not wave_events:
            return
        try:
            batch_queue.put_nowait(wave_events)
        except queue.Full:
            try:
                dropped_batch = batch_queue.get_nowait()
                self._wave_host_queue_drop_count += sum(
                    len(event[1][3]) if event[0] == "wave_single" else 1
                    for event in dropped_batch
                )
            except queue.Empty:
                pass
            batch_queue.put_nowait(wave_events)

    def _process_wave_event(self, kind: str, payload: object) -> None:
        if kind == "wave":
            self._on_wave_frame(payload)
        elif kind == "wave_stats":
            self._on_wave_stats_frame(payload)
        elif kind == "wave_single":
            self._on_wave_single_frame(payload)

    def _process_rx_queue(self) -> None:
        deadline = time.perf_counter() + RX_QUEUE_BUDGET_MS / 1000.0
        processed = 0
        try:
            while (
                processed < RX_QUEUE_MAX_ITEMS
                and time.perf_counter() < deadline
            ):
                try:
                    kind, payload = self.rx_queue.get_nowait()
                except queue.Empty:
                    kind, payload = self.rs485_rx_queue.get_nowait()
                processed += 1
                if kind == "line":
                    line = str(payload)
                    self._append_log("rx", f"RX  {line}\n", "rs485")
                    try:
                        self._active_log_channel = "rs485"
                        self._parse_status_line(line)
                    except Exception as exc:  # Keep the UI alive on parse bugs.
                        self._append_log("error", f"解析异常: {exc!r}\n", "rs485")
                    finally:
                        self._active_log_channel = "app"
                elif kind.startswith("wave"):
                    self._process_wave_event(kind, payload)
                elif kind == "can_tx":
                    self._append_log("tx", f"CAN TX {format_frame(payload)}\n", "can")
                elif kind == "can_frame":
                    self._handle_can_frame(payload)
                elif kind == "can_error":
                    self._append_log("error", f"USB-CAN error: {payload}\n", "can")
                    if self.connected and self.active_interface == "can":
                        self._disconnect_can()
                elif kind == "can_refresh_sent":
                    self.command_refresh_count = int(payload)
                    if self.pulse_active:
                        remaining_ms = max(
                            0, int((self.pulse_deadline - time.monotonic()) * 1000)
                        )
                        self.command_refresh_var.set(
                            f"CAN command refresh: timed, about {remaining_ms} ms remaining"
                        )
                    elif self.continuous_active:
                        self.command_refresh_var.set(
                            f"CAN command refresh: active (sent={self.command_refresh_count})"
                        )
                elif kind == "can_refresh_timeout":
                    self._append_log(
                        "error",
                        "CAN feedback timeout; Type 1 refresh stopped and Type 4 stop sent.\n",
                    )
                    self.stop_motor()
                elif kind == "can_refresh_motor_exit":
                    self._append_log(
                        "error",
                        "MCU left MOTOR mode; Type 1 refresh stopped and Type 4 stop sent.\n",
                    )
                    self.stop_motor()
                elif kind == "can_timed_deadline":
                    if self.pulse_active:
                        self._append_log("event", "Timed run deadline reached; Type 4 stop sent.\n")
                        self.stop_motor()
                elif kind == "can_refresh_error":
                    self._append_log("error", f"CAN command refresh failed: {payload}\n")
                    self.stop_motor()
                else:
                    self._append_log("error", f"串口错误: {payload}\n", "rs485")
                    if self.serial_port is not None:
                        self._disconnect_debug()
        except queue.Empty:
            pass
        try:
            while time.perf_counter() < deadline:
                batch = self.wave_batch_queue.get_nowait()
                for kind, payload in batch:
                    self._process_wave_event(kind, payload)
        except queue.Empty:
            pass
        try:
            self._poll_start_sequence()
        except Exception as exc:  # Keep the keepalive/sequence loop alive.
            self._append_log("error", f"轮询异常: {exc!r}\n", "app")
        next_interval = (
            RX_QUEUE_BUSY_INTERVAL_MS
            if (not self.rx_queue.empty() or not self.rs485_rx_queue.empty())
            else RX_QUEUE_INTERVAL_MS
        )
        self.after(next_interval, self._process_rx_queue)

    def _handle_can_frame(self, frame: CanFrame) -> None:
        if self.can_parameter_panel.handle_frame(frame):
            self._append_log("rx", f"CAN RX {format_frame(frame)}\n", "can")
            self.can_parameter_panel.refresh_state()
            return
        device = parse_device_id_response(frame)
        if device is not None:
            self._append_log("rx", f"CAN RX {format_frame(frame)}\n", "can")
            node_id, uid = device
            pending_change = self.can_node_change_pending
            if pending_change is not None and node_id != pending_change[1]:
                self._append_log(
                    "event",
                    f"Ignored stale CAN ID response from node {node_id} while "
                    f"verifying node {pending_change[1]}.\n",
                    "can",
                )
                return
            if pending_change is not None:
                self.can_node_change_pending = None
                self.can_node_change_generation += 1
                self._append_log(
                    "event",
                    f"CAN ID change verified: {pending_change[0]} -> {node_id}.\n",
                    "can",
                )
                self._operation_log(
                    "event", f"CAN ID verified: {pending_change[0]} -> {node_id}.\n"
                )
            first_detection = self.can_uid is None
            self.can_uid = uid
            if self.can_transport is not None:
                self.can_transport.node_id = node_id
            if hasattr(self, "can_parameter_panel"):
                self.can_parameter_panel.node_id = node_id
            self.can_device_var.set(f"CAN ID: {node_id} (0x{node_id:02X})")
            if self.can_detection_active:
                self.can_detection_active = False
                self.can_detection_generation += 1
                self._append_log(
                    "event", f"CAN device detected at node {node_id}.\n", "can"
                )
                self._operation_log(
                    "event", f"CAN device detected at node {node_id}.\n"
                )
            if first_detection:
                self.can_enumeration_generation += 1
                self._append_log("event", f"CAN node {node_id} UID=0x{uid:016X}\n", "can")
                transport = self.can_transport
                if transport is not None:
                    try:
                        # Type 1/3/4 already receive an immediate Type 2
                        # response.  Keep Type 24 periodic reporting off so
                        # each feedback frame observed during motion is a
                        # real command acknowledgement.  The firmware's
                        # 10 ms default report interval otherwise creates a
                        # continuous 100 Hz uplink that can starve downlink
                        # commands in some serial USB-CAN adapters.
                        transport.set_active_report(False)
                        self.can_active_report_enabled = False
                    except (serial.SerialException, OSError, RuntimeError) as exc:
                        self._append_log("error", f"USB-CAN report setup failed: {exc}\n", "can")
            if pending_change is not None and self.can_transport is not None:
                try:
                    self.can_transport.save_configuration()
                    self._append_log(
                        "event",
                        f"CAN ID {node_id} verified; Type 22 flash save requested.\n",
                        "can",
                    )
                    self._operation_log(
                        "event", f"CAN ID {node_id} flash save requested.\n"
                    )
                except (serial.SerialException, OSError, RuntimeError) as exc:
                    self._append_log(
                        "error",
                        f"CAN ID was changed but Type 22 flash save failed: {exc}\n",
                        "can",
                    )
                    messagebox.showerror(
                        "CAN ID",
                        "The new CAN ID responded, but the flash save request failed. "
                        "Use Save Configuration before power-off.",
                        parent=self,
                    )
            self._update_control_state()
            return
        feedback = parse_feedback(frame)
        if feedback is not None:
            # Type 2 has no sensor-valid bit. Firmware uses zero until the
            # first valid TEMP2 sample; keep the UI at "unknown" in that case.
            if feedback.temperature_c > 0.0 or self.motor_temperature_c is not None:
                self.motor_temperature_c = feedback.temperature_c
            self._last_can_feedback = feedback
            self.mit_bench_panel.update_feedback(feedback)
            self._render_temperature_text()
            now = time.monotonic()
            self.can_last_feedback_time = now
            if now - self.can_last_feedback_log_time >= 0.25:
                self.can_last_feedback_log_time = now
                self._append_log("rx", f"CAN RX {format_frame(frame)}\n", "can")
            if feedback.mode == MODE_RESET:
                self.mci_state = 0
                if (
                    not self.start_waiting
                    and self.can_active_report_enabled
                    and self.can_transport is not None
                ):
                    try:
                        self.can_transport.set_active_report(False)
                        self.can_active_report_enabled = False
                    except (serial.SerialException, OSError, RuntimeError) as exc:
                        self._append_log(
                            "error", f"USB-CAN report disable failed: {exc}\n", "can"
                        )
            elif feedback.mode == MODE_CALIBRATING:
                self.mci_state = 2
            elif feedback.mode == MODE_MOTOR:
                self.mci_state = 6
                if self.can_active_report_enabled and self.can_transport is not None:
                    try:
                        # Alignment needs unsolicited feedback to announce the
                        # transition to MOTOR. Once there, every Type 1 command
                        # returns Type 2, so remove the 100 Hz Type 24 stream.
                        self.can_transport.set_active_report(False)
                        self.can_active_report_enabled = False
                    except (serial.SerialException, OSError, RuntimeError) as exc:
                        self._append_log(
                            "error", f"USB-CAN report disable failed: {exc}\n", "can"
                        )
            else:
                self.mci_state = None
            self.state_var.set(
                f"CAN mode={feedback.mode} faults=0x{feedback.faults:02X} "
                f"pos={feedback.position_rad:.4f} rad vel={feedback.velocity_rad_s:.4f} rad/s"
            )
            self._render_can_feedback()
            if feedback.faults and self._motor_activity_active() and not self.stop_pending:
                self._append_log("error", "CAN feedback reported a fault; requesting stop.\n", "can")
                self.stop_motor()
            self._update_control_state()
            return
        self._append_log("rx", f"CAN RX {format_frame(frame)}\n", "can")
        fault = parse_fault_report(frame)
        if fault is not None:
            self._append_log(
                "error" if fault.fault else "event",
                f"CAN fault=0x{fault.fault:08X} warning=0x{fault.warning:08X}\n",
            )
            if fault.fault and self._motor_activity_active() and not self.stop_pending:
                self.stop_motor()

    def _parse_status_line(self, line: str) -> None:
        if self.telemetry_model.parse_line(line):
            self.cpu_load_var.set(self._format_cpu_load())
            self.encoder_models_var.set(self._format_encoder_models())

        match = re.search(r"(?:TORQUE_CMD.*|\bRT\b.*)\bmci=(\d+)", line)
        if match:
            previous_mci_state = self.mci_state
            self.mci_state = int(match.group(1))
            name = MCI_NAMES.get(self.mci_state, "其他")
            self.state_var.set(f"MCI: {self.mci_state} ({name})")
            self.rs485_state_var.set(f"RS485 MCI: {self.mci_state} ({name})")
            if self.mci_state == 0 and self.stop_pending:
                self.stop_pending = False
                self._restore_mit_mode_after_demo()
                self._append_log("event", "已由 MCI 状态确认电机停止。\n")
            if (
                self.mci_state == 0
                and previous_mci_state not in (None, 0)
                and (self.pulse_active or self.continuous_active)
            ):
                self.pulse_active = False
                self.continuous_active = False
                self.motion_command_active = False
                self._restore_mit_mode_after_demo()
                self.demo_service.cancel()
                self.command_refresh_var.set("CAN command refresh: idle")
                self._append_log(
                    "error",
                    "MCU 已主动退出 RUN，CAN 命令刷新已停止。\n",
                )
            self._update_control_state()

        if "CMD start rejected" in line:
            self.start_waiting = False
            self.demo_service.cancel()
            self._append_log("error", "启动被 MCU 拒绝，请查询故障状态。\n")
        if "CMD iq rejected" in line:
            self.motion_command_active = False
            self.pulse_active = False
            self.command_refresh_var.set("CAN command refresh: idle")
        if "CMD speed rejected" in line:
            self.motion_command_active = False
            self.pulse_active = False
            self.command_refresh_var.set("CAN command refresh: idle")
        if "CMD stop accepted" in line:
            self.stop_pending = False
            self.motion_command_active = False
            self.pulse_active = False
            self.command_refresh_var.set("CAN command refresh: idle")

        pwm_test = PWM_TEST_RE.search(line)
        if pwm_test:
            self._freq_pwm_hz = int(pwm_test.group(3))
            self._update_freq_label()

        encoder_rt = ENCODER_RT_RE.search(line)
        if encoder_rt:
            self._freq_enc_rt_hz = int(encoder_rt.group(1))
            self._freq_foc_hz = int(encoder_rt.group(2))
            self._update_freq_label()

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
            foc_rate_hz = self._freq_foc_hz or 50_000
            self._legacy_foc_peak_percent = min(
                100.0, focmax * foc_rate_hz * 100.0 / 250_000_000.0
            )
            if self.telemetry_model.cpu_load is None:
                self.cpu_load_var.set(self._format_cpu_load())
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
            self.telemetry_model.note_as5047p_health()
            if not self.telemetry_model.encoders:
                self.encoder_models_var.set(self._format_encoder_models())
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
        normalized = command.strip().lower()
        diagnostic_command = normalized == "status" or normalized.startswith("wave ")
        if not diagnostic_command:
            if not quiet:
                self._append_log(
                    "error",
                    f"RS485 debug is read-only; command '{command}' was blocked. Use CAN for motor control.\n",
                )
            return False
        if self.serial_port is None:
            if not quiet:
                english = self.language_var.get() == "en"
                messagebox.showwarning(
                    "Not connected" if english else "未连接",
                    (
                        "Connect the RS485 Debug port first. The CAN motion "
                        "connection can remain online."
                        if english
                        else "请先连接 RS485 调试端口。CAN 运动连接可以保持在线。"
                    ),
                )
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
            self._append_log("error", f"发送失败: {exc}\n", "rs485")
            return False
        if not quiet:
            self._append_log("tx", f"TX  {command.strip()}\n", "rs485")
        return True

    def toggle_wave_stream(self) -> None:
        """Start/stop the firmware ADC phase-current waveform stream."""
        if self.serial_port is None:
            english = self.language_var.get() == "en"
            messagebox.showwarning(
                "Not connected" if english else "未连接",
                (
                    "Connect the RS485 Debug port first. The CAN motion "
                    "connection can remain online."
                    if english
                    else "请先连接 RS485 调试端口。CAN 运动连接可以保持在线。"
                ),
            )
            return
        if not self.streaming:
            dec = max(1, min(100, self.wave_dec_var.get()))
            self.wave_dec_var.set(dec)
            use_stats = self.wave_stats_var.get()
            use_single = self.wave_single_var.get()
            single_channel = self.wave_single_channel_var.get().strip().lower()
            if single_channel not in {"u", "v", "w"}:
                single_channel = "u"
                self.wave_single_channel_var.set("U")
            if use_single:
                for channel, variable in (
                    ("u", self.wave_u_var),
                    ("v", self.wave_v_var),
                    ("w", self.wave_w_var),
                ):
                    variable.set(channel == single_channel)
                command = f"wave single on {single_channel}"
                self._wave_sample_rate_hz = 50_000.0
            elif use_stats:
                command = "wave stats on 100"
                self._wave_sample_rate_hz = 500.0
            else:
                command = f"wave on {dec}"
                self._wave_sample_rate_hz = 50_000.0 / dec
            if self.send_command(command):
                self.streaming = True
                self._wave_off_deadline = None
                self.waveform_store.reset()
                self.wave_decoder = WaveFrameDecoder()
                self._wave_host_queue_drop_count = 0
                while not self.wave_batch_queue.empty():
                    try:
                        self.wave_batch_queue.get_nowait()
                    except queue.Empty:
                        break
                self._reset_wave_display_envelopes()
                self._open_wave_csv()
                if use_single:
                    self.wave_status_var.set(
                        f"波形: 启动中 (单路全采样 {single_channel.upper()}, 50 ksample/s)"
                    )
                elif use_stats:
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

    def _reset_wave_display_envelopes(self) -> None:
        """Reset the bounded, incrementally built display-only envelopes."""
        self.waveform_store.configure_display(
            self._wave_sample_rate_hz, WAVE_DISPLAY_ENVELOPE_HZ
        )
        self._sync_waveform_store()

    def _sync_waveform_store(self) -> None:
        """Expose stable aliases while waveform state lives in its controller."""
        store = self.waveform_store
        self._wave_buffers = store.raw
        self._wave_stats_entries = store.stats
        self._wave_display_entries = store.display
        self._wave_display_accumulators = store.display_accumulators
        self._wave_display_bucket_samples = store.display_bucket_samples
        self._wave_last_seq = store.last_sequence
        self._wave_single_last_dropped = store.last_firmware_dropped
        self._wave_frame_count = store.frame_count
        self._wave_lost_count = store.lost_count
        self._wave_transport_lost_count = store.transport_lost_count
        self._wave_firmware_drop_count = store.firmware_drop_count
        self._wave_glitch_count = store.glitch_count
        self._wave_prev_raw = store.previous_raw

    def _waveform_store_for_use(self) -> WaveformStore:
        """Return the controller, including for GUI-free unit-test instances."""
        store = self.__dict__.get("waveform_store")
        if store is None:
            store = WaveformStore(
                raw_capacity=WAVE_BUFFER_MAX,
                display_capacity=5 * WAVE_DISPLAY_ENVELOPE_HZ,
                glitch_threshold=WAVE_GLITCH_LSB,
            )
            store.display_bucket_samples = self.__dict__.get(
                "_wave_display_bucket_samples", 1
            )
            self.waveform_store = store
            self._sync_waveform_store()
        return store

    def _append_wave_display_sample(
        self, channel: str, seq: int, value: int
    ) -> None:
        """Accumulate raw samples into display min/max buckets."""
        self._waveform_store_for_use().append_display(channel, seq, value)
        self._sync_waveform_store()

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
        self._waveform_store_for_use().ingest_three_phase(frame)
        self._sync_waveform_store()
        if self._wave_csv_file is not None:
            self._wave_csv_rows.append(f"{seq},{iu},{iv},{iw}\n")
            if len(self._wave_csv_rows) >= 500:
                self._flush_wave_csv()

    def _on_wave_stats_frame(
        self, frame: tuple[int, tuple[int, int, int, int, int, int]]
    ) -> None:
        self._waveform_store_for_use().ingest_stats(frame)
        self._sync_waveform_store()

    def _on_wave_single_frame(
        self, frame: tuple[int, int, int, tuple[int, ...]]
    ) -> None:
        seq_start, channel_index, dropped, samples = frame
        self._waveform_store_for_use().ingest_single(frame)
        self._sync_waveform_store()
        if self._wave_csv_file is not None:
            for offset, value in enumerate(samples):
                seq = (seq_start + offset) & 0xFFFF
                fields = ["", "", ""]
                fields[channel_index] = str(value)
                self._wave_csv_rows.append(
                    f"{seq},{fields[0]},{fields[1]},{fields[2]}\n"
                )
        if len(self._wave_csv_rows) >= 500:
            self._flush_wave_csv()

    def _select_wave_envelope(self) -> None:
        if self.wave_stats_var.get():
            self.wave_single_var.set(False)

    def _select_wave_single(self) -> None:
        if self.wave_single_var.get():
            self.wave_stats_var.set(False)

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
        ttk.Label(tools, text="时间窗").grid(
            row=0, column=5, padx=(4, 4)
        )
        self.wave_time_window_combo = ttk.Combobox(
            tools,
            textvariable=self.wave_time_window_var,
            values=WAVE_TIME_WINDOWS,
            width=7,
            state="readonly",
        )
        self.wave_time_window_combo.grid(row=0, column=6, padx=(0, 8))
        ttk.Checkbutton(tools, text="U", variable=self.wave_u_var).grid(
            row=0, column=7, padx=2
        )
        ttk.Checkbutton(tools, text="V", variable=self.wave_v_var).grid(
            row=0, column=8, padx=2
        )
        ttk.Checkbutton(tools, text="W", variable=self.wave_w_var).grid(
            row=0, column=9, padx=2
        )
        ttk.Label(tools, textvariable=self.wave_status_var).grid(
            row=0, column=10, padx=(12, 0), sticky="w"
        )
        ttk.Checkbutton(
            tools, text="自动保存CSV", variable=self.wave_save_var
        ).grid(row=1, column=5, padx=(8, 4), pady=(4, 0))
        ttk.Checkbutton(
            tools,
            text="包络模式(减带宽)",
            variable=self.wave_stats_var,
            command=self._select_wave_envelope,
        ).grid(row=1, column=2, padx=(8, 4), pady=(4, 0))
        ttk.Checkbutton(
            tools,
            text="单路全采样",
            variable=self.wave_single_var,
            command=self._select_wave_single,
        ).grid(row=1, column=3, padx=(8, 4), pady=(4, 0))
        self.wave_single_channel_combo = ttk.Combobox(
            tools,
            textvariable=self.wave_single_channel_var,
            values=("U", "V", "W"),
            width=3,
            state="readonly",
        )
        self.wave_single_channel_combo.grid(
            row=1, column=4, padx=(0, 8), pady=(4, 0)
        )
        ttk.Button(
            tools, text="保存波形", command=self.save_wave_snapshot
        ).grid(row=1, column=0, padx=(0, 8), pady=(4, 0))
        ttk.Button(
            tools, text="导出日志", command=self.export_log
        ).grid(row=1, column=1, padx=(0, 8), pady=(4, 0))
        tools.columnconfigure(11, weight=1)
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
            with open(path, "w", encoding="ascii", newline="") as handle:
                handle.write("seq,iu,iv,iw\n")
                if self.wave_single_var.get():
                    channel_index = {"U": 0, "V": 1, "W": 2}.get(
                        self.wave_single_channel_var.get(), 0
                    )
                    for seq, value in buffers[channel_index]:
                        fields = ["", "", ""]
                        fields[channel_index] = str(value)
                        handle.write(f"{seq},{fields[0]},{fields[1]},{fields[2]}\n")
                    count = len(buffers[channel_index])
                else:
                    count = min((len(buf) for buf in buffers), default=0)
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
            envelope_series: dict[str, list[tuple[int, int, int]]] = {}
            values: list[int] = []
            time_window_s = parse_wave_time_window(self.wave_time_window_var.get())
            display_count = max(2, int(self._wave_sample_rate_hz * time_window_s))
            use_display_envelope = (
                time_window_s > 0.2 and not self.wave_stats_var.get()
            )
            if use_display_envelope:
                envelope_count = max(
                    2, int(time_window_s * WAVE_DISPLAY_ENVELOPE_HZ)
                )
                for channel in ("u", "v", "w"):
                    entries = self._wave_display_entries[channel]
                    if show[channel] and entries:
                        start = max(0, len(entries) - envelope_count)
                        visible_entries = list(islice(entries, start, None))
                        envelope_series[channel] = visible_entries
                        for _, low, high in visible_entries:
                            values.extend((low, high))
            else:
                for channel in ("u", "v", "w"):
                    buf = self._wave_buffers[channel]
                    if show[channel] and buf:
                        start = max(0, len(buf) - display_count)
                        visible = list(islice(buf, start, None))
                        series[channel] = visible
                        values.extend(value for _, value in visible)
            stats_start = max(0, len(self._wave_stats_entries) - display_count)
            stats_entries = list(islice(self._wave_stats_entries, stats_start, None))
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
                    6, height - 26, anchor="sw", text=f"{y_min:.0f}",
                    fill=WAVE_LABEL, tags="wave",
                )
                canvas.create_text(
                    6,
                    height - 8,
                    anchor="sw",
                    text=f"-{time_window_s:g} s",
                    fill=WAVE_LABEL,
                    tags="wave",
                )
                canvas.create_text(
                    width - 6,
                    height - 8,
                    anchor="se",
                    text="0 s",
                    fill=WAVE_LABEL,
                    tags="wave",
                )
                for channel, points in series.items():
                    source_count = len(points)
                    compressed = source_count > int(width) * 2
                    points = compress_wave_points(points, max(1, int(width)))
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
                        if (
                            not compressed
                            and index > 0
                            and ((seq - prev_seq) & 0xFFFF) != 1
                        ):
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
                for channel, entries in envelope_series.items():
                    entries = compress_envelope_entries(
                        entries, max(1, int(width))
                    )
                    count = len(entries)
                    if count < 2:
                        continue
                    envelope_coords: list[float] = []
                    for index, (_, min_value, max_value) in enumerate(entries):
                        x = index / (count - 1) * width
                        y1 = (
                            height
                            - (min_value - y_min) / (y_max - y_min) * height
                        )
                        y2 = (
                            height
                            - (max_value - y_min) / (y_max - y_min) * height
                        )
                        # Keep both extrema in one polyline so this reads as
                        # one waveform band, not two independent channels.
                        envelope_coords.extend((x, y1, x, y2))
                    canvas.create_line(
                        envelope_coords,
                        fill=colors[channel],
                        width=1,
                        tags="wave",
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
                    tr(
                        self.language_var.get(),
                        "wave_running_stats",
                        frames=self._wave_frame_count,
                        uart_lost=self._wave_transport_lost_count,
                        firmware_dropped=self._wave_firmware_drop_count,
                        host_dropped=self._wave_host_queue_drop_count,
                        decode_errors=self.wave_decoder.checksum_errors,
                        threshold=WAVE_GLITCH_LSB,
                        glitches=self._wave_glitch_count,
                    )
                )
        except Exception as exc:  # Keep the redraw loop alive on UI hiccups.
            self._append_log("error", f"波形绘制异常: {exc!r}\n")
        self.after(WAVE_REDRAW_INTERVAL_MS, self._wave_redraw)

    def start_motor(self) -> bool:
        if self.can_transport is None:
            return False
        try:
            # Type 3 initially reports CALI. Temporarily enable Type 24 so
            # the later transition into MOTOR is visible without RS485.
            self.can_transport.set_active_report(True)
            self.can_active_report_enabled = True
            self.can_transport.enable()
        except (serial.SerialException, OSError, RuntimeError) as exc:
            self.can_active_report_enabled = False
            try:
                self.can_transport.set_active_report(False)
            except (serial.SerialException, OSError, RuntimeError):
                pass
            self._append_log("error", f"CAN enable failed: {exc}\n")
            self._operation_log("error", f"Enable failed: {exc}\n")
            return False
        self._append_log("event", "CAN Type 3 enable sent; waiting for Type 2 MOTOR feedback.\n")
        self._operation_log("event", "Enable requested.\n")
        self.start_waiting = True
        start_time = time.monotonic()
        self.start_enable_attempts = 1
        self.start_enable_retry_at = (
            start_time + START_ENABLE_RETRY_INTERVAL_MS / 1000.0
        )
        # Ignore an old idle Type 2 frame when evaluating the new enable
        # session. The first feedback timeout now starts at this Type 3 send.
        self.can_last_feedback_time = start_time
        self.start_deadline = start_time + START_TIMEOUT_MS / 1000.0
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
            self._operation_log("error", "Enable feedback timeout; stopping.\n")
            self.stop_motor()
            return
        if self.mci_state == 6:
            self.start_waiting = False
            self.start_enable_attempts = 0
            self._append_log("event", "MCU 已进入 RUN，可以施加 Iq/速度。\n")
            self._operation_log("event", "Entered RUN.\n")
            demo_action = self.demo_service.motor_ready()
            if demo_action is not None:
                self._execute_demo_action(demo_action)
            self._update_control_state()
            return
        if (
            self.active_interface == "can"
            and self.mci_state == 0
            and self.start_enable_attempts < START_ENABLE_MAX_ATTEMPTS
            and time.monotonic() >= self.start_enable_retry_at
            and self.can_transport is not None
        ):
            try:
                self.can_transport.enable()
                self.start_enable_attempts += 1
                self.start_enable_retry_at = (
                    time.monotonic() + START_ENABLE_RETRY_INTERVAL_MS / 1000.0
                )
                self._append_log(
                    "event",
                    f"MCU remained RESET/IDLE; Type 3 enable retry "
                    f"{self.start_enable_attempts}/{START_ENABLE_MAX_ATTEMPTS}.\n",
                )
                self._operation_log(
                    "event",
                    f"Enable retry {self.start_enable_attempts - 1}.\n",
                )
            except (serial.SerialException, OSError, RuntimeError) as exc:
                self._append_log("error", f"CAN enable retry failed: {exc}\n")
            return
        if time.monotonic() >= self.start_deadline:
            self.start_waiting = False
            self.start_enable_attempts = 0
            self.demo_service.cancel()
            self._append_log("error", "等待 RUN 超时，请检查状态和故障。\n")
            self._operation_log("error", "Enable timed out waiting for RUN.\n")
            self.stop_motor()
            return

    def send_speed(self, value: int) -> bool:
        if self.can_transport is None:
            return False
        try:
            self.can_transport.command_velocity(value)
            self.can_command_rpm = value
        except (ValueError, serial.SerialException, OSError, RuntimeError) as exc:
            self._append_log("error", f"CAN velocity command failed: {exc}\n")
            return False
        self.motion_command_active = value != 0
        if value == 0:
            self.can_command_refresher.stop()
            self.continuous_active = False
            self.command_refresh_var.set("CAN command refresh: idle")
        else:
            self.command_refresh_var.set("CAN command refresh: active")
        return True

    def start_continuous_speed(
        self,
        direction: int,
        value: int,
    ) -> None:
        """Run at a safe demo speed until the user presses stop."""
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
        self.command_refresh_count = 0
        assert self.can_transport is not None
        self.can_command_refresher.start(self.can_transport, command_rpm)
        self.command_refresh_var.set(
            f"CAN command refresh: continuous {command_rpm} motor rpm"
        )
        self._append_log(
            "event",
            f"持续速度运行开始: {command_rpm} motor rpm，0x700A 每 250 ms 刷新；"
            "按“停止”结束。\n",
        )

    def start_timed_speed(
        self,
        direction: int,
        value: int,
        duration_ms: int,
    ) -> None:
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
        self.command_refresh_count = 0
        assert self.can_transport is not None
        self.can_command_refresher.start(
            self.can_transport,
            command_rpm,
            deadline=self.pulse_deadline,
        )
        self.command_refresh_var.set(
            f"CAN command refresh: timed {command_rpm} motor rpm, {duration_ms} ms"
        )
        self._append_log(
            "event",
            f"定时速度测试开始: {command_rpm} motor rpm，{duration_ms} ms。\n",
        )
        self.after(20, self._pulse_watchdog_tick)

    def _pulse_watchdog_tick(self) -> None:
        if not self.pulse_active:
            return
        if (not self.connected) or (time.monotonic() >= self.pulse_deadline):
            self._append_log("event", "定时测试结束，发送 stop。\n")
            self.stop_motor()
            return
        self.after(20, self._pulse_watchdog_tick)

    def _mit_set_zero(self) -> None:
        transport = self.can_transport
        if transport is None or not self._parameter_connection_ready():
            raise RuntimeError("CAN is not connected and enumerated")
        if self.mci_state != 0 or self._motor_activity_active():
            raise RuntimeError("motor must be IDLE before setting the joint zero")
        transport.set_zero()
        self._append_log(
            "event",
            "RS04 Type 6 sent; waiting for zero-position Type 2 feedback.\n",
            "can",
        )
        self._operation_log("event", "Zero requested.\n")

    def _mit_send_once(self, command: MitCommand) -> None:
        transport = self.can_transport
        if transport is None or self.mci_state != 6:
            raise RuntimeError("enable the motor and wait for MOTOR feedback first")
        transport.command_mit(command)
        self.motion_command_active = True
        self._append_log("tx", f"MIT command once: {command}\n", "can")
        self._operation_log("tx", f"MIT single command: {command}\n")
        # A single frame is intentionally a bounded bench pulse, not a hidden
        # hold mode.  If the caller does not immediately start the 100 Hz
        # refresher, stop before the firmware watchdog has to intervene.
        self.after(200, self._mit_finish_single_command)

    def _mit_finish_single_command(self) -> None:
        if self.motion_command_active and not self.continuous_active:
            self.stop_motor()

    def _mit_start_hold(self, command: MitCommand) -> None:
        self._mit_send_once(command)
        assert self.can_transport is not None
        self.can_command_refresher.stop()
        self.mit_command_refresher.start_mit(self.can_transport, command)
        self.continuous_active = True
        self.command_refresh_count = 0
        self.command_refresh_var.set("MIT Type 1 refresh: 100 Hz")
        self._append_log(
            "event",
            "MIT hold started at 100 Hz; firmware watchdog is 250 ms.\n",
            "can",
        )
        self._operation_log("event", f"MIT hold started: {command}\n")

    def set_detected_can_id(self) -> None:
        transport = self.can_transport
        if transport is None or not self._parameter_connection_ready():
            messagebox.showwarning("CAN ID", "Detect a CAN device first.", parent=self)
            return
        if self.mci_state != 0 or self._motor_activity_active():
            messagebox.showwarning(
                "CAN ID", "The motor must be IDLE before changing its CAN ID.", parent=self
            )
            return
        try:
            node_id = int(self.can_new_node_id_var.get())
        except (TypeError, ValueError, tk.TclError):
            messagebox.showerror("CAN ID", "Enter a CAN ID from 1 to 127.", parent=self)
            return
        if not 1 <= node_id <= 127:
            messagebox.showerror("CAN ID", "Enter a CAN ID from 1 to 127.", parent=self)
            return
        old_id = transport.node_id
        if node_id == old_id:
            messagebox.showwarning(
                "CAN ID", "The new CAN ID is the same as the detected ID.", parent=self
            )
            return
        if not messagebox.askyesno(
            "CAN ID",
            f"Change CAN ID {old_id} (0x{old_id:02X}) to "
            f"{node_id} (0x{node_id:02X})?",
            parent=self,
        ):
            return
        self.can_enumeration_generation += 1
        self.can_detection_active = False
        self.can_detection_generation += 1
        self.can_node_change_generation += 1
        generation = self.can_node_change_generation
        self.can_node_change_pending = (old_id, node_id)
        old_uid = self.can_uid
        self.can_uid = None
        self.mci_state = None
        self.can_device_var.set(
            f"CAN ID: verifying {old_id} -> {node_id}..."
        )
        try:
            transport.set_node_id(node_id)
        except (ValueError, serial.SerialException, OSError, RuntimeError) as exc:
            self.can_node_change_pending = None
            self.can_node_change_generation += 1
            self.can_uid = old_uid
            self.mci_state = 0
            self.can_device_var.set(f"CAN ID: {old_id} (0x{old_id:02X})")
            messagebox.showerror("CAN ID", str(exc), parent=self)
            self._render_demo_view()
            return
        self._append_log(
            "event",
            f"RS04 Type 7 requested CAN ID {old_id} -> {node_id}; "
            "waiting for a Type 0 response from the new ID.\n",
            "can",
        )
        self._operation_log("event", f"Set CAN ID requested: {old_id} -> {node_id}.\n")
        self._render_demo_view()
        self.after(
            100,
            lambda: self._probe_changed_can_id(generation, node_id),
        )
        self.after(1_000, lambda: self._verify_changed_can_id(generation))

    def _probe_changed_can_id(self, generation: int, node_id: int) -> None:
        if (
            generation != self.can_node_change_generation
            or self.can_node_change_pending is None
            or self.can_transport is None
        ):
            return
        try:
            self.can_transport.enumerate(node_id)
        except (serial.SerialException, OSError, RuntimeError) as exc:
            self._append_log("error", f"CAN ID verification probe failed: {exc}\n", "can")

    def _verify_changed_can_id(self, generation: int) -> None:
        if (
            generation != self.can_node_change_generation
            or self.can_node_change_pending is None
        ):
            return
        old_id, new_id = self.can_node_change_pending
        self.can_node_change_pending = None
        self.can_node_change_generation += 1
        self.can_device_var.set("CAN ID: verification failed; detect again")
        self._append_log(
            "error",
            f"CAN ID {old_id} -> {new_id} was not verified. Detect the device again "
            "before sending any command.\n",
            "can",
        )
        self._operation_log("error", "CAN ID change verification failed.\n")
        self._render_demo_view()

    def stop_motor(self) -> None:
        self.can_command_refresher.stop()
        self.mit_command_refresher.stop()
        self.demo_service.cancel()
        self.demo_view.reset_continuous()
        self.pulse_active = False
        self.continuous_active = False
        self.motion_command_active = False
        self.start_waiting = False
        self.start_enable_attempts = 0
        self.command_refresh_var.set("CAN command refresh: idle")
        if hasattr(self, "mit_bench_panel"):
            self.mit_bench_panel.on_stop_or_disconnect()
        self.stop_pending = True
        self.stop_attempts = 0
        self.can_stop_not_before = time.monotonic() + 0.3
        self._operation_log("event", "Stop requested.\n")
        self._send_stop_attempt()

    def _send_stop_attempt(self) -> None:
        if not self.stop_pending or not self.connected:
            return
        self.stop_attempts += 1
        if self.can_transport is not None:
            try:
                self.can_transport.stop()
                self.can_command_rpm = 0
            except (serial.SerialException, OSError, RuntimeError) as exc:
                self._append_log("error", f"CAN stop failed: {exc}\n")
                self._operation_log("error", f"Stop send failed: {exc}\n")
        self.after(STOP_RETRY_INTERVAL_MS, self._stop_retry_tick)

    def _stop_retry_tick(self) -> None:
        if not self.stop_pending:
            return
        if self.mci_state == 0 and time.monotonic() >= self.can_stop_not_before:
            self.stop_pending = False
            self._restore_mit_mode_after_demo()
            self._append_log("event", "已确认 MCI 进入 IDLE。\n")
            self._operation_log("event", "Stop confirmed; IDLE.\n")
            return
        if self.stop_attempts < STOP_MAX_ATTEMPTS:
            self._append_log(
                "event", f"未收到 Stop 确认，第{self.stop_attempts + 1}次发送。\n"
            )
            self._operation_log("event", f"Stop retry {self.stop_attempts}.\n")
            self._send_stop_attempt()
            return
        self.stop_pending = False
        self._append_log(
            "error", "CAN Stop 未获得确认；停止命令刷新，等待 MCU 看门狗停机。\n"
        )
        self._operation_log("error", "Stop not confirmed.\n")

    def _update_control_state(self) -> None:
        self._render_demo_view()

    def clear_log(self) -> None:
        self._log_entries.clear()
        for widget in self.log_widgets.values():
            widget.configure(state=tk.NORMAL)
            widget.delete("1.0", tk.END)
            widget.configure(state=tk.DISABLED)
        if self.log_popup_text is not None:
            self.log_popup_text.configure(state=tk.NORMAL)
            self.log_popup_text.delete("1.0", tk.END)
            self.log_popup_text.configure(state=tk.DISABLED)

    def _append_log(self, tag: str, text: str, channel: str | None = None) -> None:
        channel = channel or self._active_log_channel
        if channel not in {"can", "rs485", "app", "operation"}:
            channel = "app"
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        self._log_entries.append((channel, tag, timestamp, text))
        line = f"[{timestamp}] [{channel.upper()}] {self._ui(text)}"
        self._append_log_widget(self.log_widgets["all"], tag, line)
        self._append_log_widget(self.log_widgets[channel], tag, line)
        if self.log_popup_text is not None:
            try:
                self._append_log_widget(self.log_popup_text, tag, line)
            except tk.TclError:
                self.log_popup_text = None
                self.log_popup = None

    def _operation_log(self, tag: str, text: str) -> None:
        self._append_log(tag, text, "operation")

    def _rerender_logs(self) -> None:
        for channel, widget in self.log_widgets.items():
            try:
                widget.configure(state=tk.NORMAL)
                widget.delete("1.0", tk.END)
                for source_channel, tag, timestamp, source in self._log_entries:
                    if channel != "all" and channel != source_channel:
                        continue
                    widget.insert(
                        tk.END,
                        f"[{timestamp}] [{source_channel.upper()}] {self._ui(source)}",
                        tag,
                    )
                widget.see(tk.END)
                widget.configure(state=tk.DISABLED)
            except tk.TclError:
                pass
        if self.log_popup_text is not None:
            try:
                self.log_popup_text.configure(state=tk.NORMAL)
                self.log_popup_text.delete("1.0", tk.END)
                for channel, tag, timestamp, source in self._log_entries:
                    self.log_popup_text.insert(
                        tk.END,
                        f"[{timestamp}] [{channel.upper()}] {self._ui(source)}",
                        tag,
                    )
                self.log_popup_text.see(tk.END)
                self.log_popup_text.configure(state=tk.DISABLED)
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

    def _parameter_connection_ready(self) -> bool:
        return bool(
            self.connected
            and self.active_interface == "can"
            and self.can_transport is not None
            and self.can_uid is not None
        )

    def _parameter_operation_idle(self) -> bool:
        return bool(
            self.mci_state == 0
            and not self._motor_activity_active()
            and not self.stop_pending
        )

    def _send_parameter_frame(self, frame: CanFrame) -> None:
        transport = self.can_transport
        if not self._parameter_connection_ready() or transport is None:
            raise RuntimeError("CAN Control is not connected and enumerated")
        if not self._parameter_operation_idle():
            raise RuntimeError("motor must remain IDLE for parameter operations")
        transport.send(frame)

    def _read_rotor_alignment(self) -> None:
        self.can_parameter_panel.read_parameter_index(0x7033)

    def _save_rotor_alignment(self) -> None:
        transport = self.can_transport
        if not self._parameter_connection_ready() or transport is None:
            raise RuntimeError("CAN is not connected and enumerated")
        if not self._parameter_operation_idle():
            raise RuntimeError("motor must be IDLE before saving rotor alignment")
        if self.rotor_alignment_valid is not True:
            raise RuntimeError(
                "Read 0x7033 first and confirm rotor_alignment_valid = 1"
            )
        if not messagebox.askyesno(
            "保存转子对齐",
            "确认发送 Type 22，将当前有效的转子对齐结果写入 Flash？",
            parent=self,
        ):
            return
        transport.save_configuration()
        self._append_log(
            "event", "Rotor alignment Type 22 flash save requested.\n", "can"
        )
        self._operation_log("event", "Rotor alignment save requested.\n")

    def _on_parameter_value(self, index: int, value: int | float) -> None:
        if index == 0x3005:
            self.board_temperature_c = float(value) / 10.0
        elif index == 0x3006:
            self.motor_temperature_c = float(value) / 10.0
        elif index == 0x7032:
            self.mit_bench_panel.set_calibrated(bool(value))
            return
        elif index == 0x7033:
            self.rotor_alignment_valid = bool(value)
            self.mit_bench_panel.set_alignment_valid(bool(value))
            return
        elif index == 0x701A:
            self.mit_bench_panel.set_measured_iq(float(value))
            return
        else:
            return
        self._render_temperature_text()
        self._render_can_feedback()

    def _render_temperature_text(self) -> None:
        language = self.language_var.get()

        def shown(value: float | None) -> str:
            if value is None:
                return tr(language, "temperature_unknown")
            return f"~{value: .1f} °C"

        self.temperature_var.set(
            tr(
                language,
                "temperature_summary",
                board=shown(self.board_temperature_c),
                motor=shown(self.motor_temperature_c),
            )
        )

    def _render_can_feedback(self) -> None:
        feedback = self._last_can_feedback
        if feedback is None:
            return

        self.can_feedback_var.set(
            f"CAN Type 2: mode={feedback.mode} faults=0x{feedback.faults:02X} "
            f"pos={feedback.position_rad: .4f} rad vel={feedback.velocity_rad_s: .4f} rad/s "
            f"torque={feedback.torque_nm: .4f} Nm"
        )

    def _temperature_poll_tick(self) -> None:
        try:
            transport = self.can_transport
            if (
                self.connected
                and self.active_interface == "can"
                and transport is not None
                and self.can_uid is not None
                and not self.can_parameter_panel.long_run.running
                and not self.start_waiting
                and not self.stop_pending
            ):
                indices = (0x3005, 0x3006, 0x7032, 0x7033, 0x701A)
                index = indices[self._temperature_poll_index % len(indices)]
                self._temperature_poll_index += 1
                transport.send(build_parameter_read(index))
        except (OSError, RuntimeError, ValueError, serial.SerialException) as exc:
            self._append_log("error", f"CAN temperature read failed: {exc}\n", "can")
        finally:
            self.after(1000, self._temperature_poll_tick)

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
        language = self.language_var.get()
        if self.connected or self.serial_port is not None or self._motor_activity_active():
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
            launch_update_helper(
                path,
                Path(sys.executable),
                release.manifest.asset_name,
            )
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
        self.disconnect()
        self.can_command_refresher.close()
        self.mit_command_refresher.close()
        self.quit()
        self.destroy()


if __name__ == "__main__":
    if apply_update_from_argv():
        raise SystemExit(0)
    health_marker = health_marker_from_argv()
    app = EasyMotorApp()
    if health_marker is not None:
        app.after(500, acknowledge_healthy_start, health_marker)
    app.mainloop()
