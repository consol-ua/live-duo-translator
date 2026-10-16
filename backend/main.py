import os
import asyncio
import logging
from logging.handlers import RotatingFileHandler
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional, Set, List, Dict
from datetime import datetime
from dotenv import load_dotenv

# Load .env file from backend or root directory
env_path = Path(__file__).parent / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from audio_engine import DualChannelAudioEngine
from ai_pipeline import GeminiLiveAudioSession

# Setup Logging
LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = LOGS_DIR / "app.log"

logger = logging.getLogger("gemini_translator")
logger.setLevel(logging.INFO)
file_handler = RotatingFileHandler(LOG_FILE, maxBytes=5 * 1024 * 1024, backupCount=3)
file_handler.setFormatter(
    logging.Formatter("[%(asctime)s] [%(levelname)s] [%(name)s]: %(message)s")
)
logger.addHandler(file_handler)

from collections import deque

MAX_IN_MEMORY_LOGS = 150
in_memory_logs: deque[Dict[str, str]] = deque(maxlen=MAX_IN_MEMORY_LOGS)

def add_log_entry(level: str, message: str, source: str = "system") -> None:
    now_str = datetime.now().strftime("%H:%M:%S")
    entry = {
        "timestamp": now_str,
        "level": level.upper(),
        "message": message,
        "source": source,
    }
    in_memory_logs.append(entry)

    if level.upper() == "ERROR":
        logger.error(f"[{source}] {message}")
    elif level.upper() == "WARN":
        logger.warning(f"[{source}] {message}")
    else:
        logger.info(f"[{source}] {message}")

add_log_entry("INFO", "Сервер перекладача успішно ініціалізовано.", "system")


@asynccontextmanager
async def lifespan(app: FastAPI):
    broadcast_task = asyncio.create_task(state_broadcast_loop())
    try:
        yield
    finally:
        broadcast_task.cancel()
        try:
            await broadcast_task
        except asyncio.CancelledError:
            pass
        audio_engine.is_call_running = audio_engine.is_dubbing_running = False
        audio_engine.is_sample_running = audio_engine.is_mic_test_running = False
        outgoing_ai.stop()
        incoming_ai.stop()
        for tasks in (active_call_tasks, active_dubbing_tasks, active_sample_tasks, active_mic_test_tasks):
            await cancel_tasks(tasks)
        audio_engine._outgoing_playback_running = audio_engine._incoming_playback_running = False
        playback_tasks = list(audio_engine._playback_tasks)
        for task in playback_tasks:
            task.cancel()
        await asyncio.gather(*playback_tasks, return_exceptions=True)
        await asyncio.to_thread(audio_engine.terminate)


