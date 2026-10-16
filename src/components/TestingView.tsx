import React, { useState, useEffect, useMemo } from "react";
import {
  AudioDevice,
  DualBackendState,
  SampleInfo,
  GeminiVoice,
  MicTestResult,
} from "../api";
import { VuMeter } from "./VuMeter";
import { TranscriptBox } from "./TranscriptBox";
import {
  PlayCircle,
  StopCircle,
  Volume2,
  VolumeX,
  Mic,
  Sparkles,
  CheckCircle2,
  Sliders,
  FileAudio,
  FileText,
  ChevronDown,
  Copy,
  Check,
  Zap,
  Play,
  Pause,
} from "lucide-react";

interface TestingViewProps {
  samples: SampleInfo[];
  voices: GeminiVoice[];
  selectedSampleId: string;
  onSelectSample: (id: string) => void;
  devices: AudioDevice[];
  myMicIndex?: number;
  onSelectMyMic: (index: number) => void;
  headphonesIndex?: number;
  onSelectHeadphones: (index: number) => void;
  partnerLangLabel: string;
  sampleVoice: string;
  onSelectSampleVoice: (voice: string) => void;
  micTestVoice: string;
  onSelectMicTestVoice: (voice: string) => void;
  duckingFactor: number;
  onDuckingChange: (factor: number) => void;
  jitterBufferMs: number;
  onJitterBufferChange: (ms: number) => void;
  state: DualBackendState;
  isLoading: boolean;
  onToggleSampleTest: () => void;
  onStartMicTest: () => Promise<void>;
  onStopMicTest: () => Promise<MicTestResult | undefined>;
}

