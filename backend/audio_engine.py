from __future__ import annotations
import asyncio
import os
import wave
import time
from pathlib import Path
import numpy as np
import pyaudio
from typing import List, Dict, Optional, Callable
import threading
from collections import deque
from audio_support import AudioInputQueue, AudioOverload, PlaybackTiming, StreamingResampler


class AudioStreamBuffer:
    """FIFO with explicit startup/recovery targets; never discard translated samples."""
    def __init__(self, sample_rate: int = 24000, jitter_buffer_ms: int = 40):
        self.sample_rate = sample_rate
        self.jitter_buffer_ms = max(30, min(300, jitter_buffer_ms))
        self.recovery_ms = self.jitter_buffer_ms
        self._chunks = deque()
        self._total_samples = 0
        self._is_buffering = True
        self._turn_ended = True
        self._was_playing = False
        self._last_adaptation = time.monotonic()
        self.underruns = 0
        self._lock = threading.Lock()

    def set_jitter_buffer_ms(self, ms: int) -> None:
        with self._lock:
            self.jitter_buffer_ms = max(30, min(300, ms))
            self.recovery_ms = self.jitter_buffer_ms

    @property
    def target_buffer_samples(self) -> int:
        return int(self.sample_rate * self.recovery_ms / 1000)

    def push(self, samples: np.ndarray, timing: Optional[PlaybackTiming] = None) -> None:
        if not len(samples):
            return
        with self._lock:
            if self._total_samples + len(samples) > self.sample_rate * 15:
                raise AudioOverload("Буфер перекладу перевищив 15 с; відтворення не встигає за потоком.")
            now = time.monotonic()
            self._chunks.append((samples.copy(), timing, now))
            self._total_samples += len(samples)
            self._turn_ended = False
            # Reduce recovery cushion only after a stable ten-second interval.
            if now - self._last_adaptation >= 10:
                self.recovery_ms = max(self.jitter_buffer_ms, self.recovery_ms - 10)
                self._last_adaptation = now

    def pop_with_timing(self, num_samples: int):
        with self._lock:
            if not self._total_samples:
                if self._was_playing and not self._turn_ended:
                    self.underruns += 1
                    self.recovery_ms = min(300, self.recovery_ms + 20)
                    self._last_adaptation = time.monotonic()
                self._was_playing = False
                self._is_buffering = True
                return None, []
            if self._is_buffering and not self._turn_ended:
                if self._total_samples < self.target_buffer_samples:
                    return None, []
            self._is_buffering = False
            self._was_playing = True
            output = np.zeros(num_samples, dtype=np.int16)
            written = 0
            timings = []
            while written < num_samples and self._chunks:
                chunk, timing, received_at = self._chunks.popleft()
                count = min(len(chunk), num_samples - written)
                output[written:written + count] = chunk[:count]
                if timing is not None:
                    timings.append(timing)
                written += count
                self._total_samples -= count
                if count < len(chunk):
                    self._chunks.appendleft((chunk[count:], None, received_at))
            if written < num_samples and not self._turn_ended:
                self.underruns += 1
                self.recovery_ms = min(300, self.recovery_ms + 20)
                self._last_adaptation = time.monotonic()
                self._is_buffering = True
                self._was_playing = False
            return output, timings

    def pop(self, num_samples: int) -> Optional[np.ndarray]:
        return self.pop_with_timing(num_samples)[0]

    def flush(self) -> None:
        with self._lock:
            self._turn_ended = True
            self._is_buffering = False

    def clear(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._total_samples = 0
            self._is_buffering = True
            self._turn_ended = True
            self._was_playing = False

    def reset(self) -> None:
        self.clear()
        self.underruns = 0
        self.recovery_ms = self.jitter_buffer_ms
        self._last_adaptation = time.monotonic()

    def has_audio(self) -> bool:
        with self._lock:
            return self._total_samples > 0

    def snapshot(self) -> dict:
        with self._lock:
            age = (time.monotonic() - self._chunks[0][2]) * 1000 if self._chunks else 0.0
            return {"queued_ms": round(self._total_samples * 1000 / self.sample_rate, 1),
                    "oldest_ms": round(age, 1), "target_ms": self.recovery_ms,
                    "underruns": self.underruns, "overloaded": age > 1000}


class SmartDuckingDSP:
    """
    Sidechain compression with smooth attack, hold time, and release curves.
    Prevents volume pumping and audio popping between words.
    """

    def __init__(
        self,
        sample_rate: int = 16000,
        ducking_factor: float = 0.2,
        attack_ms: float = 40.0,
        hold_ms: float = 500.0,
        release_ms: float = 200.0,
    ) -> None:
        self.sample_rate = sample_rate
        self.ducking_factor = max(0.0, min(1.0, ducking_factor))
        self.attack_ms = attack_ms
        self.hold_ms = hold_ms
        self.release_ms = release_ms

        self.current_gain: float = 1.0
        self.last_voice_activity_time: float = 0.0
        self.is_active: bool = False
        self._cached_ramp_len: int = 0
        self._cached_ramp: Optional[np.ndarray] = None

    def set_ducking_factor(self, factor: float) -> None:
        self.ducking_factor = max(0.0, min(1.0, factor))

    def process(
        self,
        background_chunk: np.ndarray,
        voiceover_chunk: Optional[np.ndarray],
    ) -> tuple[np.ndarray, bool]:
        """
        Smoothly attenuates background audio and mixes in the voiceover.
        Returns: (mixed_pcm_int16, is_ducking_active)
        """
        chunk_len = len(background_chunk)
        if chunk_len == 0:
            return background_chunk, False

        now = time.monotonic()
        chunk_duration = chunk_len / self.sample_rate

        has_voiceover = voiceover_chunk is not None and np.any(np.abs(voiceover_chunk) > 50)

        if has_voiceover:
            self.last_voice_activity_time = now
            target_gain = self.ducking_factor
            self.is_active = True
        else:
            time_since_voice = (now - self.last_voice_activity_time) * 1000.0
            if time_since_voice < self.hold_ms:
                target_gain = self.ducking_factor
                self.is_active = True
            else:
                target_gain = 1.0
                self.is_active = False

        # Calculate gain transition rates
        if target_gain < self.current_gain:
            rate_per_sec = (1.0 - self.ducking_factor) / (self.attack_ms / 1000.0)
            max_gain_delta = rate_per_sec * chunk_duration
            end_gain = max(target_gain, self.current_gain - max_gain_delta)
        else:
            rate_per_sec = (1.0 - self.ducking_factor) / (self.release_ms / 1000.0)
            max_gain_delta = rate_per_sec * chunk_duration
            end_gain = min(target_gain, self.current_gain + max_gain_delta)

        if chunk_len != self._cached_ramp_len:
            self._cached_ramp_len = chunk_len
        # Build ramp using np.linspace only when start/end gains differ significantly
        if abs(self.current_gain - end_gain) < 1e-6:
            gain_ramp = np.full(chunk_len, end_gain, dtype=np.float32)
        else:
            gain_ramp = np.linspace(self.current_gain, end_gain, chunk_len, dtype=np.float32)
        self.current_gain = end_gain

        bg_float = background_chunk.astype(np.float32)
        ducked_bg = bg_float * gain_ramp

        if voiceover_chunk is not None and len(voiceover_chunk) > 0:
            vo_float = voiceover_chunk.astype(np.float32)
            if len(vo_float) < chunk_len:
                vo_float = np.pad(vo_float, (0, chunk_len - len(vo_float)))
            elif len(vo_float) > chunk_len:
                vo_float = vo_float[:chunk_len]
            mixed = ducked_bg + vo_float
        else:
            mixed = ducked_bg

        mixed_clipped = np.clip(mixed, -32768, 32767).astype(np.int16)
        is_ducking = self.current_gain < 0.95 or self.is_active
        return mixed_clipped, is_ducking


class DualChannelAudioEngine:
    """
    High-performance audio engine for bidirectional translation, sample playback, and mic testing.
    """

    def __init__(
        self,
        format_type: int = pyaudio.paInt16,
        channels: int = 1,
        rate: int = 16000,
        chunk_size: Optional[int] = None,
        output_rate: int = 24000,
    ) -> None:
        self.format_type = format_type
        self.channels = channels
        self.rate = rate
        chunk_ms = int(os.environ.get("AUDIO_CHUNK_MS", "20"))
        if chunk_ms not in (10, 20, 32):
            raise ValueError("AUDIO_CHUNK_MS must be 10, 20 or 32")
        self.chunk_size = chunk_size or int(rate * chunk_ms / 1000)
        self.output_rate = output_rate
        self.output_chunk_size = round(self.chunk_size * output_rate / rate)

        self.p: pyaudio.PyAudio = pyaudio.PyAudio()

        # Outgoing streams
        self.my_mic_stream: Optional[pyaudio.Stream] = None
        self.call_virtual_mic_stream: Optional[pyaudio.Stream] = None

        # Incoming streams
        self.call_input_stream: Optional[pyaudio.Stream] = None
        self.headphones_stream: Optional[pyaudio.Stream] = None

        # Mic testing stream
        self.mic_test_stream: Optional[pyaudio.Stream] = None

        # Input Queues for Gemini STT
        self.outgoing_input_queue = AudioInputQueue(rate, self.chunk_size)
        self.incoming_input_queue = AudioInputQueue(rate, self.chunk_size)

        # Preserve the former effective 40 ms startup cushion; recover adaptively.
        self.outgoing_playback_buffer = AudioStreamBuffer(sample_rate=output_rate, jitter_buffer_ms=40)
        self.incoming_playback_buffer = AudioStreamBuffer(sample_rate=output_rate, jitter_buffer_ms=40)

        # Mic Test Audio Collector (Recorded translated PCM)
        self.mic_test_recorded_pcm: List[np.ndarray] = []
        self._recording_mic_test = False
        self.mic_test_file_path: Path = Path(__file__).parent / "samples" / "mic_test_result.wav"

        # Smart Ducking DSP Engines (Fast 25ms attack)
        self.incoming_ducking_dsp = SmartDuckingDSP(
            sample_rate=output_rate,
            ducking_factor=0.2,
            attack_ms=25.0,
            hold_ms=350.0,
            release_ms=150.0,
        )

        # Status flags
        self.is_call_running: bool = False
        self.is_dubbing_running: bool = False
        self.is_sample_running: bool = False
        self.is_mic_test_running: bool = False
        self.is_incoming_ducking: bool = False

        # Thread synchronization lock for PyAudio C-extension calls
        self._stream_lock: threading.Lock = threading.Lock()
        self._io_locks = {}
        self._playback_tasks = set()
        self._outgoing_playback_running = False
        self._incoming_playback_running = False
        self._background = AudioStreamBuffer(output_rate, 30)
        self._background_resampler = StreamingResampler(rate, output_rate)
        self.on_audio_error = None
        self.on_outgoing_playback = None
        self.on_incoming_playback = None
        self.input_overflows = 0
        self.output_underflows = 0

        # Telemetry Callbacks
        self.outgoing_telemetry_cb: Optional[Callable[[float], None]] = None
        self.incoming_telemetry_cb: Optional[Callable[[float, bool], None]] = None

    def list_devices(self) -> List[Dict[str, str | int]]:
        """List all available audio input and output devices."""
        devices: List[Dict[str, str | int]] = []
        with self._stream_lock:
            device_count = self.p.get_device_count()
            for i in range(device_count):
                try:
                    info = self.p.get_device_info_by_index(i)
                    devices.append(
                        {
                            "index": i,
                            "name": str(info.get("name")),
                            "max_input_channels": int(info.get("maxInputChannels", 0)),
                            "max_output_channels": int(info.get("maxOutputChannels", 0)),
                            "default_sample_rate": int(info.get("defaultSampleRate", 0)),
                        }
                    )
                except Exception:
                    pass
        return devices

    def set_ducking_factor(self, factor: float) -> None:
        self.incoming_ducking_dsp.set_ducking_factor(factor)

    def set_jitter_buffer_ms(self, ms: int) -> None:
        self.outgoing_playback_buffer.set_jitter_buffer_ms(ms)
        self.incoming_playback_buffer.set_jitter_buffer_ms(ms)

    def _calculate_rms_db(self, audio_data: np.ndarray) -> float:
        float_data = audio_data.astype(np.float32)
        rms = np.sqrt(np.mean(float_data**2))
        if rms > 0:
            return float(20 * np.log10(rms / 32767.0))
        return -100.0

    def _safe_read(self, stream, chunk_size):
        if stream is None:
            return None
        with self._io_locks[id(stream)]:
            return stream.read(chunk_size, exception_on_overflow=True)

    def _safe_write(self, stream, data):
        if stream is None:
            return False
        with self._io_locks[id(stream)]:
            try:
                stream.write(data, exception_on_underflow=True)
            except OSError as exc:
                if exc.errno != pyaudio.paOutputUnderflowed:
                    raise
                # PortAudio has accepted this block but reports an earlier gap.
                # Count it; retrying the block would duplicate speech.
                self.output_underflows += 1
        return True

    def _open_stream(self, **kwargs):
        stream = self.p.open(**kwargs)
        self._io_locks[id(stream)] = threading.Lock()
        return stream

    def _safe_close_stream(self, stream_attr):
        with self._stream_lock:
            stream = getattr(self, stream_attr, None)
            if stream is not None:
                with self._io_locks[id(stream)]:
                    try:
                        if stream.is_active():
                            stream.stop_stream()
                    finally:
                        stream.close()
                        setattr(self, stream_attr, None)
                self._io_locks.pop(id(stream), None)

    def _prepare(self):
        self._drain_queues()
        self.outgoing_playback_buffer.reset()
        self.incoming_playback_buffer.reset()
        self._background.reset()
        self._background_resampler = StreamingResampler(self.rate, self.output_rate)
        self.input_overflows = self.output_underflows = 0
        self.incoming_ducking_dsp.current_gain = 1.0
        self.incoming_ducking_dsp.last_voice_activity_time = 0.0

    def start_playback(self, outgoing=False, incoming=False):
        self._outgoing_playback_running = outgoing and self.call_virtual_mic_stream is not None
        self._incoming_playback_running = incoming
        for channel, enabled in (("outgoing", self._outgoing_playback_running), ("incoming", incoming)):
            if enabled:
                task = asyncio.create_task(self._playback_loop(channel), name=f"playback-{channel}")
                self._playback_tasks.add(task)
                task.add_done_callback(self._playback_tasks.discard)

    async def _playback_loop(self, channel):
        outgoing = channel == "outgoing"
        buffer = self.outgoing_playback_buffer if outgoing else self.incoming_playback_buffer
        stream = self.call_virtual_mic_stream if outgoing else self.headphones_stream
        callback = self.on_outgoing_playback if outgoing else self.on_incoming_playback
        loop = asyncio.get_running_loop()
        try:
            while (self._outgoing_playback_running if outgoing else self._incoming_playback_running):
                audio, timings = buffer.pop_with_timing(self.output_chunk_size)
                if outgoing:
                    output = audio if audio is not None else np.zeros(self.output_chunk_size, dtype=np.int16)
                else:
                    background = self._background.pop(self.output_chunk_size)
                    if background is None:
                        background = np.zeros(self.output_chunk_size, dtype=np.int16)
                    output, self.is_incoming_ducking = self.incoming_ducking_dsp.process(background, audio)
                submitted_at = time.monotonic()
                # PortAudio owns playback pacing, including silence between translated turns.
                latency = stream.get_output_latency()
                await loop.run_in_executor(None, self._safe_write, stream, output.tobytes())
                if callback:
                    for timing in timings:
                        callback(timing, submitted_at, latency)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if outgoing:
                self._outgoing_playback_running = False
            else:
                self._incoming_playback_running = False
            self.output_underflows += 1
            if self.on_audio_error:
                self.on_audio_error(f"Помилка відтворення {channel}: {exc}", "AUDIO_OUTPUT")

    async def _finish_playback(self, timeout):
        self.outgoing_playback_buffer.flush()
        self.incoming_playback_buffer.flush()
        self._background.flush()
        deadline = time.monotonic() + timeout
        while ((self._outgoing_playback_running and self.outgoing_playback_buffer.has_audio())
               or (self._incoming_playback_running and (self.incoming_playback_buffer.has_audio() or self._background.has_audio()))):
            if time.monotonic() >= deadline:
                if self.on_audio_error:
                    self.on_audio_error("Не вдалося повністю відтворити переклад до завершення таймауту.", "PLAYBACK_TIMEOUT")
                break
            await asyncio.sleep(0.01)
        self._outgoing_playback_running = self._incoming_playback_running = False
        if self._playback_tasks:
            await asyncio.gather(*list(self._playback_tasks), return_exceptions=True)
        return not (self.outgoing_playback_buffer.has_audio() or self.incoming_playback_buffer.has_audio() or self._background.has_audio())

    def diagnostics(self):
        return {"outgoing": {"input": self.outgoing_input_queue.snapshot(), "playback": self.outgoing_playback_buffer.snapshot()},
                "incoming": {"input": self.incoming_input_queue.snapshot(), "playback": self.incoming_playback_buffer.snapshot()},
                "input_errors": self.input_overflows, "output_errors": self.output_underflows,
                "chunk_ms": self.chunk_size * 1000 / self.rate, "output_rate": self.output_rate}

    def push_outgoing_tts_chunk(self, chunk: np.ndarray, timing: Optional[PlaybackTiming] = None) -> None:
        if self._recording_mic_test:
            self.mic_test_recorded_pcm.append(chunk.copy())
        else:
            self.outgoing_playback_buffer.push(chunk, timing)

    def push_incoming_tts_chunk(self, chunk: np.ndarray, timing: Optional[PlaybackTiming] = None) -> None:
        self.incoming_playback_buffer.push(chunk, timing)

    def clear_playback_buffers(self) -> None:
        self.outgoing_playback_buffer.clear()
        self.incoming_playback_buffer.clear()

    def start_call(
        self,
        my_mic_index: Optional[int] = None,
        call_virtual_mic_index: Optional[int] = None,
        call_input_index: Optional[int] = None,
        headphones_index: Optional[int] = None,
    ) -> None:
        if self.is_call_running:
            return

        self._prepare()

        with self._stream_lock:
            self.my_mic_stream = self._open_stream(
                format=self.format_type,
                channels=self.channels,
                rate=self.rate,
                input=True,
                input_device_index=my_mic_index,
                frames_per_buffer=self.chunk_size,
            )

            if call_virtual_mic_index is not None:
                self.call_virtual_mic_stream = self._open_stream(
                    format=self.format_type,
                    channels=self.channels,
                    rate=self.output_rate,
                    output=True,
                    output_device_index=call_virtual_mic_index,
                    frames_per_buffer=self.output_chunk_size,
                )

            if call_input_index is not None:
                self.call_input_stream = self._open_stream(
                    format=self.format_type,
                    channels=self.channels,
                    rate=self.rate,
                    input=True,
                    input_device_index=call_input_index,
                    frames_per_buffer=self.chunk_size,
                )

            self.headphones_stream = self._open_stream(
                format=self.format_type,
                channels=self.channels,
                rate=self.output_rate,
                output=True,
                output_device_index=headphones_index,
                frames_per_buffer=self.output_chunk_size,
            )

        self.is_call_running = True

    async def graceful_stop_call(self, timeout: float = 15.0) -> bool:
        self.is_call_running = False
        complete = await self._finish_playback(timeout)
        await asyncio.to_thread(self.stop_call)
        return complete

    def stop_call(self) -> None:
        self.is_call_running = False
        self._outgoing_playback_running = self._incoming_playback_running = False

        for stream_attr in [
            "my_mic_stream",
            "call_virtual_mic_stream",
            "call_input_stream",
            "headphones_stream",
        ]:
            self._safe_close_stream(stream_attr)

        self._drain_queues()
        self.clear_playback_buffers()

    def start_dubbing(
        self,
        input_device_index: Optional[int] = None,
        headphones_index: Optional[int] = None,
    ) -> None:
        if self.is_dubbing_running:
            return

        self._prepare()

        with self._stream_lock:
            if input_device_index is not None:
                self.call_input_stream = self._open_stream(
                    format=self.format_type,
                    channels=self.channels,
                    rate=self.rate,
                    input=True,
                    input_device_index=input_device_index,
                    frames_per_buffer=self.chunk_size,
                )

            self.headphones_stream = self._open_stream(
                format=self.format_type,
                channels=self.channels,
                rate=self.output_rate,
                output=True,
                output_device_index=headphones_index,
                frames_per_buffer=self.output_chunk_size,
            )

        self.is_dubbing_running = True

    async def graceful_stop_dubbing(self, timeout: float = 15.0) -> bool:
        self.is_dubbing_running = False
        complete = await self._finish_playback(timeout)
        await asyncio.to_thread(self.stop_dubbing)
        return complete

    def stop_dubbing(self) -> None:
        self.is_dubbing_running = False
        self._incoming_playback_running = False

        for stream_attr in [
            "call_input_stream",
            "headphones_stream",
        ]:
            self._safe_close_stream(stream_attr)

        self._drain_queues()
        self.clear_playback_buffers()

    def start_sample_test(self, headphones_index: Optional[int] = None) -> None:
        if self.is_sample_running:
            return

        self._prepare()

        with self._stream_lock:
            self.headphones_stream = self._open_stream(
                format=self.format_type,
                channels=self.channels,
                rate=self.output_rate,
                output=True,
                output_device_index=headphones_index,
                frames_per_buffer=self.output_chunk_size,
            )
        self.is_sample_running = True

    def stop_sample_test(self) -> None:
        self.is_sample_running = False
        self._incoming_playback_running = False
        self._safe_close_stream("headphones_stream")
        self._drain_queues()
        self.clear_playback_buffers()

    def start_mic_test(self, mic_index: Optional[int] = None) -> None:
        """Start microphone test recording session."""
        if self.is_mic_test_running:
            return

        self.mic_test_recorded_pcm = []
        self._prepare()

        with self._stream_lock:
            self.mic_test_stream = self._open_stream(
                format=self.format_type,
                channels=self.channels,
                rate=self.rate,
                input=True,
                input_device_index=mic_index,
                frames_per_buffer=self.chunk_size,
            )
        self.is_mic_test_running = True
        self._recording_mic_test = True

    def stop_mic_test(self) -> Optional[str]:
        """Stop mic test recording and save translated WAV file."""
        self.is_mic_test_running = False
        self._recording_mic_test = False
        self._safe_close_stream("mic_test_stream")
        self._drain_queues()

        # Save collected translated audio to WAV file
        if len(self.mic_test_recorded_pcm) > 0:
            full_audio = np.concatenate(self.mic_test_recorded_pcm)
            self.mic_test_file_path.parent.mkdir(parents=True, exist_ok=True)
            with wave.open(str(self.mic_test_file_path), "wb") as wf:
                wf.setnchannels(self.channels)
                wf.setsampwidth(2)  # 16-bit
                wf.setframerate(self.output_rate)
                wf.writeframes(full_audio.tobytes())
            return str(self.mic_test_file_path)
        return None

    def _drain_queues(self) -> None:
        for q in [self.outgoing_input_queue, self.incoming_input_queue]:
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break

    async def _capture_loop(self, stream_attr, queue, active, telemetry=None, background=False):
        loop = asyncio.get_running_loop()
        try:
            while active():
                stream = getattr(self, stream_attr)
                if stream is None:
                    return
                raw = await loop.run_in_executor(None, self._safe_read, stream, self.chunk_size)
                # A stop may arrive while read() is in flight. Keep that final
                # block; the caller waits for capture before sending stream-end.
                captured_at = time.monotonic() - self.chunk_size / self.rate
                queue.capture(raw, captured_at)
                samples = np.frombuffer(raw, dtype=np.int16)
                if background:
                    converted = self._background_resampler.process(samples)
                    self._background.push(converted)
                    # Background is continuous live audio, not a network-jittered TTS turn.
                    self._background.flush()
                if telemetry:
                    telemetry(self._calculate_rms_db(samples))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.input_overflows += 1
            if self.on_audio_error:
                self.on_audio_error(f"Помилка захоплення: {exc}", "AUDIO_INPUT")

    async def outgoing_process_loop(self):
        await self._capture_loop("my_mic_stream", self.outgoing_input_queue,
                                 lambda: self.is_call_running, self.outgoing_telemetry_cb)

    async def mic_test_loop(self):
        await self._capture_loop("mic_test_stream", self.outgoing_input_queue,
                                 lambda: self.is_mic_test_running, self.outgoing_telemetry_cb)

    async def incoming_process_loop(self):
        def telemetry(db):
            if self.incoming_telemetry_cb:
                self.incoming_telemetry_cb(db, self.is_incoming_ducking)
        await self._capture_loop("call_input_stream", self.incoming_input_queue,
                                 lambda: self.is_call_running or self.is_dubbing_running,
                                 telemetry, background=True)
        tail = self._background_resampler.process(np.empty(0, dtype=np.int16), last=True)
        self._background.push(tail)
        self._background.flush()

    async def sample_playback_loop(self, sample_filepath: str):
        """Feed a WAV at absolute real-time deadlines; playback has its own device clock."""
        with wave.open(sample_filepath, "rb") as wf:
            if wf.getframerate() != self.rate or wf.getnchannels() != 1 or wf.getsampwidth() != 2:
                raise ValueError("Sample must be mono 16-bit PCM at 16 kHz")
            started_at = time.monotonic()
            frames_sent = 0
            while self.is_sample_running:
                raw = wf.readframes(self.chunk_size)
                if not raw:
                    break
                capture_at = started_at + frames_sent / self.rate
                await asyncio.sleep(max(0.0, capture_at - time.monotonic()))
                if not self.is_sample_running:
                    break
                self.incoming_input_queue.capture(raw, capture_at)
                samples = np.frombuffer(raw, dtype=np.int16)
                self._background.push(self._background_resampler.process(samples))
                self._background.flush()
                frames_sent += len(samples)
                if self.incoming_telemetry_cb:
                    self.incoming_telemetry_cb(self._calculate_rms_db(samples), self.is_incoming_ducking)
            await asyncio.sleep(max(0.0, started_at + frames_sent / self.rate - time.monotonic()))
            self._background.push(self._background_resampler.process(np.empty(0, dtype=np.int16), last=True))
            self._background.flush()

    def terminate(self) -> None:
        """Cleanup PyAudio instance."""
        self.stop_call()
        self.stop_dubbing()
        self.stop_sample_test()
        self.stop_mic_test()
        with self._stream_lock:
            try:
                self.p.terminate()
            except Exception:
                pass

