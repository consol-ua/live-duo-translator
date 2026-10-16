import os
import time
import asyncio
import re
from audio_support import CapturedAudio, LatencyStats, PlaybackTiming, StreamingResampler
import numpy as np
from typing import Callable, Optional
from google import genai
from google.genai import types
from google.genai.errors import APIError


VALID_VOICE_NAMES = {"Puck", "Charon", "Fenrir", "Aoede", "Kore"}


class GeminiLiveAudioSession:
    """Independent Gemini 3.5 Live Streaming Session for a single directional audio channel."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        target_lang: str = "uk",
        voice_name: str = "Puck",
        sample_rate: int = 16000,
        channel_name: str = "channel",
        output_sample_rate: int = 24000,
    ) -> None:
        self.api_key = api_key or os.environ.get("GEMINI_API_KEY", "")
        self.target_lang = target_lang
        self.voice_name = voice_name if voice_name in VALID_VOICE_NAMES else "Puck"
        self.sample_rate = sample_rate
        self.output_sample_rate = output_sample_rate
        self.channel_name = channel_name

        self.client: Optional[genai.Client] = None
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                print(f"[GeminiLiveAudioSession:{self.channel_name}] Init client error: {e}")

        self.is_running = False
        self.is_turn_complete = False
        self.last_audio_received_time = 0.0
        self.measured_latency_ms = None
        self._captured_at = None
        self._sent_at = None
        self._first_audio = False
        self._awaiting_response = False
        self._tasks = set()
        self._runner = None
        self._finished = asyncio.Event()
        self._turn_done = asyncio.Event()
        self._resampler = None
        self._source_rate = None
        self._pending_timing = None
        self.send_to_audio = LatencyStats()
        self.capture_to_audio = LatencyStats()
        self.capture_to_playback = LatencyStats()
        self.receive_to_playback = LatencyStats()
        self.send_wait = LatencyStats()
        self.simulated = False
        self._failed = False
        self._has_audio_input = False

        # Callbacks for audio, text, telemetry and completion
        self.on_audio_chunk: Optional[Callable[[np.ndarray, Optional[PlaybackTiming]], None]] = None
        self.on_stt_result: Optional[Callable[[str, bool], None]] = None
        self.on_translated_result: Optional[Callable[[str], None]] = None
        self.on_error: Optional[Callable[[str, str], None]] = None
        self.on_interrupt: Optional[Callable[[], None]] = None
        self.on_turn_complete: Optional[Callable[[], None]] = None

    def set_api_key(self, api_key: str) -> None:
        """Update Gemini API Key dynamically."""
        self._finished.clear()
        self.api_key = api_key.strip()
        if self.api_key:
            try:
                self.client = genai.Client(api_key=self.api_key)
            except Exception as e:
                self._report_error(f"Помилка ініціалізації клієнта з ключем: {e}", "KEY_ERROR")
        else:
            self.client = None

    def set_voice(self, voice_name: str) -> None:
        """Update voice name for speech synthesis when not running."""
        if self.is_running:
            print(f"[GeminiLiveAudioSession:{self.channel_name}] Warning: Voice change ignored during active session.")
            return
        if voice_name in VALID_VOICE_NAMES:
            self.voice_name = voice_name
        else:
            print(f"[GeminiLiveAudioSession:{self.channel_name}] Warning: Unknown voice '{voice_name}', ignoring.")

    def _report_error(self, message: str, error_type: str = "API_ERROR") -> None:
        """Log error and notify UI callback."""
        print(f"[GeminiLive:{self.channel_name} ERROR] ({error_type}) {message}")
        if error_type != "NO_API_KEY":
            self._failed = True
        if self.on_error:
            self.on_error(message, error_type)

    def metrics(self):
        return {"send_to_audio": self.send_to_audio.snapshot(),
                "capture_to_audio": self.capture_to_audio.snapshot(),
                "capture_to_playback_estimate": self.capture_to_playback.snapshot(),
                "receive_to_playback_estimate": self.receive_to_playback.snapshot(),
                "send_wait": self.send_wait.snapshot(), "simulated": self.simulated}

    def mark_playback(self, timing: PlaybackTiming, submitted_at: float, output_latency: float):
        # Driver latency is an estimate, not an acoustic loopback measurement.
        estimated_at = submitted_at + output_latency
        self.capture_to_playback.add((estimated_at - timing.captured_at) * 1000)
        self.receive_to_playback.add((estimated_at - timing.received_at) * 1000)

    async def finish(self, input_queue, timeout: float = 10.0):
        if self._finished.is_set():
            return not self._failed
        try:
            await asyncio.wait_for(input_queue.put(None), timeout)
            await asyncio.wait_for(self._finished.wait(), timeout)
        except asyncio.TimeoutError:
            self._report_error("Не отримано завершення перекладу до таймауту.", "DRAIN_TIMEOUT")
            self.stop()
            await self._finished.wait()
        return not self._failed

    def _emit_audio(self, raw: bytes, mime_type: str):
        match = re.search(r"rate=(\d+)", mime_type)
        source_rate = int(match.group(1)) if match else 24000
        if self._resampler is None:
            self._source_rate = source_rate
            self._resampler = StreamingResampler(source_rate, self.output_sample_rate)
        elif source_rate != self._source_rate:
            raise ValueError("Gemini changed PCM sample rate within a turn")
        now = time.monotonic()
        self.last_audio_received_time = now
        if not self._first_audio:
            if self._captured_at is not None and self._sent_at is not None:
                self.measured_latency_ms = round((now - self._sent_at) * 1000)
                self.send_to_audio.add((now - self._sent_at) * 1000)
                self.capture_to_audio.add((now - self._captured_at) * 1000)
                self._pending_timing = PlaybackTiming(self._captured_at, now)
            self._first_audio = True
        samples = self._resampler.process(np.frombuffer(raw, dtype="<i2"))
        if self.on_audio_chunk and len(samples):
            self.on_audio_chunk(samples, self._pending_timing)
            self._pending_timing = None

    def _complete_turn(self):
        if self._resampler is not None:
            tail = self._resampler.process(np.empty(0, dtype=np.int16), last=True)
            if self.on_audio_chunk and len(tail):
                self.on_audio_chunk(tail, self._pending_timing)
        self._resampler = None
        self._source_rate = None
        self._pending_timing = None
        self.is_turn_complete = True
        self._awaiting_response = False
        self._turn_done.set()
        self._captured_at = self._sent_at = None
        self._first_audio = False
        if self.on_turn_complete:
            self.on_turn_complete()

    async def run(self, input_queue: asyncio.Queue) -> None:
        self._runner = asyncio.current_task()
        self._finished.clear()
        try:
            await self._run(input_queue)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._report_error(str(exc), "SESSION_ERROR")
        finally:
            self.is_running = False
            self._finished.set()

    async def _run(
        self,
        input_queue: asyncio.Queue,
    ) -> None:
        """Runs bidirectional streaming audio translation session with Gemini Live API."""
        self.is_running = True
        self._finished.clear()
        self.is_turn_complete = False
        self._turn_done.clear()
        self._captured_at = self._sent_at = None
        self._first_audio = self._awaiting_response = False
        self._failed = self._has_audio_input = False
        self._resampler = None
        self._pending_timing = None
        self.measured_latency_ms = None
        self.send_to_audio = LatencyStats()
        self.capture_to_audio = LatencyStats()
        self.capture_to_playback = LatencyStats()
        self.receive_to_playback = LatencyStats()
        self.send_wait = LatencyStats()
        self.simulated = not self.client or not self.api_key
        if self.simulated:
            self._report_error("Gemini API ключ не вказано. Демонстраційний режим, метрики перекладу недоступні.", "NO_API_KEY")
            try:
                await self._run_simulation(input_queue)
            finally:
                self.is_running = False
                self._finished.set()
            return

        # Ensure voice name is strictly pinned to a valid prebuilt voice
        if self.voice_name not in VALID_VOICE_NAMES:
            self.voice_name = "Aoede" if self.channel_name == "incoming" else "Puck"

        speech_config = types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice_name)
            )
        )

        if self.channel_name == "outgoing":
            system_prompt = (
                f"You are a dedicated real-time simultaneous voice interpreter translating spoken Ukrainian into {self.target_lang}. "
                f"Translate immediately and continuously as the user speaks, phrase-by-phrase, with minimal latency. "
                f"Do not wait for full sentences or long pauses before starting speech synthesis. "
                f"You MUST strictly maintain the assigned speaker voice '{self.voice_name}' across all speech turns, sentences, and pauses. "
                f"Never switch voices, alter timbre, change pitch, or change gender after pauses or silence. "
                f"Only synthesize accurate speech in {self.target_lang}. Do not produce meta-commentary, explanations, or conversational filler."
            )
        else:
            system_prompt = (
                f"You are a dedicated real-time simultaneous voice interpreter translating incoming speech into Ukrainian (uk). "
                f"Translate immediately and continuously as the speaker speaks, phrase-by-phrase, with minimal latency. "
                f"Do not wait for full sentences or long pauses before starting speech synthesis. "
                f"You MUST strictly maintain the assigned speaker voice '{self.voice_name}' across all speech turns, sentences, and pauses. "
                f"Never switch voices, alter timbre, change pitch, or change gender after pauses or silence. "
                f"Only synthesize accurate, fluent speech in Ukrainian. Do not produce meta-commentary, explanations, or conversational filler."
            )

        system_instruction = types.Content(
            parts=[types.Part.from_text(text=system_prompt)]
        )

        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            speech_config=speech_config,
            temperature=0.0,
            system_instruction=system_instruction,
            translation_config=types.TranslationConfig(
                target_language_code=self.target_lang,
                echo_target_language=True,
            ),
            input_audio_transcription=types.AudioTranscriptionConfig(),
            output_audio_transcription=types.AudioTranscriptionConfig(),
        )

        try:
            async with self.client.aio.live.connect(model="gemini-3.5-live-translate-preview", config=config) as session:
                async def send_audio_worker():
                    while self.is_running:
                        chunk = await input_queue.get()
                        if chunk is None:
                            # A completion from an earlier reply must not authorize
                            # cancelling a final reply that is still in flight.
                            self._turn_done.clear()
                            await session.send_realtime_input(audio_stream_end=True)
                            return
                        if not isinstance(chunk, CapturedAudio):
                            raise TypeError("Audio input must include a monotonic capture timestamp")
                        now = time.monotonic()
                        self.send_wait.add((now - chunk.captured_at) * 1000)
                        if now - chunk.captured_at > 5.0:
                            raise RuntimeError("Аудіо відстає більш ніж на 5 с; сесію зупинено без прихованої втрати мовлення.")
                        pcm = np.frombuffer(chunk.data, dtype="<i2").astype(np.float32)
                        # Quiet speech also needs a final response. The level gate below
                        # controls timing estimates only, never whether input is drained.
                        if np.any(pcm):
                            self._has_audio_input = True
                            self._awaiting_response = True
                            self.is_turn_complete = False
                            self._turn_done.clear()
                        # Only anchor an estimate at voiced input; silence must not inflate latency.
                        # This is a level gate for metrics, not a VAD that discards input audio.
                        if len(pcm) and np.sqrt(np.mean(pcm * pcm)) > 300:
                            if self._captured_at is None:
                                self._captured_at = chunk.captured_at
                                self._sent_at = now
                        await session.send_realtime_input(audio=types.Blob(data=chunk.data, mime_type=f"audio/pcm;rate={self.sample_rate}"))

                async def receive_audio_worker():
                    while self.is_running:
                        received = False
                        completed = False
                        last_content = None
                        async for response in session.receive():
                            received = True
                            content = response.server_content
                            last_content = content
                            if not content:
                                continue
                            if content.interrupted:
                                # Discard only this channel's interrupted output and filter state.
                                self._resampler = None
                                self._source_rate = None
                                self._pending_timing = None
                                self._first_audio = False
                                if self.on_interrupt:
                                    self.on_interrupt()
                                continue
                            if content.model_turn:
                                for part in content.model_turn.parts or []:
                                    if part.inline_data and part.inline_data.data:
                                        self._emit_audio(part.inline_data.data, part.inline_data.mime_type or "audio/pcm;rate=24000")
                            if content.input_transcription and content.input_transcription.text and self.on_stt_result:
                                self.on_stt_result(content.input_transcription.text, True)
                            if content.output_transcription and content.output_transcription.text and self.on_translated_result:
                                self.on_translated_result(content.output_transcription.text)
                            if content.turn_complete:
                                self._complete_turn()
                                completed = True
                        if not received:
                            raise ConnectionError("Gemini closed the audio receive stream")
                        if not completed and getattr(last_content, "interaction_status", None) == types.InteractionStatus.IDLE:
                            self._complete_turn()
                        # SDK receive() yields one completed interaction. Read the next one.

                sender = asyncio.create_task(send_audio_worker())
                receiver = asyncio.create_task(receive_audio_worker())
                self._tasks = {sender, receiver}
                done, _ = await asyncio.wait(self._tasks, return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    await receiver  # Propagate failure and cancel the sender in finally.
                    raise ConnectionError("Gemini receiver stopped unexpectedly")
                await sender
                if self._has_audio_input:
                    turn_wait = asyncio.create_task(self._turn_done.wait())
                    self._tasks.add(turn_wait)
                    done, _ = await asyncio.wait({turn_wait, receiver}, timeout=10.0, return_when=asyncio.FIRST_COMPLETED)
                    if receiver in done:
                        await receiver
                    if turn_wait not in done:
                        raise TimeoutError("Gemini did not finish the final speech turn")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if self.is_running:
                # Network failures (including 1006/1011/ping timeouts) are actionable.
                message = str(exc)
                if isinstance(exc, APIError):
                    message = f"Gemini API [{exc.code}]: {exc.message}"
                self._report_error(message, "SESSION_ERROR")
        finally:
            self.is_running = False
            for task in self._tasks:
                if not task.done():
                    task.cancel()
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            self._tasks.clear()
            self._finished.set()

    async def _run_simulation(self, input_queue):
        last_tone = 0.0
        while self.is_running:
            chunk = await input_queue.get()
            if chunk is None:
                self._complete_turn()
                return
            now = time.monotonic()
            if now - last_tone < 2.0:
                continue
            last_tone = now
            if self.on_translated_result:
                self.on_translated_result("Демонстраційний сигнал — це не переклад Gemini.")
            t = np.arange(round(self.output_sample_rate * 0.4)) / self.output_sample_rate
            tone = (np.sin(2 * np.pi * 440 * t) * 0.25 * 32767).astype(np.int16)
            if self.on_audio_chunk:
                self.on_audio_chunk(tone, None)
            if self.on_turn_complete:
                self.on_turn_complete()

    def stop(self):
        """Abort immediately; normal stop uses finish() to preserve the final speech turn."""
        self.is_running = False
        for task in self._tasks:
            task.cancel()
        if self._runner is not None and not self._runner.done() and self._runner is not asyncio.current_task():
            self._runner.cancel()