export const TestingView = React.memo<TestingViewProps>(({
  samples,
  voices,
  selectedSampleId,
  onSelectSample,
  devices,
  myMicIndex,
  onSelectMyMic,
  headphonesIndex,
  onSelectHeadphones,
  partnerLangLabel,
  sampleVoice,
  onSelectSampleVoice,
  micTestVoice,
  onSelectMicTestVoice,
  duckingFactor,
  onDuckingChange,
  jitterBufferMs,
  onJitterBufferChange,
  state,
  isLoading,
  onToggleSampleTest,
  onStartMicTest,
  onStopMicTest,
}) => {
  const [subTab, setSubTab] = useState<"samples" | "mic">("samples");
  const [micTestResult, setMicTestResult] = useState<MicTestResult | null>(null);
  const [recordSeconds, setRecordSeconds] = useState<number>(0);
  const [isPlayingAudio, setIsPlayingAudio] = useState<boolean>(false);
  const [audioElem, setAudioElem] = useState<HTMLAudioElement | null>(null);
  const [isTranscriptOpen, setIsTranscriptOpen] = useState<boolean>(false);
  const [copiedField, setCopiedField] = useState<"original" | "translated" | null>(null);

  const selectedSample = useMemo(() => samples.find((s) => s.id === selectedSampleId), [samples, selectedSampleId]);

  const handleCopyText = async (text: string | undefined, field: "original" | "translated") => {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setCopiedField(field);
      setTimeout(() => setCopiedField(null), 2000);
    } catch {
      // ignore
    }
  };

  const inputDevices = useMemo(() => devices.filter((d: AudioDevice) => d.max_input_channels > 0), [devices]);
  const outputDevices = useMemo(() => devices.filter((d: AudioDevice) => d.max_output_channels > 0), [devices]);

  // Timer during mic test
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    if (state.is_mic_test_active) {
      setRecordSeconds(0);
      timer = setInterval(() => {
        setRecordSeconds((s) => s + 1);
      }, 1000);
    }
    return () => {
      if (timer) clearInterval(timer);
    };
  }, [state.is_mic_test_active]);

  const handleToggleMicTest = async () => {
    if (state.is_mic_test_active) {
      const res = await onStopMicTest();
      if (res) {
        setMicTestResult(res);
        const audio = new Audio(`${res.audio_url}?t=${Date.now()}`);
        audio.onended = () => setIsPlayingAudio(false);
        setAudioElem(audio);
      }
    } else {
      setMicTestResult(null);
      await onStartMicTest();
    }
  };

  const handleToggleAudioPlay = () => {
    if (!audioElem) return;
    if (isPlayingAudio) {
      audioElem.pause();
      setIsPlayingAudio(false);
    } else {
      audioElem.play();
      setIsPlayingAudio(true);
    }
  };

  return (
    <div className="space-y-6">
      {/* Sub-Tab Navigation */}
      <div className="flex bg-slate-900 border border-slate-800 rounded-xl p-1 gap-1">
        <button
          onClick={() => setSubTab("samples")}
          className={`flex-1 flex items-center justify-center gap-2 py-2.5 text-xs font-semibold rounded-lg transition-all ${
            subTab === "samples"
              ? "bg-indigo-600 text-white shadow-sm"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <FileAudio className="w-4 h-4" />
          1. Готові тестові записи (Audio Samples)
        </button>

        <button
          onClick={() => setSubTab("mic")}
          className={`flex-1 flex items-center justify-center gap-2 py-2.5 text-xs font-semibold rounded-lg transition-all ${
            subTab === "mic"
              ? "bg-indigo-600 text-white shadow-sm"
              : "text-slate-400 hover:text-slate-200"
          }`}
        >
          <Mic className="w-4 h-4" />
          2. Тест мого мікрофона (Mic Recording & Latency Test)
        </button>
      </div>

      {subTab === "samples" ? (
        /* MODE 1: AUDIO SAMPLES */
        <div className="space-y-6">
          {/* Sample Selector Cards */}
          <div className="space-y-3">
            <label className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Оберіть тестовий аудіо-запис
            </label>
            <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
              {samples.map((sample) => {
                const isSelected = selectedSampleId === sample.id;
                const isPlaying =
                  state.is_testing_active && state.active_sample_id === sample.id;

                return (
                  <div
                    key={sample.id}
                    onClick={() =>
                      !state.is_testing_active && onSelectSample(sample.id)
                    }
                    className={`p-4 rounded-xl border transition-all cursor-pointer flex flex-col justify-between ${
                      isSelected
                        ? "bg-slate-900 border-indigo-500 ring-1 ring-indigo-500/50 shadow-indigo-500/10 shadow-lg"
                        : "bg-slate-900/60 border-slate-800 hover:border-slate-700 hover:bg-slate-900"
                    } ${
                      state.is_testing_active && !isPlaying
                        ? "opacity-50 cursor-not-allowed"
                        : ""
                    }`}
                  >
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[10px] font-bold uppercase tracking-wider px-2 py-0.5 rounded bg-slate-800 text-slate-300">
                          {sample.category}
                        </span>
                        {isSelected && (
                          <CheckCircle2 className="w-4 h-4 text-indigo-400" />
                        )}
                      </div>
                      <h3 className="text-sm font-semibold text-slate-200">
                        {sample.title}
                      </h3>
                      <p className="text-xs text-slate-400 mt-1.5 line-clamp-2 leading-relaxed">
                        {sample.description}
                      </p>
                    </div>

                    {isPlaying && (
                      <div className="mt-3 flex items-center gap-1.5 text-xs text-emerald-400 font-medium">
                        <span className="w-2 h-2 rounded-full bg-emerald-400 animate-ping" />
                        Відтворюється (до повного завершення фраз)...
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Sample Transcript Accordion */}
          {selectedSample && (selectedSample.transcript_original || selectedSample.transcript_translated) && (
            <div className="bg-slate-900/80 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
              <button
                type="button"
                onClick={() => setIsTranscriptOpen(!isTranscriptOpen)}
                className="w-full flex items-center justify-between px-4 py-3 bg-slate-900 hover:bg-slate-800/80 transition-colors text-left"
              >
                <div className="flex items-center gap-2.5">
                  <FileText className="w-4 h-4 text-indigo-400" />
                  <span className="text-xs font-semibold text-slate-200">
                    Транскрипція та еталонний текст семплу:{" "}
                    <span className="text-indigo-400 font-normal">{selectedSample.title}</span>
                  </span>
                </div>
                <div className="flex items-center gap-2 text-slate-400">
                  <span className="text-[11px] text-slate-400 font-medium">
                    {isTranscriptOpen ? "Згорнути" : "Переглянути текст"}
                  </span>
                  <ChevronDown
                    className={`w-4 h-4 text-slate-400 transition-transform duration-200 ${
                      isTranscriptOpen ? "rotate-180" : ""
                    }`}
                  />
                </div>
              </button>

              {isTranscriptOpen && (
                <div className="p-4 border-t border-slate-800/80 bg-slate-950/60 grid grid-cols-1 md:grid-cols-2 gap-4">
                  {/* Original Transcript (EN) */}
                  <div className="bg-slate-900 border border-slate-800/90 rounded-lg p-3 flex flex-col justify-between">
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-amber-400 flex items-center gap-1.5">
                          Оригінальний аудіозапис (EN)
                        </span>
                        {selectedSample.transcript_original && (
                          <button
                            type="button"
                            onClick={() => handleCopyText(selectedSample.transcript_original, "original")}
                            title="Скопіювати оригінал"
                            className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 transition-colors px-1.5 py-0.5 rounded hover:bg-slate-800"
                          >
                            {copiedField === "original" ? (
                              <>
                                <Check className="w-3 h-3 text-emerald-400" />
                                <span className="text-emerald-400 font-medium">Скопійовано</span>
                              </>
                            ) : (
                              <>
                                <Copy className="w-3 h-3" />
                                <span>Копіювати</span>
                              </>
                            )}
                          </button>
                        )}
                      </div>
                      <p className="font-mono text-xs text-slate-300 leading-relaxed whitespace-pre-wrap">
                        {selectedSample.transcript_original || "Текст відсутній"}
                      </p>
                    </div>
                  </div>

                  {/* Translated Transcript (UA) */}
                  <div className="bg-slate-900 border border-slate-800/90 rounded-lg p-3 flex flex-col justify-between">
                    <div>
                      <div className="flex items-center justify-between mb-2">
                        <span className="text-[11px] font-bold uppercase tracking-wider text-emerald-400 flex items-center gap-1.5">
                          Еталонний переклад (UA)
                        </span>
                        {selectedSample.transcript_translated && (
                          <button
                            type="button"
                            onClick={() => handleCopyText(selectedSample.transcript_translated, "translated")}
                            title="Скопіювати переклад"
                            className="flex items-center gap-1 text-[11px] text-slate-400 hover:text-slate-200 transition-colors px-1.5 py-0.5 rounded hover:bg-slate-800"
                          >
                            {copiedField === "translated" ? (
                              <>
                                <Check className="w-3 h-3 text-emerald-400" />
                                <span className="text-emerald-400 font-medium">Скопійовано</span>
                              </>
                            ) : (
                              <>
                                <Copy className="w-3 h-3" />
                                <span>Копіювати</span>
                              </>
                            )}
                          </button>
                        )}
                      </div>
                      <p className="font-mono text-xs text-slate-300 leading-relaxed whitespace-pre-wrap">
                        {selectedSample.transcript_translated || "Переклад відсутній"}
                      </p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Audio Output & DSP Controls */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm">
            <div className="space-y-3">
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                    <Volume2 className="w-3.5 h-3.5 text-indigo-400" />
                    Пристрій виводу
                  </label>
                  <select
                    value={headphonesIndex}
                    onChange={(e) => onSelectHeadphones(Number(e.target.value))}
                    disabled={state.is_testing_active}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
                  >
                    {outputDevices.map((d) => (
                      <option key={d.index} value={d.index}>
                        {d.name}
                      </option>
                    ))}
                  </select>
                </div>

                <div className="space-y-1">
                  <label className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                    <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
                    Голос перекладу (AI)
                  </label>
                  <select
                    value={sampleVoice}
                    onChange={(e) => onSelectSampleVoice(e.target.value)}
                    disabled={state.is_testing_active}
                    className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50 font-medium"
                  >
                    {voices.map((v) => (
                      <option key={v.id} value={v.id}>
                        {v.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <VuMeter
                label="Рівень тестового аудіо"
                volumeDb={state.incoming.volume_db}
                isDucking={state.incoming.is_ducking}
                activeColorClass="bg-emerald-500"
              />
            </div>

            <div className="space-y-3">
              {/* Ducking & Jitter Buffer Grid */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <div className="flex justify-between items-center">
                    <label className="text-[11px] font-medium text-slate-300 flex items-center gap-1">
                      <VolumeX className="w-3 h-3 text-emerald-400" />
                      Приглушення (Ducking)
                    </label>
                    <span className="text-[11px] font-mono font-semibold text-emerald-400">
                      {Math.round(duckingFactor * 100)}%
                    </span>
                  </div>
                  <input
                    type="range"
                    min="0.0"
                    max="1.0"
                    step="0.05"
                    value={duckingFactor}
                    onChange={(e) => onDuckingChange(parseFloat(e.target.value))}
                    className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-emerald-500"
                  />
                </div>

                <div className="space-y-1">
                  <div className="flex justify-between items-center">
                    <label className="text-[11px] font-medium text-slate-300 flex items-center gap-1">
                      <Sliders className="w-3 h-3 text-indigo-400" />
                      Згладжування (Jitter)
                    </label>
                    <span className="text-[11px] font-mono font-semibold text-indigo-400">
                      {jitterBufferMs} мс
                    </span>
                  </div>
                  <input
                    type="range"
                    min="40"
                    max="300"
                    step="10"
                    value={jitterBufferMs}
                    onChange={(e) => onJitterBufferChange(parseInt(e.target.value, 10))}
                    className="w-full h-1.5 bg-slate-800 rounded-lg appearance-none cursor-pointer accent-indigo-500"
                  />
                </div>
              </div>

              <div className="pt-2">
                <button
                  onClick={onToggleSampleTest}
                  disabled={isLoading || state.is_call_active || state.is_mic_test_active}
                  className={`w-full py-2.5 px-4 rounded-lg font-semibold text-xs flex items-center justify-center gap-2 transition-all ${
                    state.is_testing_active
                      ? "bg-rose-600 hover:bg-rose-500 text-white"
                      : "bg-emerald-600 hover:bg-emerald-500 text-white"
                  } disabled:opacity-50`}
                >
                  {state.is_testing_active ? (
                    <>
                      <StopCircle className="w-4 h-4" /> Зупинити тест
                    </>
                  ) : (
                    <>
                      <PlayCircle className="w-4 h-4" /> Запустити тест обраного запису
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>

          {/* Live Test Transcripts */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <TranscriptBox
              title={`Оригінал із аудіозапису (${partnerLangLabel})`}
              text={state.incoming.stt_text}
              history={state.incoming.stt_history}
              placeholder="Текст із тестового аудіозапису з'явиться тут..."
              themeColor="amber"
            />
            <TranscriptBox
              title={`Синхронний переклад (Українська) [${sampleVoice}]`}
              text={state.incoming.translated_text}
              history={state.incoming.translated_history}
              placeholder="Синхронний український переклад та голос лунатимуть у навушниках до завершення фраз..."
              themeColor="emerald"
            />
          </div>
        </div>
      ) : (
        /* MODE 2: MIC TESTING & REVIEW */
        <div className="space-y-6">
          {/* Controls Card */}
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm space-y-4">
            <div className="flex items-start justify-between border-b border-slate-800 pb-3">
              <div>
                <h3 className="text-sm font-semibold text-slate-100 flex items-center gap-2">
                  <Mic className="w-4 h-4 text-indigo-400" />
                  Тест звуку з власного мікрофона
                </h3>
                <p className="text-xs text-slate-400 mt-1">
                  Запишіть кілька речень українською мовою. Додаток синхронно перекладе їх мовою співрозмовника ({partnerLangLabel}), виміряє затримку та збереже готове аудіо для прослуховування.
                </p>
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                  <Mic className="w-3.5 h-3.5 text-indigo-400" />
                  Оберіть мікрофон для тесту
                </label>
                <select
                  value={myMicIndex}
                  onChange={(e) => onSelectMyMic(Number(e.target.value))}
                  disabled={state.is_mic_test_active}
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
                >
                  {inputDevices.map((d) => (
                    <option key={d.index} value={d.index}>
                      {d.name}
                    </option>
                  ))}
                </select>
              </div>

              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                  <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
                  Голос озвучки перекладу (AI)
                </label>
                <select
                  value={micTestVoice}
                  onChange={(e) => onSelectMicTestVoice(e.target.value)}
                  disabled={state.is_mic_test_active}
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50 font-medium"
                >
                  {voices.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <VuMeter
              label="Рівень мікрофона під час тесту"
              volumeDb={state.outgoing.volume_db}
              activeColorClass="bg-indigo-500"
            />

            <div className="flex items-center justify-between pt-2">
              {state.is_mic_test_active ? (
                <div className="flex items-center gap-2 text-xs text-rose-400 font-semibold animate-pulse">
                  <span className="w-2.5 h-2.5 rounded-full bg-rose-500" />
                  Йде запис та синхронний переклад... ({recordSeconds} сек)
                </div>
              ) : (
                <span className="text-xs text-slate-400">
                  Натисніть кнопку нижче та проговоріть тестову репліку українською.
                </span>
              )}

              <button
                onClick={handleToggleMicTest}
                disabled={isLoading || state.is_call_active || state.is_testing_active}
                className={`py-2.5 px-6 rounded-lg font-semibold text-xs flex items-center gap-2 transition-all ${
                  state.is_mic_test_active
                    ? "bg-rose-600 hover:bg-rose-500 text-white shadow-lg shadow-rose-900/30"
                    : "bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-900/30"
                } disabled:opacity-50`}
              >
                {state.is_mic_test_active ? (
                  <>
                    <StopCircle className="w-4 h-4" /> Завершити та переглянути результат
                  </>
                ) : (
                  <>
                    <Mic className="w-4 h-4" /> Почати запис та переклад мікрофона
                  </>
                )}
              </button>
            </div>
          </div>

          {/* Test Review & Audio Player Card */}
          {micTestResult && (
            <div className="bg-slate-900 border border-indigo-500/40 rounded-xl p-5 shadow-lg shadow-indigo-950/40 space-y-4">
              {micTestResult.complete === false && (
                <p role="alert" className="text-xs text-amber-300">
                  Переклад завершено з помилкою. Запис може бути неповним; перевірте журнал.
                </p>
              )}
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-slate-800 pb-3">
                <div className="flex items-center gap-2 text-xs font-bold uppercase tracking-wider text-emerald-400">
                  <CheckCircle2 className="w-4 h-4" />
                  Результат тесту готовий
                </div>

                {/* Latency Metric Badge */}
                <div className="flex items-center gap-1.5 px-3 py-1 rounded-full bg-indigo-950/80 border border-indigo-500/50 text-indigo-300 text-xs font-semibold">
                  <Zap className="w-3.5 h-3.5 text-amber-400" />
                  До аудіовідповіді Gemini:
                  <span className="font-mono text-amber-300 font-bold">
                    {micTestResult.latency_ms === null ? "Не виміряно" : `${micTestResult.latency_ms} мс`}
                  </span>
                </div>
              </div>

              {/* Audio Playback Bar */}
              {micTestResult.has_audio && (
                <div className="bg-slate-950 border border-slate-800 rounded-xl p-3.5 flex items-center justify-between gap-4">
                  <div className="flex items-center gap-3">
                    <button
                      onClick={handleToggleAudioPlay}
                      className="p-2.5 rounded-full bg-indigo-600 hover:bg-indigo-500 text-white shadow-md transition-all"
                    >
                      {isPlayingAudio ? (
                        <Pause className="w-4 h-4" />
                      ) : (
                        <Play className="w-4 h-4 ml-0.5" />
                      )}
                    </button>
                    <div>
                      <h4 className="text-xs font-semibold text-slate-200">
                        Озвучка для співрозмовника ({partnerLangLabel}) [Голос: {micTestVoice}]
                      </h4>
                      <p className="text-[11px] text-slate-400">
                        Послухайте, як ваш перекладений голос звучатиме у дзвінку
                      </p>
                    </div>
                  </div>

                  <a
                    href={micTestResult.audio_url}
                    download="mic_test_translated.wav"
                    className="text-xs text-indigo-400 hover:text-indigo-300 font-medium underline pr-2"
                  >
                    Завантажити WAV
                  </a>
                </div>
              )}

              {/* Text Transcripts Comparison */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 pt-1">
                <TranscriptBox
                  title="Що ви сказали в мікрофон (Українська)"
                  text={micTestResult.stt_text || state.outgoing.stt_text}
                  history={state.outgoing.stt_history}
                  placeholder="Не вдалося розпізнати мову..."
                  themeColor="indigo"
                />
                <TranscriptBox
                  title={`Що почув би співрозмовник (${partnerLangLabel})`}
                  text={micTestResult.translated_text || state.outgoing.translated_text}
                  history={state.outgoing.translated_history}
                  placeholder="Переклад відсутній..."
                  themeColor="sky"
                />
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
});

