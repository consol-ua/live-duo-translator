"""Audio timing and streaming conversion shared by capture, Gemini and playback."""
from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
import time

import numpy as np
import soxr


class AudioOverload(RuntimeError):
    """Stop explicitly rather than silently discard speech or grow memory forever."""


@dataclass(frozen=True)
class CapturedAudio:
    data: bytes
    captured_at: float


@dataclass(frozen=True)
class PlaybackTiming:
    captured_at: float
    received_at: float


class AudioInputQueue(asyncio.Queue):
    def __init__(self, rate: int, chunk_size: int, max_delay_ms: int = 5000):
        super().__init__(maxsize=max(1, int(rate * max_delay_ms / 1000 / chunk_size)))
        self.rate = rate
        self.queued_samples = 0
        self._times: deque[float] = deque()

    def _put(self, item):
        super()._put(item)
        if item is not None:
            self.queued_samples += len(item.data) // 2
            self._times.append(item.captured_at)

    def _get(self):
        item = super()._get()
        if item is not None:
            self.queued_samples -= len(item.data) // 2
            self._times.popleft()
        return item

    def capture(self, data: bytes, captured_at: float) -> None:
        try:
            self.put_nowait(CapturedAudio(data, captured_at))
        except asyncio.QueueFull as exc:
            raise AudioOverload("Черга Gemini перевищила 5 с. Мовлення не відкидається непомітно; сесію зупинено.") from exc

    def snapshot(self) -> dict:
        age = (time.monotonic() - self._times[0]) * 1000 if self._times else 0.0
        return {
            "queued_ms": round(self.queued_samples * 1000 / self.rate, 1),
            "oldest_ms": round(max(0.0, age), 1),
            "overloaded": age > 1000,
        }


class StreamingResampler:
    """Keep filter/phase state across packets and preserve the filter tail at EOF."""
    def __init__(self, source_rate: int, target_rate: int):
        self.source_rate = source_rate
        self.target_rate = target_rate
        self._stream = None if source_rate == target_rate else soxr.ResampleStream(
            source_rate, target_rate, 1, dtype="int16", quality="HQ"
        )

    def process(self, samples: np.ndarray, *, last: bool = False) -> np.ndarray:
        samples = np.asarray(samples, dtype=np.int16)
        if self._stream is None:
            return samples
        return self._stream.resample_chunk(samples, last=last)


class LatencyStats:
    def __init__(self):
        self._values: deque[float] = deque(maxlen=256)

    def add(self, milliseconds: float) -> None:
        self._values.append(max(0.0, milliseconds))

    def snapshot(self) -> dict:
        if not self._values:
            return {"latest_ms": None, "p50_ms": None, "p95_ms": None, "samples": 0}
        return {
            "latest_ms": round(self._values[-1], 1),
            "p50_ms": round(float(np.percentile(self._values, 50)), 1),
            "p95_ms": round(float(np.percentile(self._values, 95)), 1),
            "samples": len(self._values),
        }