app = FastAPI(
    title="Real-Time Call Translation & Voiceover Backend",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    # TODO: Restrict origins for production deployment
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global Audio Engine
audio_engine = DualChannelAudioEngine()

# Global AI Sessions
outgoing_ai = GeminiLiveAudioSession(
    api_key=os.environ.get("GEMINI_API_KEY", ""),
    target_lang="en",
    voice_name="Puck",
    channel_name="outgoing",
)
incoming_ai = GeminiLiveAudioSession(
    api_key=os.environ.get("GEMINI_API_KEY", ""),
    target_lang="uk",
    voice_name="Aoede",
    channel_name="incoming",
)

# Audio Queue routing
outgoing_ai.on_audio_chunk = audio_engine.push_outgoing_tts_chunk
incoming_ai.on_audio_chunk = audio_engine.push_incoming_tts_chunk

outgoing_ai.on_interrupt = audio_engine.outgoing_playback_buffer.clear
incoming_ai.on_interrupt = audio_engine.incoming_playback_buffer.clear

# Supported Neural Voices in Gemini Live
AVAILABLE_VOICES = [
    {"id": "Puck", "label": "Puck (Чоловічий / Енергійний, природний)", "gender": "male"},
    {"id": "Charon", "label": "Charon (Чоловічий / Впевнений, спокійний)", "gender": "male"},
    {"id": "Fenrir", "label": "Fenrir (Чоловічий / Низький тембр)", "gender": "male"},
    {"id": "Aoede", "label": "Aoede (Жіночий / Виразний, глибокий)", "gender": "female"},
    {"id": "Kore", "label": "Kore (Жіночий / Спокійний, м'який)", "gender": "female"},
]
VALID_VOICE_IDS = {v["id"] for v in AVAILABLE_VOICES}

# Request Models
class CallStartRequest(BaseModel):
    my_mic_index: Optional[int] = None
    call_virtual_mic_index: Optional[int] = None
    call_input_index: Optional[int] = None
    headphones_index: Optional[int] = None
    partner_lang: str = "en"
    outgoing_voice: str = "Puck"
    incoming_voice: str = "Aoede"
    ducking_factor: float = 0.2
    jitter_buffer_ms: int = Field(default=40, ge=30, le=300)
    api_key: Optional[str] = None

class SampleStartRequest(BaseModel):
    sample_id: str
    headphones_index: Optional[int] = None
    ducking_factor: float = 0.2
    jitter_buffer_ms: int = Field(default=40, ge=30, le=300)
    partner_lang: str = "en"
    voice_name: str = "Aoede"
    api_key: Optional[str] = None

class DubbingStartRequest(BaseModel):
    input_device_index: Optional[int] = None
    headphones_index: Optional[int] = None
    source_lang: str = "en"
    voice_name: str = "Aoede"
    ducking_factor: float = 0.2
    jitter_buffer_ms: int = Field(default=40, ge=30, le=300)
    api_key: Optional[str] = None

class MicTestStartRequest(BaseModel):
    mic_index: Optional[int] = None
    partner_lang: str = "en"
    voice_name: str = "Puck"
    api_key: Optional[str] = None

class DuckingRequest(BaseModel):
    ducking_factor: float

class JitterBufferRequest(BaseModel):
    jitter_buffer_ms: int = Field(ge=30, le=300)

class AppState:
    def __init__(self) -> None:
        self.is_call_active: bool = False
        self.is_dubbing_active: bool = False
        self.is_testing_active: bool = False
        self.is_mic_test_active: bool = False
        self.active_sample_id: Optional[str] = None
        self.partner_lang: str = "en"
        self.outgoing_voice: str = "Puck"
        self.incoming_voice: str = "Aoede"
        self.jitter_buffer_ms: int = 40
        self.last_error: Optional[str] = None
        self.mic_test_latency_ms: Optional[int] = None
        self.is_stopping: bool = False

        # Outgoing (My Voice: UA -> Target)
        self.outgoing_stt: str = ""
        self.outgoing_translation: str = ""
        self.outgoing_volume_db: float = -100.0
        self.outgoing_stt_history: List[Dict[str, str]] = []
        self.outgoing_trans_history: List[Dict[str, str]] = []

        # Incoming (Call / Sample: Target -> UA)
        self.incoming_stt: str = ""
        self.incoming_translation: str = ""
        self.incoming_volume_db: float = -100.0
        self.is_incoming_ducking: bool = False
        self.incoming_stt_history: List[Dict[str, str]] = []
        self.incoming_trans_history: List[Dict[str, str]] = []

state = AppState()

def update_transcript_history(
    history: List[Dict[str, str]],
    new_text: str,
    max_entries: int = 150
) -> None:
    text_clean = new_text.strip()
    if not text_clean:
        return
    now_str = datetime.now().strftime("%H:%M:%S")

    if not history:
        history.append({"timestamp": now_str, "text": text_clean})
    else:
        last_entry = history[-1]
        # If new_text is an extension or continuation of the last entry
        if text_clean.startswith(last_entry["text"]) or last_entry["text"].startswith(text_clean):
            last_entry["text"] = text_clean
        else:
            # New distinct sentence or speech turn
            history.append({"timestamp": now_str, "text": text_clean})

    if len(history) > max_entries:
        history.pop(0)

# WebSocket Manager
class ConnectionManager:
    def __init__(self) -> None:
        self.active_connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.active_connections.add(websocket)

    def disconnect(self, websocket: WebSocket) -> None:
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict) -> None:
        for connection in list(self.active_connections):
            try:
                await connection.send_json(message)
            except Exception:
                self.active_connections.discard(connection)

