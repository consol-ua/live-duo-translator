import asyncio
from collections import deque
import logging
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import numpy as np
from google.genai import live, types

from ai_pipeline import GeminiLiveAudioSession
from audio_engine import AudioStreamBuffer, DualChannelAudioEngine
from audio_support import AudioInputQueue, AudioOverload, CapturedAudio, PlaybackTiming, StreamingResampler


class BufferTests(unittest.TestCase):
    def test_requested_jitter_threshold_and_short_final_turn(self):
        for target in (40, 75, 150, 300):
            with self.subTest(target=target):
                buffer = AudioStreamBuffer(16000, target)
                count = target * 16
                buffer.push(np.ones(count - 1, dtype=np.int16))
                self.assertIsNone(buffer.pop(320))
                buffer.push(np.ones(1, dtype=np.int16))
                self.assertIsNotNone(buffer.pop(320))
        buffer = AudioStreamBuffer(16000, 300)
        expected = np.arange(1, 101, dtype=np.int16)
        buffer.push(expected)
        buffer.flush()
        np.testing.assert_array_equal(buffer.pop(320)[:100], expected)

    def test_packet_boundaries_preserve_every_sample(self):
        buffer = AudioStreamBuffer(24000)
        expected = np.arange(1, 5000, dtype=np.int16)
        for chunk in np.array_split(expected, 73):
            buffer.push(chunk)
        buffer.flush()
        output = []
        while buffer.has_audio():
            output.append(buffer.pop(480))
        actual = np.concatenate(output)
        np.testing.assert_array_equal(actual[:len(expected)], expected)
        self.assertTrue(np.all(actual[len(expected):] == 0))
        self.assertEqual(buffer.snapshot()['underruns'], 0)

    def test_recovery_adapts_without_repeatedly_counting_idle(self):
        buffer = AudioStreamBuffer(16000, 40)
        buffer.push(np.ones(640, dtype=np.int16))
        buffer.pop(640)
        buffer.pop(320)
        self.assertEqual(buffer.snapshot()['underruns'], 1)
        self.assertEqual(buffer.snapshot()['target_ms'], 60)
        buffer.pop(320)
        self.assertEqual(buffer.snapshot()['underruns'], 1)
        buffer.push(np.ones(640, dtype=np.int16))
        self.assertIsNone(buffer.pop(320))
        buffer.flush()
        self.assertIsNotNone(buffer.pop(320))
        buffer.clear()
        self.assertFalse(buffer.has_audio())

    def test_queue_overload_is_explicit_and_preserves_pending_frames(self):
        queue = AudioInputQueue(16000, 320, max_delay_ms=40)
        first = np.ones(320, dtype=np.int16).tobytes()
        queue.capture(first, time.monotonic())
        queue.capture(first, time.monotonic())
        with self.assertRaises(AudioOverload):
            queue.capture(first, time.monotonic())
        self.assertEqual(queue.snapshot()['queued_ms'], 40)
        self.assertEqual(queue.get_nowait().data, first)
        self.assertEqual(queue.get_nowait().data, first)
        self.assertEqual(queue.snapshot()['queued_ms'], 0)

    def test_playback_overload_does_not_silently_remove_samples(self):
        buffer = AudioStreamBuffer(1000)
        buffer.push(np.ones(15000, dtype=np.int16))
        with self.assertRaises(AudioOverload):
            buffer.push(np.ones(1, dtype=np.int16))
        self.assertEqual(buffer.snapshot()['queued_ms'], 15000)

    def test_stateful_resampling_filters_alias_and_preserves_packet_phase(self):
        t = np.arange(24000) / 24000
        source = (12000 * np.sin(2 * np.pi * 10000 * t)).astype(np.int16)
        stream = StreamingResampler(24000, 16000)
        blocks = [stream.process(block) for block in np.array_split(source, 97)]
        blocks.append(stream.process(np.empty(0, dtype=np.int16), last=True))
        actual = np.concatenate(blocks)
        reference = StreamingResampler(24000, 16000).process(source, last=True)
        self.assertEqual(len(actual), 16000)
        # libsoxr dithers int16 output: independent streams can differ by two LSBs.
        np.testing.assert_allclose(actual, reference, atol=2)
        self.assertLess(np.sqrt(np.mean(actual[1000:-1000].astype(float) ** 2)), 2)
        source = (12000 * np.sin(2 * np.pi * 1000 * t)).astype(np.int16)
        converted = StreamingResampler(24000, 16000).process(source, last=True)
        rms = np.sqrt(np.mean(converted[1000:-1000].astype(float) ** 2))
        self.assertAlmostEqual(rms, 12000 / np.sqrt(2), delta=10)


