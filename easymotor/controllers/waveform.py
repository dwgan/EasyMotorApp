"""Bounded waveform storage and loss accounting without Tk dependencies."""

from __future__ import annotations

from array import array
from collections import deque
from collections.abc import Iterator


class CompactSampleRing:
    """Fixed-size signed sample ring with compact 16-bit storage."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        self.capacity = capacity
        self._sequences = array("H", [0]) * capacity
        self._values = array("h", [0]) * capacity
        self._start = 0
        self._size = 0

    def clear(self) -> None:
        self._start = 0
        self._size = 0

    def append(self, item: tuple[int, int]) -> None:
        sequence, value = item
        if self._size < self.capacity:
            index = (self._start + self._size) % self.capacity
            self._size += 1
        else:
            index = self._start
            self._start = (self._start + 1) % self.capacity
        self._sequences[index] = sequence & 0xFFFF
        self._values[index] = value

    def __len__(self) -> int:
        return self._size

    def __iter__(self) -> Iterator[tuple[int, int]]:
        for offset in range(self._size):
            index = (self._start + offset) % self.capacity
            yield self._sequences[index], self._values[index]


class WaveformStore:
    CHANNELS = ("u", "v", "w")

    def __init__(
        self,
        *,
        raw_capacity: int,
        display_capacity: int,
        glitch_threshold: int,
    ) -> None:
        self.raw = {
            channel: CompactSampleRing(raw_capacity) for channel in self.CHANNELS
        }
        self.stats = deque(maxlen=display_capacity)
        self.display = {
            channel: deque(maxlen=display_capacity) for channel in self.CHANNELS
        }
        self.display_accumulators: dict[str, list[int] | None] = {
            channel: None for channel in self.CHANNELS
        }
        self.display_bucket_samples = 1
        self.glitch_threshold = glitch_threshold
        self.reset()

    def reset(self) -> None:
        for values in self.raw.values():
            values.clear()
        self.stats.clear()
        for values in self.display.values():
            values.clear()
        for channel in self.CHANNELS:
            self.display_accumulators[channel] = None
        self.last_sequence: int | None = None
        self.last_firmware_dropped: int | None = None
        self.frame_count = 0
        self.transport_lost_count = 0
        self.firmware_drop_count = 0
        self.glitch_count = 0
        self.previous_raw: tuple[int, int, int] | None = None

    @property
    def lost_count(self) -> int:
        return self.transport_lost_count + self.firmware_drop_count

    def _record_sequence_gap(self, sequence: int) -> None:
        if self.last_sequence is None:
            return
        expected = (self.last_sequence + 1) & 0xFFFF
        gap = (sequence - expected) & 0xFFFF
        self.transport_lost_count += gap

    def configure_display(self, sample_rate_hz: float, envelope_hz: int) -> None:
        for values in self.display.values():
            values.clear()
        for channel in self.CHANNELS:
            self.display_accumulators[channel] = None
        self.display_bucket_samples = max(1, round(sample_rate_hz / envelope_hz))

    def append_display(self, channel: str, sequence: int, value: int) -> None:
        accumulator = self.display_accumulators[channel]
        if accumulator is None:
            accumulator = [sequence, 1, value, value]
            self.display_accumulators[channel] = accumulator
        else:
            accumulator[1] += 1
            accumulator[2] = min(accumulator[2], value)
            accumulator[3] = max(accumulator[3], value)
        if accumulator[1] >= self.display_bucket_samples:
            self.display[channel].append(
                (accumulator[0], accumulator[2], accumulator[3])
            )
            self.display_accumulators[channel] = None

    def ingest_three_phase(self, frame: tuple[int, int, int, int]) -> None:
        sequence, phase_u, phase_v, phase_w = frame
        self._record_sequence_gap(sequence)
        self.last_sequence = sequence
        self.frame_count += 1
        if self.previous_raw is not None:
            if any(
                abs(value - previous) > self.glitch_threshold
                for value, previous in zip(
                    (phase_u, phase_v, phase_w), self.previous_raw
                )
            ):
                self.glitch_count += 1
        self.previous_raw = (phase_u, phase_v, phase_w)
        for channel, value in zip(self.CHANNELS, self.previous_raw):
            self.raw[channel].append((sequence, value))
            self.append_display(channel, sequence, value)

    def ingest_stats(
        self, frame: tuple[int, tuple[int, int, int, int, int, int]]
    ) -> None:
        sequence, values = frame
        self._record_sequence_gap(sequence)
        self.last_sequence = sequence
        self.frame_count += 1
        self.stats.append((sequence, values))

    def ingest_single(
        self, frame: tuple[int, int, int, tuple[int, ...]]
    ) -> None:
        sequence_start, channel_index, dropped, samples = frame
        channel = self.CHANNELS[channel_index]
        self._record_sequence_gap(sequence_start)
        if self.last_firmware_dropped is None:
            self.firmware_drop_count += dropped
        else:
            self.firmware_drop_count += (
                dropped - self.last_firmware_dropped
            ) & 0xFFFF
        self.last_firmware_dropped = dropped
        previous = self.previous_raw[channel_index] if self.previous_raw else None
        for offset, value in enumerate(samples):
            sequence = (sequence_start + offset) & 0xFFFF
            self.raw[channel].append((sequence, value))
            self.append_display(channel, sequence, value)
            if previous is not None and abs(value - previous) > self.glitch_threshold:
                self.glitch_count += 1
            previous = value
        if samples:
            previous_values = list(self.previous_raw or (0, 0, 0))
            previous_values[channel_index] = samples[-1]
            self.previous_raw = tuple(previous_values)
            self.last_sequence = (sequence_start + len(samples) - 1) & 0xFFFF
            self.frame_count += len(samples)
