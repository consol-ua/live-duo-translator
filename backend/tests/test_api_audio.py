import asyncio
import importlib
import logging
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave

import httpx
import numpy as np

from test_audio_pipeline import FakeStream


class ApiAudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.streams = []
        def open_stream(**kwargs):
            stream = FakeStream()
            self.streams.append(stream)
            return stream
        self.pa = SimpleNamespace(open=open_stream, terminate=lambda: None, get_device_count=lambda: 0)
        # Avoid hardware and writing to the tracked application log when importing main.
        with patch('audio_engine.pyaudio.PyAudio', return_value=self.pa), patch(
            'logging.handlers.RotatingFileHandler', return_value=logging.NullHandler()
        ):
            self.main = importlib.import_module('main')
        from audio_engine import DualChannelAudioEngine
        with patch('audio_engine.pyaudio.PyAudio', return_value=self.pa):
            self.main.audio_engine = DualChannelAudioEngine()
        self.main.state = self.main.AppState()
        self.main.session_stop_lock = asyncio.Lock()
        self.main.active_call_tasks.clear()
        self.main.active_dubbing_tasks.clear()
        self.main.active_sample_tasks.clear()
        self.main.active_mic_test_tasks.clear()
        from ai_pipeline import GeminiLiveAudioSession
        self.main.outgoing_ai = GeminiLiveAudioSession(api_key='')
        self.main.incoming_ai = GeminiLiveAudioSession(api_key='')
        engine = self.main.audio_engine
        self.main.outgoing_ai.on_audio_chunk = engine.push_outgoing_tts_chunk
        self.main.incoming_ai.on_audio_chunk = engine.push_incoming_tts_chunk
        self.main.outgoing_ai.on_interrupt = engine.outgoing_playback_buffer.clear
        self.main.incoming_ai.on_interrupt = engine.incoming_playback_buffer.clear
        self.main.outgoing_ai.on_turn_complete = engine.outgoing_playback_buffer.flush
        self.main.incoming_ai.on_turn_complete = engine.incoming_playback_buffer.flush
        self.main.outgoing_ai.on_error = self.main.on_ai_error
        self.main.incoming_ai.on_error = self.main.on_ai_error
        engine.on_audio_error = self.main.on_ai_error
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.main.app), base_url='http://test')
        self.temp = tempfile.TemporaryDirectory()
        engine.mic_test_file_path = Path(self.temp.name) / 'mic.wav'

    async def asyncTearDown(self):
        self.main.outgoing_ai.stop()
        self.main.incoming_ai.stop()
        for tasks in (self.main.active_call_tasks, self.main.active_dubbing_tasks,
                      self.main.active_sample_tasks, self.main.active_mic_test_tasks):
            await self.main.cancel_tasks(tasks)
        await self.main.audio_engine._finish_playback(timeout=.1)
        await asyncio.to_thread(self.main.audio_engine.terminate)
        await self.client.aclose()
        self.temp.cleanup()

    async def test_metrics_and_invalid_buffer_setting(self):
        response = await self.client.get('/metrics')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['outgoing_latency']['send_to_audio']['p50_ms'])
        self.assertEqual((await self.client.post('/jitter_buffer', json={'jitter_buffer_ms': 301})).status_code, 422)
        self.assertEqual((await self.client.post('/jitter_buffer', json={'jitter_buffer_ms': 150})).json(), {'jitter_buffer_ms': 150})
        self.assertEqual(self.main.audio_engine.outgoing_playback_buffer.jitter_buffer_ms, 150)

    async def test_call_start_stop_restart_and_channel_isolation(self):
        for _ in range(2):
            response = await self.client.post('/call/start', json={'call_virtual_mic_index': 1, 'call_input_index': 2})
            self.assertEqual(response.status_code, 200)
            await asyncio.sleep(.03)
            engine = self.main.audio_engine
            engine.incoming_playback_buffer.push(np.ones(2400, dtype=np.int16))
            self.main.outgoing_ai.on_interrupt()
            self.assertTrue(engine.incoming_playback_buffer.has_audio())
            response = await asyncio.wait_for(self.client.post('/call/stop'), 2)
            self.assertEqual(response.status_code, 200)
            self.assertFalse(self.main.state.is_stopping)
            self.assertFalse(engine.incoming_playback_buffer.has_audio())
            self.assertEqual(self.main.active_call_tasks, set())
            self.assertTrue(all(stream.closed for stream in self.streams))
        self.assertEqual((await self.client.post('/call/stop')).json()['status'], 'not_running')

    async def test_mic_result_does_not_invent_latency(self):
        self.assertEqual((await self.client.post('/test_mic/start', json={})).status_code, 200)
        await asyncio.sleep(.02)
        response = await asyncio.wait_for(self.client.post('/test_mic/stop'), 2)
        result = response.json()
        self.assertIsNone(result['latency_ms'])
        self.assertTrue(result['metrics']['simulated'])
        self.assertTrue(result['has_audio'])
        with wave.open(str(self.main.audio_engine.mic_test_file_path)) as wf:
            self.assertEqual(wf.getframerate(), 24000)

    async def test_natural_sample_completion_drains_playback(self):
        path = Path(self.temp.name) / 'test.wav'
        with wave.open(str(path), 'wb') as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(np.ones(1600, dtype=np.int16).tobytes())
        with patch.object(self.main, 'SAMPLES_DIR', Path(self.temp.name)), patch.object(
            self.main, 'AVAILABLE_SAMPLES', [{'id': 'test', 'filename': 'test.wav', 'title': 'test'}]
        ):
            response = await self.client.post('/samples/start', json={'sample_id': 'test'})
            self.assertEqual(response.status_code, 200)
            async with asyncio.timeout(2):
                while self.main.state.is_testing_active:
                    await asyncio.sleep(.01)
        self.assertFalse(self.main.audio_engine.incoming_playback_buffer.has_audio())
        self.assertTrue(all(stream.closed for stream in self.streams))

    async def test_partial_device_open_failure_closes_already_opened_stream(self):
        original_open = self.pa.open
        def fail_second(**kwargs):
            if self.streams:
                raise OSError('unsupported device format')
            return original_open(**kwargs)
        self.pa.open = fail_second
        response = await self.client.post('/call/start', json={'call_virtual_mic_index': 1})
        self.assertEqual(response.status_code, 500)
        self.assertTrue(self.streams[0].closed)
        self.assertFalse(self.main.state.is_call_active)


if __name__ == '__main__':
    unittest.main()