manager = ConnectionManager()

# Hook up Telemetry Callbacks
def on_outgoing_telemetry(db: float) -> None:
    state.outgoing_volume_db = db

def on_incoming_telemetry(db: float, is_ducking: bool) -> None:
    state.incoming_volume_db = db
    state.is_incoming_ducking = is_ducking

audio_engine.outgoing_telemetry_cb = on_outgoing_telemetry
audio_engine.incoming_telemetry_cb = on_incoming_telemetry

# Hook up AI STT & Translation Callbacks
def on_out_stt(text: str, is_final: bool) -> None:
    state.outgoing_stt = text
    if text:
        update_transcript_history(state.outgoing_stt_history, text)

def on_out_trans(text: str) -> None:
    state.outgoing_translation = text
    if text:
        update_transcript_history(state.outgoing_trans_history, text)

def on_in_stt(text: str, is_final: bool) -> None:
    state.incoming_stt = text
    if text:
        update_transcript_history(state.incoming_stt_history, text)

def on_in_trans(text: str) -> None:
    state.incoming_translation = text
    if text:
        update_transcript_history(state.incoming_trans_history, text)

def on_ai_error(error_msg: str, error_type: str) -> None:
    state.last_error = error_msg
    add_log_entry("ERROR", error_msg, f"GeminiAPI:{error_type}")
    if error_type != "NO_API_KEY" and not state.is_stopping:
        asyncio.create_task(stop_failed_session())

outgoing_ai.on_stt_result = on_out_stt
outgoing_ai.on_translated_result = on_out_trans
outgoing_ai.on_error = on_ai_error
outgoing_ai.on_turn_complete = lambda: audio_engine.outgoing_playback_buffer.flush()

incoming_ai.on_stt_result = on_in_stt
incoming_ai.on_translated_result = on_in_trans
incoming_ai.on_error = on_ai_error
incoming_ai.on_turn_complete = lambda: audio_engine.incoming_playback_buffer.flush()

audio_engine.on_audio_error = on_ai_error
audio_engine.on_outgoing_playback = outgoing_ai.mark_playback
audio_engine.on_incoming_playback = incoming_ai.mark_playback

def audio_metrics():
    return {**audio_engine.diagnostics(),
            "outgoing_latency": outgoing_ai.metrics(), "incoming_latency": incoming_ai.metrics()}

# Meters update at 10 Hz; audio never travels through this UI WebSocket.
async def state_broadcast_loop() -> None:
    while True:
        if manager.active_connections:
            payload = {
                "is_call_active": state.is_call_active,
                "is_dubbing_active": state.is_dubbing_active,
                "is_testing_active": state.is_testing_active,
                "is_mic_test_active": state.is_mic_test_active,
                "active_sample_id": state.active_sample_id,
                "partner_lang": state.partner_lang,
                "outgoing_voice": state.outgoing_voice,
                "incoming_voice": state.incoming_voice,
                "jitter_buffer_ms": state.jitter_buffer_ms,
                "mic_test_latency_ms": state.mic_test_latency_ms,
                "audio_metrics": audio_metrics(),
                "is_stopping": state.is_stopping,
                "last_error": state.last_error,
                "logs": list(in_memory_logs)[-40:],
                "outgoing": {
                    "stt_text": state.outgoing_stt,
                    "translated_text": state.outgoing_translation,
                    "stt_history": state.outgoing_stt_history,
                    "translated_history": state.outgoing_trans_history,
                    "volume_db": round(state.outgoing_volume_db, 1),
                },
                "incoming": {
                    "stt_text": state.incoming_stt,
                    "translated_text": state.incoming_translation,
                    "stt_history": state.incoming_stt_history,
                    "translated_history": state.incoming_trans_history,
                    "volume_db": round(state.incoming_volume_db, 1),
                    "is_ducking": state.is_incoming_ducking,
                },
            }
            await manager.broadcast(payload)
        await asyncio.sleep(0.1)