class FakeLiveSession:
    # Exercise the actual SDK's one-interaction iterator contract.
    receive = live.AsyncSession.receive

    def __init__(self):
        self.responses = asyncio.Queue()
        self.sent = asyncio.Queue()
        self.ended = asyncio.Event()
        self.acknowledge_end = False

    async def _receive(self):
        response = await self.responses.get()
        if isinstance(response, Exception):
            raise response
        return response

    async def send_realtime_input(self, **kwargs):
        if kwargs.get('audio_stream_end'):
            self.ended.set()
            if self.acknowledge_end:
                await self.responses.put(types.LiveServerMessage(server_content=types.LiveServerContent(
                    interaction_status=types.InteractionStatus.IDLE)))
        else:
            await self.sent.put(kwargs['audio'].data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def respond(self, samples, text='translated'):
        await self.responses.put(types.LiveServerMessage(server_content=types.LiveServerContent(
            model_turn=types.Content(parts=[types.Part(inline_data=types.Blob(
                data=samples.tobytes(), mime_type='audio/pcm;rate=24000'))]))))
        await self.responses.put(types.LiveServerMessage(server_content=types.LiveServerContent(
            turn_complete=True, output_transcription=types.Transcription(text=text))))


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.session = GeminiLiveAudioSession(api_key='')
        self.fake = FakeLiveSession()
        self.session.api_key = 'test-placeholder'
        self.session.client = SimpleNamespace(aio=SimpleNamespace(live=SimpleNamespace(connect=lambda **kwargs: self.fake)))
        self.queue = AudioInputQueue(16000, 320)
        self.errors = []
        self.audio = []
        self.texts = []
        self.session.on_error = lambda *error: self.errors.append(error)
        self.session.on_audio_chunk = lambda samples, timing: self.audio.append((samples, timing))
        self.session.on_translated_result = self.texts.append
        self.task = asyncio.create_task(self.session.run(self.queue))
        await asyncio.sleep(0)

    async def asyncTearDown(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)

    async def speak(self, level=1000):
        self.queue.capture(np.full(320, level, dtype=np.int16).tobytes(), time.monotonic() - .02)
        return await asyncio.wait_for(self.fake.sent.get(), 1)

    async def wait_for_turns(self, count):
        async with asyncio.timeout(1):
            while len(self.texts) < count:
                await asyncio.sleep(0)

    async def test_multiple_turns_and_native_24k_audio(self):
        expected = np.arange(1, 2401, dtype=np.int16)
        for turn in range(3):
            await self.speak()
            await self.fake.respond(expected, f'turn {turn}')
            await self.wait_for_turns(turn + 1)
        self.assertEqual(self.texts, ['turn 0', 'turn 1', 'turn 2'])
        self.assertFalse(self.task.done())
        for samples, timing in self.audio:
            np.testing.assert_array_equal(samples, expected)
            self.assertIsNotNone(timing)
        self.fake.acknowledge_end = True
        await self.session.finish(self.queue, timeout=1)
        await self.task
        self.assertTrue(self.fake.ended.is_set())
        self.assertEqual(self.session.metrics()['send_to_audio']['samples'], 3)
        self.assertEqual(self.errors, [])

    async def test_stop_waits_for_final_audio_after_input_end(self):
        # Below the telemetry gate: quiet speech must still be fully drained.
        await self.speak(level=50)
        finish = asyncio.create_task(self.session.finish(self.queue, timeout=1))
        await self.fake.ended.wait()
        self.assertFalse(finish.done())
        expected = np.ones(240, dtype=np.int16)
        await self.fake.respond(expected)
        await finish
        np.testing.assert_array_equal(self.audio[0][0], expected)
        self.assertEqual(self.errors, [])

    async def test_receiver_failure_stops_sender_and_is_reported(self):
        await self.fake.responses.put(ConnectionError('1006 no close frame received'))
        await asyncio.wait_for(self.task, 1)
        self.assertFalse(self.session.is_running)
        self.assertEqual(self.session._tasks, set())
        self.assertEqual(self.errors[0][1], 'SESSION_ERROR')

    async def test_interaction_idle_also_finishes_a_turn(self):
        await self.speak()
        await self.fake.responses.put(types.LiveServerMessage(server_content=types.LiveServerContent(
            interaction_status=types.InteractionStatus.IDLE,
            output_transcription=types.Transcription(text='idle'))))
        await self.wait_for_turns(1)
        self.fake.acknowledge_end = True
        await self.session.finish(self.queue, timeout=1)
        self.assertTrue(self.session.is_turn_complete)
        self.assertEqual(self.errors, [])

    async def test_prior_turn_completion_cannot_cut_off_a_late_final_reply(self):
        await self.speak()
        await self.fake.respond(np.ones(240, dtype=np.int16), 'first')
        await self.wait_for_turns(1)
        finish = asyncio.create_task(self.session.finish(self.queue, timeout=1))
        await self.fake.ended.wait()
        self.assertFalse(finish.done())
        await self.fake.respond(np.full(240, 2, dtype=np.int16), 'final')
        self.assertTrue(await finish)
        self.assertEqual(self.texts, ['first', 'final'])

    async def test_finish_timeout_cancels_a_stalled_connection(self):
        self.task.cancel()
        await asyncio.gather(self.task, return_exceptions=True)
        entered = asyncio.Event()
        class StalledConnection:
            async def __aenter__(self):
                entered.set()
                await asyncio.Event().wait()
            async def __aexit__(self, *args):
                pass
        self.session.client.aio.live.connect = lambda **kwargs: StalledConnection()
        self.task = asyncio.create_task(self.session.run(self.queue))
        await entered.wait()
        await asyncio.wait_for(self.session.finish(self.queue, timeout=.02), .5)
        self.assertTrue(self.task.cancelled())
        self.assertEqual(self.errors[-1][1], 'DRAIN_TIMEOUT')

    async def test_unknown_latency_is_not_fabricated(self):
        await self.speak(level=0)
        await self.session.finish(self.queue, timeout=1)
        self.assertIsNone(self.session.measured_latency_ms)
        self.assertEqual(self.session.metrics()['send_to_audio']['samples'], 0)

    async def test_stale_input_stops_instead_of_translating_old_speech(self):
        self.queue.capture(np.ones(320, dtype=np.int16).tobytes(), time.monotonic() - 6)
        await asyncio.wait_for(self.task, 1)
        self.assertTrue(self.fake.sent.empty())
        self.assertEqual(self.errors[0][1], 'SESSION_ERROR')

    async def test_cancellation_leaves_no_workers(self):
        await self.speak()
        self.task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.task
        self.assertEqual(self.session._tasks, set())
        self.assertTrue(self.session._finished.is_set())


class FakeStream:
    def __init__(self, read_gate=None):
        self.writes = []
        self.closed = False
        self.read_gate = read_gate
        self.entered_read = threading.Event()

    def read(self, count, **kwargs):
        self.entered_read.set()
        if self.read_gate:
            self.read_gate.wait(1)
        else:
            time.sleep(.005)
        return np.ones(count, dtype=np.int16).tobytes()

    def write(self, data, **kwargs):
        time.sleep(.005)
        self.writes.append(np.frombuffer(data, dtype=np.int16).copy())

    def is_active(self):
        return not self.closed

    def stop_stream(self):
        pass

    def close(self):
        self.closed = True

    def get_output_latency(self):
        return .01


class EngineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.streams = []
        def open_stream(**kwargs):
            stream = FakeStream()
            self.streams.append(stream)
            return stream
        self.pa = SimpleNamespace(open=open_stream, terminate=lambda: None)
        with patch('audio_engine.pyaudio.PyAudio', return_value=self.pa):
            self.engine = DualChannelAudioEngine()

    async def asyncTearDown(self):
        self.engine._outgoing_playback_running = False
        self.engine._incoming_playback_running = False
        self.engine.is_call_running = False
        for task in self.engine._playback_tasks:
            task.cancel()
        await asyncio.gather(*list(self.engine._playback_tasks), return_exceptions=True)
        await asyncio.to_thread(self.engine.terminate)

    async def test_playback_continues_while_capture_blocks_and_drains_before_close(self):
        self.engine.start_call(call_virtual_mic_index=1)
        gate = threading.Event()
        self.engine.my_mic_stream.read_gate = gate
        capture = asyncio.create_task(self.engine.outgoing_process_loop())
        self.engine.start_playback(outgoing=True)
        expected = np.arange(1, 1601, dtype=np.int16)
        self.engine.push_outgoing_tts_chunk(expected)
        self.engine.outgoing_playback_buffer.flush()
        output = self.engine.call_virtual_mic_stream
        try:
            async with asyncio.timeout(1):
                while not output.writes or not any(np.any(block) for block in output.writes):
                    await asyncio.sleep(.005)
            self.assertTrue(self.engine.my_mic_stream.entered_read.is_set())
        finally:
            self.engine.is_call_running = False
            gate.set()
            await capture
        self.assertEqual(self.engine.outgoing_input_queue.queued_samples, self.engine.chunk_size)
        await self.engine.graceful_stop_call(timeout=1)
        samples = np.concatenate(output.writes)
        np.testing.assert_array_equal(samples[samples != 0], expected)
        self.assertTrue(output.closed)

    async def test_mic_test_keeps_final_chunks_after_capture_stops(self):
        self.engine.start_mic_test()
        self.engine.is_mic_test_running = False
        expected = np.arange(1, 2401, dtype=np.int16)
        self.engine.push_outgoing_tts_chunk(expected)
        with tempfile.TemporaryDirectory() as temp:
            self.engine.mic_test_file_path = Path(temp) / 'result.wav'
            result = self.engine.stop_mic_test()
            with wave.open(result) as wf:
                self.assertEqual(wf.getframerate(), 24000)
                np.testing.assert_array_equal(np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16), expected)
        self.assertFalse(self.engine.outgoing_playback_buffer.has_audio())

    async def test_sample_feeder_has_one_real_time_clock(self):
        self.engine.is_sample_running = True
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'sample.wav'
            with wave.open(str(path), 'wb') as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(16000)
                wf.writeframes(np.ones(3200, dtype=np.int16).tobytes())
            started = time.monotonic()
            await self.engine.sample_playback_loop(str(path))
            elapsed = time.monotonic() - started
        self.assertGreaterEqual(elapsed, .19)
        self.assertLess(elapsed, .35)
        self.assertEqual(self.engine.incoming_input_queue.queued_samples, 3200)


if __name__ == '__main__':
    unittest.main()
