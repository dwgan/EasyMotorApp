"""Binary RS485 waveform framing, independent of serial and Tk."""

from __future__ import annotations

from collections.abc import Iterable


WAVE_SOF = 0xA5
WAVE_FRAME_LEN = 10
WAVE_STATS_SOF = 0xA6
WAVE_STATS_FRAME_LEN = 16
WAVE_SINGLE_SOF = 0xA7
WAVE_SINGLE_BLOCK_SAMPLES = 128
WAVE_END_SEQ = 0xFFFF
WAVE_PENDING_MAX = 4096

WaveEvent = tuple[str, object]


def _text_events(raw: bytes) -> Iterable[WaveEvent]:
    for line in raw.splitlines():
        line = line.rstrip(b"\r")
        if line and all(32 <= byte < 127 for byte in line):
            yield "line", line.decode("ascii", errors="replace")


class WaveFrameDecoder:
    """Consume complete waveform frames while retaining fragmented input."""

    def __init__(self, pending_limit: int = WAVE_PENDING_MAX) -> None:
        self.pending_limit = pending_limit
        self.checksum_errors = 0
        self.discarded_bytes = 0

    def _bound_pending(self, pending: bytearray) -> None:
        overflow = len(pending) - self.pending_limit
        if overflow > 0:
            del pending[:overflow]
            self.discarded_bytes += overflow

    def extract(self, pending: bytearray) -> list[WaveEvent]:
        events: list[WaveEvent] = []
        while True:
            starts = (
                pending.find(bytes((WAVE_SOF,))),
                pending.find(bytes((WAVE_STATS_SOF,))),
                pending.find(bytes((WAVE_SINGLE_SOF,))),
            )
            candidates = [position for position in starts if position >= 0]
            if not candidates:
                last_newline = pending.rfind(b"\n")
                if last_newline >= 0:
                    events.extend(_text_events(bytes(pending[: last_newline + 1])))
                    del pending[: last_newline + 1]
                self._bound_pending(pending)
                return events

            start = min(candidates)
            if start > 0:
                events.extend(_text_events(bytes(pending[:start])))
                del pending[:start]

            frame_type = pending[0]
            if frame_type == WAVE_SINGLE_SOF:
                if len(pending) < 5:
                    return events
                sample_count = pending[4]
                if not 1 <= sample_count <= WAVE_SINGLE_BLOCK_SAMPLES:
                    del pending[0]
                    continue
                length = 8 + sample_count * 2
            else:
                sample_count = 0
                length = (
                    WAVE_STATS_FRAME_LEN
                    if frame_type == WAVE_STATS_SOF
                    else WAVE_FRAME_LEN
                )
            if len(pending) < length:
                return events

            frame = bytes(pending[:length])
            checksum = 0
            for byte in frame[1:-1]:
                checksum ^= byte
            if checksum != frame[-1]:
                # A candidate SOF may be payload noise. Drop only that byte so
                # the next genuine frame inside the candidate remains visible.
                del pending[0]
                self.checksum_errors += 1
                self.discarded_bytes += 1
                continue
            del pending[:length]

            sequence = frame[1] | (frame[2] << 8)
            if frame_type == WAVE_STATS_SOF:
                values = tuple(
                    int.from_bytes(
                        frame[3 + 2 * index : 5 + 2 * index],
                        "little",
                        signed=True,
                    )
                    for index in range(6)
                )
                events.append(("wave_stats", (sequence, values)))
            elif frame_type == WAVE_SINGLE_SOF:
                channel = frame[3]
                if channel > 2:
                    continue
                dropped = int.from_bytes(frame[5:7], "little")
                samples = tuple(
                    int.from_bytes(
                        frame[7 + 2 * index : 9 + 2 * index],
                        "little",
                        signed=True,
                    )
                    for index in range(sample_count)
                )
                events.append(
                    ("wave_single", (sequence, channel, dropped, samples))
                )
            else:
                phase_u = int.from_bytes(frame[3:5], "little", signed=True)
                phase_v = int.from_bytes(frame[5:7], "little", signed=True)
                phase_w = int.from_bytes(frame[7:9], "little", signed=True)
                events.append(("wave", (sequence, phase_u, phase_v, phase_w)))