# Built-in Samples Metadata
SAMPLES_DIR = Path(__file__).parent / "samples"
AVAILABLE_SAMPLES = [
    {
        "id": "it_standup",
        "title": "IT Daily Standup",
        "category": "IT & Team",
        "description": "Daily status report: backend microservices, DB queries optimization, WebSocket integration.",
        "filename": "it_standup.wav",
        "transcript_original": "Hey team, good morning. Yesterday I finished refactoring the backend microservices and optimized several slow database queries. Today I'm focusing on the WebSocket telemetry integration and running end-to-end latency benchmarks. No blockers on my end.",
        "transcript_translated": "Всім привіт, доброго ранку. Вчора я завершив рефакторинг бекенд-мікросервісів та оптимізував кілька повільних запитів до бази даних. Сьогодні я зосереджуюсь на інтеграції телеметрії через WebSocket та тестуванні затримок. З мого боку блокерів немає.",
    },
    {
        "id": "tech_interview",
        "title": "System Architecture Interview",
        "category": "Job Interview",
        "description": "Technical question about high-load distributed streaming and geographic failover.",
        "filename": "tech_interview.wav",
        "transcript_original": "Could you explain your architectural approach to designing a high-load, low-latency audio streaming pipeline with multi-region failover and automatic jitter buffer synchronization?",
        "transcript_translated": "Чи могли б ви пояснити свій архітектурний підхід до проєктування високонавантаженого аудіострімінгу з низькою затримкою, географічним резервуванням та автоматичною синхронізацією джиттер-буфера?",
    },
    {
        "id": "small_talk",
        "title": "Casual Small Talk",
        "category": "General Conversation",
        "description": "Friendly conversation about weekly activities, pleasant weather, and weekend plans.",
        "filename": "small_talk.wav",
        "transcript_original": "Hi there! The weather has been great all week. Are you planning any outdoor activities or weekend trips?",
        "transcript_translated": "Привіт! Погода весь тиждень чудова. Чи плануєш щось на відкритому повітрі або поїздку на вихідні?",
    },
]

@app.get("/devices")
def get_devices():
    return {"devices": audio_engine.list_devices()}

@app.get("/voices")
def get_voices():
    return {"voices": AVAILABLE_VOICES}

@app.get("/samples")
def get_samples():
    return {"samples": AVAILABLE_SAMPLES}

# Active task tracking for safe async shutdown
active_call_tasks: Set[asyncio.Task] = set()
active_dubbing_tasks: Set[asyncio.Task] = set()
active_sample_tasks: Set[asyncio.Task] = set()
active_mic_test_tasks: Set[asyncio.Task] = set()

async def cancel_tasks(task_set: Set[asyncio.Task]) -> None:
    tasks = list(task_set)
    for t in tasks:
        if not t.done():
            t.cancel()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    task_set.clear()

@app.get("/metrics")
async def get_metrics():
    return audio_metrics()

@app.get("/logs")
def get_logs():
    return {"logs": list(in_memory_logs)}

session_stop_lock = asyncio.Lock()

async def finish_audio_tasks(tasks, sessions):
    captures = [task for task in tasks if task.get_name().startswith("capture-")]
    if captures:
        await asyncio.gather(*captures, return_exceptions=True)
    results = await asyncio.gather(*(session.finish(queue) for session, queue in sessions))
    return all(results)

