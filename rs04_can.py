"""RobStride RS04 private CAN protocol and official USB-CAN AT framing.

Only the non-motion messages needed by the current bench stage are exposed:
device enumeration (type 0), single-parameter read (type 17), and the small
firmware-approved write subset (type 18).  Motion, enable, zeroing, and CAN-ID
change builders intentionally do not live in this module.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct
from typing import Final


CAN_ID_MASK: Final = 0x1FFFFFFF
AT_HEADER: Final = b"AT"
AT_TAIL: Final = b"\r\n"
AT_EXTENDED_FLAG: Final = 0x04


@dataclass(frozen=True)
class CanFrame:
    arbitration_id: int
    data: bytes

    def __post_init__(self) -> None:
        if not 0 <= self.arbitration_id <= CAN_ID_MASK:
            raise ValueError("extended CAN ID must fit in 29 bits")
        if len(self.data) > 8:
            raise ValueError("classic CAN payload cannot exceed 8 bytes")


@dataclass(frozen=True)
class Parameter:
    index: int
    name: str
    kind: str
    writable: bool = False
    minimum: int | float | None = None
    maximum: int | float | None = None
    allowed_values: tuple[int | float, ...] = ()


PARAMETERS: Final = (
    Parameter(0x7005, "run_mode", "uint8", True, allowed_values=(0,)),
    Parameter(0x7006, "iq_ref", "float"),
    Parameter(0x700A, "spd_ref", "float"),
    Parameter(0x700B, "limit_torque", "float"),
    Parameter(0x7010, "cur_kp", "float"),
    Parameter(0x7011, "cur_ki", "float"),
    Parameter(0x7014, "cur_filt_gain", "float"),
    Parameter(0x7016, "loc_ref", "float"),
    Parameter(0x7017, "limit_spd", "float"),
    Parameter(0x7018, "limit_cur", "float"),
    Parameter(0x7019, "mechPos", "float"),
    Parameter(0x701A, "iqf", "float"),
    Parameter(0x701B, "mechVel", "float"),
    Parameter(0x701C, "VBUS", "float"),
    Parameter(0x701E, "loc_kp", "float"),
    Parameter(0x701F, "spd_kp", "float"),
    Parameter(0x7020, "spd_ki", "float"),
    Parameter(0x7021, "spd_filt_gain", "float"),
    Parameter(0x7022, "acc_rad", "float"),
    Parameter(0x7024, "vel_max", "float"),
    Parameter(0x7025, "acc_set", "float"),
    Parameter(0x7026, "EPScan_time", "uint16", True, 1, 200),
    Parameter(0x7028, "cantimeout", "uint32", True, allowed_values=(0,), minimum=20, maximum=100000),
    Parameter(0x7029, "zero_sta", "uint8"),
    Parameter(0x702A, "damper", "uint8"),
    Parameter(0x702B, "add_offset", "float"),
    Parameter(0x702C, "alveolous_open", "uint8"),
    Parameter(0x702D, "iq_test", "uint8"),
    Parameter(0x702E, "dcc_set", "float"),
)
PARAMETER_BY_INDEX: Final = {parameter.index: parameter for parameter in PARAMETERS}

REJECTION_PROBES: Final = {
    "readonly_mechpos": (
        "只读参数 mechPos 写入",
        0x7019,
        struct.pack("<H2xf", 0x7019, 0.0),
    ),
    "epscan_below_min": (
        "EPScan_time=0（低于下限）",
        0x7026,
        struct.pack("<H2xH2x", 0x7026, 0),
    ),
    "cantimeout_gap": (
        "cantimeout=19（禁用值与下限之间）",
        0x7028,
        struct.pack("<H2xI", 0x7028, 19),
    ),
}


def make_id(comm_type: int, data2: int, target_id: int) -> int:
    if not 0 <= comm_type <= 0x1F:
        raise ValueError("communication type must fit in 5 bits")
    if not 0 <= data2 <= 0xFFFF:
        raise ValueError("data2 must fit in 16 bits")
    if not 0 <= target_id <= 0xFF:
        raise ValueError("target ID must fit in 8 bits")
    return (comm_type << 24) | (data2 << 8) | target_id


def split_id(arbitration_id: int) -> tuple[int, int, int]:
    if not 0 <= arbitration_id <= CAN_ID_MASK:
        raise ValueError("extended CAN ID must fit in 29 bits")
    return (
        (arbitration_id >> 24) & 0x1F,
        (arbitration_id >> 8) & 0xFFFF,
        arbitration_id & 0xFF,
    )


def build_device_id_request(node_id: int = 0x7F, host_id: int = 0xFD) -> CanFrame:
    return CanFrame(make_id(0, host_id, node_id), bytes(8))


def build_parameter_read(index: int, node_id: int = 0x7F, host_id: int = 0xFD) -> CanFrame:
    _require_known_parameter(index)
    return CanFrame(make_id(17, host_id, node_id), struct.pack("<H6x", index))


def build_parameter_write(
    index: int,
    value: int | float,
    node_id: int = 0x7F,
    host_id: int = 0xFD,
) -> CanFrame:
    parameter = _require_known_parameter(index)
    validate_safe_write(parameter, value)
    payload = struct.pack("<H2x", index) + pack_value(parameter.kind, value)
    return CanFrame(make_id(18, host_id, node_id), payload)


def build_rejection_probe(
    probe_name: str, node_id: int = 0x7F, host_id: int = 0xFD
) -> tuple[str, int, CanFrame]:
    """Build one fixed, non-motion frame used to verify firmware rejection.

    This deliberately does not accept arbitrary indexes or values.  Keeping
    the three probes closed prevents the diagnostic UI from becoming a way
    around the normal safe-write whitelist.
    """
    try:
        label, index, payload = REJECTION_PROBES[probe_name]
    except KeyError as exc:
        raise ValueError(f"unknown rejection probe: {probe_name}") from exc
    return label, index, CanFrame(make_id(18, host_id, node_id), payload)


def validate_safe_write(parameter: Parameter, value: int | float) -> None:
    if not parameter.writable:
        raise ValueError(f"0x{parameter.index:04X} {parameter.name} is read-only in this app")
    if parameter.allowed_values and value in parameter.allowed_values:
        return
    if parameter.minimum is None or parameter.maximum is None:
        allowed = ", ".join(str(item) for item in parameter.allowed_values)
        raise ValueError(f"{parameter.name} only permits: {allowed}")
    if not parameter.minimum <= value <= parameter.maximum:
        raise ValueError(
            f"{parameter.name} must be {parameter.minimum}..{parameter.maximum}"
            + (f" or {parameter.allowed_values}" if parameter.allowed_values else "")
        )


def pack_value(kind: str, value: int | float) -> bytes:
    formats = {"uint8": "<B3x", "uint16": "<H2x", "uint32": "<I", "float": "<f"}
    try:
        return struct.pack(formats[kind], value)
    except KeyError as exc:
        raise ValueError(f"unsupported parameter kind: {kind}") from exc
    except struct.error as exc:
        raise ValueError(str(exc)) from exc


def unpack_value(kind: str, raw: bytes) -> int | float:
    if len(raw) != 4:
        raise ValueError("parameter value field must contain 4 bytes")
    formats = {"uint8": "<B", "uint16": "<H", "uint32": "<I", "float": "<f"}
    sizes = {"uint8": 1, "uint16": 2, "uint32": 4, "float": 4}
    try:
        return struct.unpack(formats[kind], raw[: sizes[kind]])[0]
    except KeyError as exc:
        raise ValueError(f"unsupported parameter kind: {kind}") from exc


def parse_device_id_response(frame: CanFrame, host_id: int = 0xFD) -> tuple[int, int] | None:
    comm_type, data2, target = split_id(frame.arbitration_id)
    if comm_type != 0 or target not in (host_id, 0xFE) or len(frame.data) != 8:
        return None
    node_id = data2 & 0xFF
    uid = int.from_bytes(frame.data, "little")
    return node_id, uid


def parse_parameter_response(
    frame: CanFrame, host_id: int = 0xFD
) -> tuple[int, int | float] | None:
    comm_type, data2, target = split_id(frame.arbitration_id)
    if comm_type != 17 or target != host_id or len(frame.data) != 8:
        return None
    status = (data2 >> 8) & 0xFF
    if status:
        raise ValueError(f"motor rejected parameter read (status={status})")
    index = int.from_bytes(frame.data[0:2], "little")
    parameter = _require_known_parameter(index)
    return index, unpack_value(parameter.kind, frame.data[4:8])


def encode_at_frame(frame: CanFrame) -> bytes:
    """Wrap a frame for the official RobStride USB-CAN module.

    The module's 32-bit field is ``(CAN_ID << 3) | 0b100`` in big-endian
    byte order.  ``0b100`` selects a CAN 2.0 extended data frame.
    """
    encoded_id = (frame.arbitration_id << 3) | AT_EXTENDED_FLAG
    return AT_HEADER + encoded_id.to_bytes(4, "big") + bytes((len(frame.data),)) + frame.data + AT_TAIL


class AtFrameDecoder:
    """Incrementally decode fragmented/noisy official USB-CAN serial data."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[CanFrame]:
        self._buffer.extend(chunk)
        frames: list[CanFrame] = []
        while True:
            start = self._buffer.find(AT_HEADER)
            if start < 0:
                if self._buffer[-1:] != AT_HEADER[:1]:
                    self._buffer.clear()
                elif len(self._buffer) > 1:
                    del self._buffer[:-1]
                return frames
            if start:
                del self._buffer[:start]
            if len(self._buffer) < 7:
                return frames
            dlc = self._buffer[6]
            if dlc > 8:
                del self._buffer[0]
                continue
            frame_length = 9 + dlc
            if len(self._buffer) < frame_length:
                return frames
            if self._buffer[frame_length - 2 : frame_length] != AT_TAIL:
                del self._buffer[0]
                continue
            encoded_id = int.from_bytes(self._buffer[2:6], "big")
            if encoded_id & 0x07 != AT_EXTENDED_FLAG:
                del self._buffer[:frame_length]
                continue
            arbitration_id = encoded_id >> 3
            if arbitration_id <= CAN_ID_MASK:
                frames.append(CanFrame(arbitration_id, bytes(self._buffer[7 : 7 + dlc])))
            del self._buffer[:frame_length]


def format_frame(frame: CanFrame) -> str:
    return f"ID=0x{frame.arbitration_id:08X} DLC={len(frame.data)} DATA={frame.data.hex(' ').upper()}"


def _require_known_parameter(index: int) -> Parameter:
    try:
        return PARAMETER_BY_INDEX[index]
    except KeyError as exc:
        raise ValueError(f"unknown RS04 parameter index 0x{index:04X}") from exc