async def stop_failed_session():
    if state.is_call_active:
        await stop_call()
    elif state.is_dubbing_active:
        await stop_dubbing()
    elif state.is_testing_active:
        await stop_sample_test()
    elif state.is_mic_test_active:
        await stop_mic_test_endpoint()

@app.post("/call/start")
async def start_call(req: CallStartRequest):
    if state.is_stopping or state.is_call_active or state.is_dubbing_active or state.is_testing_active or state.is_mic_test_active:
        return {"status": "already_running"}

    if req.call_virtual_mic_index is None:
        state.last_error = "Оберіть віртуальний мікрофон для відтворення вихідного перекладу."
        raise HTTPException(status_code=400, detail=state.last_error)

    if req.outgoing_voice not in VALID_VOICE_IDS:
        raise HTTPException(status_code=400, detail=f"Невідомий голос для виходу: {req.outgoing_voice}")
    if req.incoming_voice not in VALID_VOICE_IDS:
        raise HTTPException(status_code=400, detail=f"Невідомий голос для входу: {req.incoming_voice}")

    state.last_error = None
    api_key = req.api_key or os.environ.get("GEMINI_API_KEY", "")
    outgoing_ai.set_api_key(api_key)
    incoming_ai.set_api_key(api_key)

    state.partner_lang = req.partner_lang
    state.outgoing_voice = req.outgoing_voice
    state.incoming_voice = req.incoming_voice
    state.jitter_buffer_ms = req.jitter_buffer_ms

    outgoing_ai.target_lang = req.partner_lang
    outgoing_ai.set_voice(req.outgoing_voice)

    incoming_ai.target_lang = "uk"
    incoming_ai.set_voice(req.incoming_voice)

    audio_engine.set_ducking_factor(req.ducking_factor)
    audio_engine.set_jitter_buffer_ms(req.jitter_buffer_ms)

    state.outgoing_stt = ""
    state.outgoing_translation = ""
    state.incoming_stt = ""
    state.incoming_translation = ""
    state.outgoing_stt_history = []
    state.outgoing_trans_history = []
    state.incoming_stt_history = []
    state.incoming_trans_history = []

    add_log_entry(
        "INFO",
        f"Запуск дзвінка (мова: {req.partner_lang}, вихідний голос: {req.outgoing_voice}, вхідний: {req.incoming_voice}).",
        "call",
    )

    try:
        audio_engine.start_call(
            my_mic_index=req.my_mic_index,
            call_virtual_mic_index=req.call_virtual_mic_index,
            call_input_index=req.call_input_index,
            headphones_index=req.headphones_index,
        )
    except Exception as e:
        await asyncio.to_thread(audio_engine.stop_call)
        err_msg = f"Помилка аудіопристроїв: {e}"
        add_log_entry("ERROR", err_msg, "audio")
        state.last_error = err_msg
        raise HTTPException(status_code=500, detail=err_msg)

    audio_engine.start_playback(outgoing=True, incoming=req.call_input_index is not None)
    t1 = asyncio.create_task(audio_engine.outgoing_process_loop(), name="capture-outgoing")
    t2 = asyncio.create_task(audio_engine.incoming_process_loop(), name="capture-incoming")
    t3 = asyncio.create_task(outgoing_ai.run(audio_engine.outgoing_input_queue))
    t4 = asyncio.create_task(incoming_ai.run(audio_engine.incoming_input_queue))
    active_call_tasks.update([t1, t2, t3, t4])

    state.is_call_active = True
    return {"status": "call_started"}

@app.post("/call/stop")
async def stop_call():
    async with session_stop_lock:
        if not state.is_call_active and not active_call_tasks:
            return {"status": "not_running"}
        state.is_stopping = True
        try:
            audio_engine.is_call_running = False
            complete = await finish_audio_tasks(active_call_tasks, [(outgoing_ai, audio_engine.outgoing_input_queue), (incoming_ai, audio_engine.incoming_input_queue)])
            played = await audio_engine.graceful_stop_call()
            complete = complete and played
        finally:
            await cancel_tasks(active_call_tasks)
            state.is_call_active = state.is_stopping = False
    add_log_entry("INFO", "Синхронний дзвінок зупинено.", "call")
    return {"status": "call_stopped", "complete": complete}

# Media / YouTube Dubbing Endpoints
@app.post("/dubbing/start")
async def start_dubbing(req: DubbingStartRequest):
    if state.is_stopping or state.is_call_active or state.is_dubbing_active or state.is_testing_active or state.is_mic_test_active:
        return {"status": "already_running"}

    if req.input_device_index is None:
        state.last_error = "Оберіть аудіовхід для дублювання."
        raise HTTPException(status_code=400, detail=state.last_error)

    if req.voice_name not in VALID_VOICE_IDS:
        raise HTTPException(status_code=400, detail=f"Невідомий голос для дубляжу: {req.voice_name}")

    state.last_error = None
    api_key = req.api_key or os.environ.get("GEMINI_API_KEY", "")
    incoming_ai.set_api_key(api_key)
    incoming_ai.target_lang = "uk"
    incoming_ai.set_voice(req.voice_name)

    state.partner_lang = req.source_lang
    state.incoming_voice = req.voice_name
    state.jitter_buffer_ms = req.jitter_buffer_ms

    audio_engine.set_ducking_factor(req.ducking_factor)
    audio_engine.set_jitter_buffer_ms(req.jitter_buffer_ms)

    state.incoming_stt = ""
    state.incoming_translation = ""
    state.incoming_stt_history = []
    state.incoming_trans_history = []
    state.is_dubbing_active = True

    add_log_entry(
        "INFO",
        f"Запуск дублювання відео (джерело: {req.source_lang}, голос дубляжу: {req.voice_name}).",
        "dubbing",
    )

    try:
        audio_engine.start_dubbing(
            input_device_index=req.input_device_index,
            headphones_index=req.headphones_index,
        )
    except Exception as e:
        await asyncio.to_thread(audio_engine.stop_dubbing)
        err_msg = f"Помилка аудіопристроїв дублювання: {e}"
        add_log_entry("ERROR", err_msg, "audio")
        state.last_error = err_msg
        state.is_dubbing_active = False
        raise HTTPException(status_code=500, detail=err_msg)

    audio_engine.start_playback(incoming=req.input_device_index is not None)
    t1 = asyncio.create_task(audio_engine.incoming_process_loop(), name="capture-incoming")
    t2 = asyncio.create_task(incoming_ai.run(audio_engine.incoming_input_queue))
    active_dubbing_tasks.update([t1, t2])

    return {"status": "dubbing_started"}

@app.post("/dubbing/stop")
async def stop_dubbing():
    async with session_stop_lock:
        if not state.is_dubbing_active and not active_dubbing_tasks:
            return {"status": "not_running"}
        state.is_stopping = True
        try:
            audio_engine.is_dubbing_running = False
            complete = await finish_audio_tasks(active_dubbing_tasks, [(incoming_ai, audio_engine.incoming_input_queue)])
            played = await audio_engine.graceful_stop_dubbing()
            complete = complete and played
        finally:
            await cancel_tasks(active_dubbing_tasks)
            state.is_dubbing_active = state.is_stopping = False
    add_log_entry("INFO", "Дублювання відео зупинено.", "dubbing")
    return {"status": "dubbing_stopped", "complete": complete}

@app.post("/samples/start")
async def start_sample_test(req: SampleStartRequest):
    if state.is_stopping or state.is_call_active or state.is_dubbing_active or state.is_testing_active or state.is_mic_test_active:
        return {"status": "already_running"}

    if req.voice_name not in VALID_VOICE_IDS:
        raise HTTPException(status_code=400, detail=f"Невідомий голос для тестування: {req.voice_name}")

    sample_meta = next((s for s in AVAILABLE_SAMPLES if s["id"] == req.sample_id), None)
    if not sample_meta:
        raise HTTPException(status_code=404, detail="Sample not found")

    sample_path = SAMPLES_DIR / sample_meta["filename"]
    if not sample_path.exists():
        raise HTTPException(status_code=404, detail=f"Sample file {sample_meta['filename']} missing")

    state.last_error = None
    api_key = req.api_key or os.environ.get("GEMINI_API_KEY", "")
    incoming_ai.set_api_key(api_key)
    incoming_ai.target_lang = "uk"
    incoming_ai.set_voice(req.voice_name)

    state.partner_lang = req.partner_lang
    state.incoming_voice = req.voice_name
    state.jitter_buffer_ms = req.jitter_buffer_ms

    audio_engine.set_ducking_factor(req.ducking_factor)
    audio_engine.set_jitter_buffer_ms(req.jitter_buffer_ms)

    state.incoming_stt = ""
    state.incoming_translation = ""
    state.incoming_stt_history = []
    state.incoming_trans_history = []
    state.active_sample_id = req.sample_id
    state.is_testing_active = True

    add_log_entry("INFO", f"Запуск семплу '{sample_meta['title']}' (голос: {req.voice_name}).", "sample")

    try:
        audio_engine.start_sample_test(headphones_index=req.headphones_index)
    except Exception as e:
        await asyncio.to_thread(audio_engine.stop_sample_test)
        err_msg = f"Помилка виводу звуку: {e}"
        add_log_entry("ERROR", err_msg, "audio")
        state.last_error = err_msg
        state.is_testing_active = False
        raise HTTPException(status_code=500, detail=err_msg)

    audio_engine.start_playback(incoming=True)
    t_ai = asyncio.create_task(incoming_ai.run(audio_engine.incoming_input_queue))
    active_sample_tasks.add(t_ai)

    async def run_sample():
        completed = False
        try:
            await audio_engine.sample_playback_loop(str(sample_path))
            state.is_stopping = True
            completed = await incoming_ai.finish(audio_engine.incoming_input_queue)
            played = await audio_engine._finish_playback(timeout=15.0)
            completed = completed and played
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            on_ai_error(str(exc), "SAMPLE_ERROR")
        finally:
            incoming_ai.stop()
            audio_engine._incoming_playback_running = False
            await asyncio.gather(*list(audio_engine._playback_tasks), return_exceptions=True)
            await asyncio.to_thread(audio_engine.stop_sample_test)
            state.is_testing_active = False
            state.active_sample_id = None
            state.is_stopping = False
            add_log_entry("INFO", "Відтворення семплу завершено." if completed else "Відтворення семплу зупинено через помилку.", "sample")

    t_sample = asyncio.create_task(run_sample())
    active_sample_tasks.add(t_sample)

    return {"status": "sample_testing_started", "sample": sample_meta}

@app.post("/samples/stop")
async def stop_sample_test():
    async with session_stop_lock:
        if not state.is_testing_active and not active_sample_tasks:
            return {"status": "not_running"}
        state.is_stopping = True
        audio_engine.is_sample_running = False
        if active_sample_tasks:
            await asyncio.gather(*list(active_sample_tasks), return_exceptions=True)
        active_sample_tasks.clear()
        state.is_testing_active = state.is_stopping = False
        state.active_sample_id = None
    return {"status": "sample_testing_stopped"}

# Microphone Testing Endpoints
@app.post("/test_mic/start")
async def start_mic_test_endpoint(req: MicTestStartRequest):
    """Start capturing mic audio, streaming to Gemini, and recording translated speech."""
    if state.is_stopping or state.is_call_active or state.is_dubbing_active or state.is_testing_active or state.is_mic_test_active:
        return {"status": "already_running"}

    if req.voice_name not in VALID_VOICE_IDS:
        raise HTTPException(status_code=400, detail=f"Невідомий голос для тесту мікрофона: {req.voice_name}")

    state.last_error = None
    api_key = req.api_key or os.environ.get("GEMINI_API_KEY", "")
    outgoing_ai.set_api_key(api_key)
    outgoing_ai.target_lang = req.partner_lang
    outgoing_ai.set_voice(req.voice_name)

    state.partner_lang = req.partner_lang
    state.outgoing_voice = req.voice_name
    state.outgoing_stt = ""
    state.outgoing_translation = ""
    state.outgoing_stt_history = []
    state.outgoing_trans_history = []
    state.mic_test_latency_ms = None
    state.is_mic_test_active = True

    add_log_entry("INFO", f"Початок тесту мікрофона (UA -> {req.partner_lang}, голос: {req.voice_name}).", "mic_test")

    try:
        audio_engine.start_mic_test(mic_index=req.mic_index)
    except Exception as e:
        await asyncio.to_thread(audio_engine.stop_mic_test)
        err_msg = f"Помилка ініціалізації мікрофона: {e}"
        add_log_entry("ERROR", err_msg, "audio")
        state.last_error = err_msg
        state.is_mic_test_active = False
        raise HTTPException(status_code=500, detail=err_msg)

    t_engine = asyncio.create_task(audio_engine.mic_test_loop(), name="capture-mic-test")
    t_ai = asyncio.create_task(outgoing_ai.run(audio_engine.outgoing_input_queue))
    active_mic_test_tasks.update([t_engine, t_ai])

    return {"status": "mic_test_started"}

@app.post("/test_mic/stop")
async def stop_mic_test_endpoint():
    """Stop mic test recording and finalize audio file and latency measurement."""
    if not state.is_mic_test_active:
        return {"status": "not_running"}

    async with session_stop_lock:
        state.is_stopping = True
        try:
            audio_engine.is_mic_test_running = False
            complete = await finish_audio_tasks(active_mic_test_tasks, [(outgoing_ai, audio_engine.outgoing_input_queue)])
            file_path = await asyncio.to_thread(audio_engine.stop_mic_test)
        finally:
            await cancel_tasks(active_mic_test_tasks)
            state.is_mic_test_active = state.is_stopping = False
    latency = outgoing_ai.measured_latency_ms
    state.mic_test_latency_ms = latency
    return {
        "status": "mic_test_finished",
        "complete": complete,
        "latency_ms": latency,
        "metrics": outgoing_ai.metrics(),
        "stt_text": state.outgoing_stt,
        "translated_text": state.outgoing_translation,
        "audio_url": "http://127.0.0.1:8000/test_mic/audio",
        "has_audio": file_path is not None,
    }

@app.get("/test_mic/audio")
def get_mic_test_audio():
    """Download or stream recorded mic test translated WAV."""
    wav_path = audio_engine.mic_test_file_path
    if not wav_path.exists():
        raise HTTPException(status_code=404, detail="Audio file not found")
    return FileResponse(str(wav_path), media_type="audio/wav")

@app.post("/ducking")
def set_ducking(req: DuckingRequest):
    audio_engine.set_ducking_factor(req.ducking_factor)
    return {"ducking_factor": audio_engine.incoming_ducking_dsp.ducking_factor}

@app.post("/jitter_buffer")
def set_jitter_buffer(req: JitterBufferRequest):
    state.jitter_buffer_ms = req.jitter_buffer_ms
    audio_engine.set_jitter_buffer_ms(req.jitter_buffer_ms)
    add_log_entry("INFO", f"Розмір Jitter Buffer: {req.jitter_buffer_ms} мс.", "audio")
    return {"jitter_buffer_ms": req.jitter_buffer_ms}

@app.post("/transcripts/clear")
def clear_transcripts_endpoint():
    state.outgoing_stt = ""
    state.outgoing_translation = ""
    state.incoming_stt = ""
    state.incoming_translation = ""
    state.outgoing_stt_history = []
    state.outgoing_trans_history = []
    state.incoming_stt_history = []
    state.incoming_trans_history = []
    return {"status": "cleared"}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
