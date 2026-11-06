import React, { useMemo } from "react";
import { AudioDevice, DualBackendState, GeminiVoice } from "../api";
import { VuMeter } from "./VuMeter";
import { TranscriptBox } from "./TranscriptBox";
import { SetupGuide } from "./SetupGuide";
import {
  Mic,
  Volume2,
  VolumeX,
  Play,
  Square,
  ArrowRight,
  PhoneForwarded,
  Sliders,
  Sparkles,
  AlertTriangle,
  RefreshCw,
} from "lucide-react";

interface CallViewProps {
  devices: AudioDevice[];
  voices: GeminiVoice[];
  myMicIndex?: number;
  onSelectMyMic: (index: number) => void;
  callVirtualMicIndex?: number;
  onSelectCallVirtualMic: (index: number) => void;
  callInputIndex?: number;
  onSelectCallInput: (index: number) => void;
  headphonesIndex?: number;
  onSelectHeadphones: (index: number) => void;
  partnerLangLabel: string;
  outgoingVoice: string;
  onSelectOutgoingVoice: (voice: string) => void;
  incomingVoice: string;
  onSelectIncomingVoice: (voice: string) => void;
  duckingFactor: number;
  onDuckingChange: (factor: number) => void;
  jitterBufferMs: number;
  onJitterBufferChange: (ms: number) => void;
  state: DualBackendState;
  isLoading: boolean;
  onToggleCall: () => void;
  onRefreshDevices?: () => void;
  isRefreshingDevices?: boolean;
}

export const CallView = React.memo<CallViewProps>(({
  devices,
  voices,
  myMicIndex,
  onSelectMyMic,
  callVirtualMicIndex,
  onSelectCallVirtualMic,
  callInputIndex,
  onSelectCallInput,
  headphonesIndex,
  onSelectHeadphones,
  partnerLangLabel,
  outgoingVoice,
  onSelectOutgoingVoice,
  incomingVoice,
  onSelectIncomingVoice,
  duckingFactor,
  onDuckingChange,
  jitterBufferMs,
  onJitterBufferChange,
  state,
  isLoading,
  onToggleCall,
  onRefreshDevices,
  isRefreshingDevices,
}) => {
  const inputDevices = useMemo(() => devices.filter((d: AudioDevice) => d.max_input_channels > 0), [devices]);
  const outputDevices = useMemo(() => devices.filter((d: AudioDevice) => d.max_output_channels > 0), [devices]);

  const selectedVirtualMic = useMemo(() => outputDevices.find((d) => d.index === callVirtualMicIndex), [outputDevices, callVirtualMicIndex]);
  const selectedCallInput = useMemo(() => inputDevices.find((d) => d.index === callInputIndex), [inputDevices, callInputIndex]);

  // Detect whether the same virtual driver is used for both outgoing virtual mic and incoming call sound
  const isLoopbackRisk = useMemo(() => {
    if (!selectedVirtualMic || !selectedCallInput) return false;
    const outName = selectedVirtualMic.name.toLowerCase();
    const inName = selectedCallInput.name.toLowerCase();

    if (outName === inName) return true;
    if (outName.includes("blackhole 2ch") && inName.includes("blackhole 2ch")) return true;
    if (outName.includes("blackhole 16ch") && inName.includes("blackhole 16ch")) return true;
    if (outName.includes("blackhole 64ch") && inName.includes("blackhole 64ch")) return true;
    if (
      outName.includes("blackhole") &&
      inName.includes("blackhole") &&
      !outName.includes("16ch") &&
      !inName.includes("16ch") &&
      !outName.includes("64ch") &&
      !inName.includes("64ch")
    ) {
      return true;
    }
    return false;
  }, [selectedVirtualMic, selectedCallInput]);

  return (
    <div className="space-y-6">
      {/* Interactive Setup Guide */}
      <SetupGuide />

      {/* Loopback Feedback Conflict Warning */}
      {isLoopbackRisk && (
        <div className="p-4 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-start gap-3 text-rose-200 shadow-sm animate-pulse-subtle">
          <AlertTriangle className="w-5 h-5 text-rose-400 shrink-0 mt-0.5" />
          <div className="space-y-1">
            <h4 className="text-xs font-semibold text-rose-300">
              Увага: Ризик закільцьовування звуку (Loopback Conflict)!
            </h4>
            <p className="text-[11px] leading-relaxed text-rose-200/90">
              Ви обрали однаковий пристрій <strong className="text-rose-100 font-mono">"{selectedVirtualMic?.name}"</strong> одночасно для «Віртуального мікрофона для Zoom» та «Входу звуку із Zoom». У такому режимі переклад вашого власного голосу транслюватиметься вам назад у навушники.
            </p>
            <p className="text-[11px] text-rose-300 font-medium pt-0.5">
              💡 <strong>Як виправити:</strong> встановіть <code className="text-xs bg-rose-950/60 px-1 py-0.5 rounded text-rose-200 font-mono">BlackHole 2ch</code> для мікрофона та <code className="text-xs bg-rose-950/60 px-1 py-0.5 rounded text-rose-200 font-mono">BlackHole 16ch</code> для входу звуку співрозмовника.
            </p>
          </div>
        </div>
      )}

      {/* 2 Duplex Channels Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Channel 1: Outgoing (Me -> Zoom/Meet) */}
        <section className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm space-y-4 flex flex-col">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center gap-2">
              <span className="p-1.5 rounded-lg bg-indigo-500/10 text-indigo-400">
                <Mic className="w-4 h-4" />
              </span>
              <div>
                <h2 className="text-sm font-semibold text-slate-200">
                  Мій голос для дзвінка
                </h2>
                <p className="text-[11px] text-slate-400">
                  Українська (UA) <ArrowRight className="inline w-3 h-3 mx-0.5" /> {partnerLangLabel}
                </p>
              </div>
            </div>
            <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded bg-indigo-900/40 text-indigo-300 border border-indigo-700/50">
              Вихідна лінія
            </span>
          </div>

          {/* Device & Voice Controls */}
          <div className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                  <Mic className="w-3.5 h-3.5 text-indigo-400" />
                  Мій мікрофон
                </label>
                <select
                  value={myMicIndex}
                  onChange={(e) => onSelectMyMic(Number(e.target.value))}
                  disabled={state.is_call_active}
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
                  Голос для Zoom (AI)
                </label>
                <select
                  value={outgoingVoice}
                  onChange={(e) => onSelectOutgoingVoice(e.target.value)}
                  disabled={state.is_call_active}
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

            <div className="space-y-1">
              <label className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                <PhoneForwarded className="w-3.5 h-3.5 text-indigo-400" />
                Віртуальний мікрофон для Zoom/Meet (BlackHole)
              </label>
              <select
                value={callVirtualMicIndex}
                onChange={(e) => onSelectCallVirtualMic(Number(e.target.value))}
                disabled={state.is_call_active}
                className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
              >
                <option value="">-- Без виходу (лише транскрипт) --</option>
                {outputDevices.map((d) => (
                  <option key={d.index} value={d.index}>
                    {d.name}
                  </option>
                ))}
              </select>
              <p className="text-[10px] text-slate-500 italic">
                * У Zoom або Meet виберіть цей віртуальний пристрій як свій мікрофон.
              </p>
            </div>

            <VuMeter
              label="Рівень мого мікрофона"
              volumeDb={state.outgoing.volume_db}
              activeColorClass="bg-indigo-500"
            />
          </div>

          {/* Transcripts */}
          <div className="space-y-2.5 flex-1 flex flex-col pt-2 border-t border-slate-800/80">
            <TranscriptBox
              title="Що я сказав (Українська)"
              text={state.outgoing.stt_text}
              history={state.outgoing.stt_history}
              placeholder="Говоріть у мікрофон українською..."
              themeColor="indigo"
            />
            <TranscriptBox
              title={`Переклад у Zoom (${partnerLangLabel}) [${outgoingVoice}]`}
              text={state.outgoing.translated_text}
              history={state.outgoing.translated_history}
              placeholder="Синхронний AI-переклад транслюватиметься сюди..."
              themeColor="sky"
            />
          </div>
        </section>

        {/* Channel 2: Incoming (Zoom/Meet -> Headphones) */}
        <section className="bg-slate-900 border border-slate-800 rounded-xl p-5 shadow-sm space-y-4 flex flex-col">
          <div className="flex items-center justify-between border-b border-slate-800 pb-3">
            <div className="flex items-center gap-2">
              <span className="p-1.5 rounded-lg bg-emerald-500/10 text-emerald-400">
                <Volume2 className="w-4 h-4" />
              </span>
              <div>
                <h2 className="text-sm font-semibold text-slate-200">
                  Звук співрозмовника
                </h2>
                <p className="text-[11px] text-slate-400">
                  {partnerLangLabel} <ArrowRight className="inline w-3 h-3 mx-0.5" /> Українська (UA)
                </p>
              </div>
            </div>
            <span className="text-[10px] uppercase font-bold tracking-wider px-2 py-0.5 rounded bg-emerald-900/40 text-emerald-300 border border-emerald-700/50">
              Вхідна лінія
            </span>
          </div>

          {/* Device Controls */}
          <div className="space-y-3">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="space-y-1">
                <label className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                  <Mic className="w-3.5 h-3.5 text-emerald-400" />
                  Вхід звуку із Zoom (BlackHole)
                </label>
                <select
                  value={callInputIndex}
                  onChange={(e) => onSelectCallInput(Number(e.target.value))}
                  disabled={state.is_call_active}
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-emerald-500 disabled:opacity-50"
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
                  <Sparkles className="w-3.5 h-3.5 text-emerald-400" />
                  Голос у навушники (AI)
                </label>
                <select
                  value={incomingVoice}
                  onChange={(e) => onSelectIncomingVoice(e.target.value)}
                  disabled={state.is_call_active}
                  className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-emerald-500 disabled:opacity-50 font-medium"
                >
                  {voices.map((v) => (
                    <option key={v.id} value={v.id}>
                      {v.label}
                    </option>
                  ))}
                </select>
              </div>
            </div>

            <div className="space-y-1">
              <div className="flex items-center justify-between">
                <label className="text-xs font-medium text-slate-300 flex items-center gap-1.5">
                  <Volume2 className="w-3.5 h-3.5 text-emerald-400" />
                  Мої навушники / динаміки (вихід перекладу)
                </label>
                {onRefreshDevices && (
                  <button
                    type="button"
                    onClick={onRefreshDevices}
                    disabled={isRefreshingDevices || state.is_call_active}
                    className="text-[11px] text-slate-400 hover:text-slate-200 flex items-center gap-1 transition-colors disabled:opacity-50"
                    title="Оновити список аудіопристроїв"
                  >
                    <RefreshCw className={`w-3 h-3 ${isRefreshingDevices ? "animate-spin text-emerald-400" : ""}`} />
                    <span>{isRefreshingDevices ? "Пошук…" : "Оновити"}</span>
                  </button>
                )}
              </div>
              <select
                value={headphonesIndex}
                onChange={(e) => onSelectHeadphones(Number(e.target.value))}
                disabled={state.is_call_active}
                className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-emerald-500 disabled:opacity-50"
              >
                {outputDevices.map((d) => (
                  <option key={d.index} value={d.index}>
                    {d.name}
                  </option>
                ))}
              </select>
            </div>

            {/* DSP Controls: Ducking & Jitter Buffer */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1 border-t border-slate-800">
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

            <VuMeter
              label="Рівень звуку дзвінка"
              volumeDb={state.incoming.volume_db}
              isDucking={state.incoming.is_ducking}
              activeColorClass="bg-emerald-500"
            />
          </div>

          {/* Transcripts */}
          <div className="space-y-2.5 flex-1 flex flex-col pt-2 border-t border-slate-800/80">
            <TranscriptBox
              title={`Що каже співрозмовник (${partnerLangLabel})`}
              text={state.incoming.stt_text}
              history={state.incoming.stt_history}
              placeholder="Очікування звуку зі співрозмовника..."
              themeColor="amber"
            />
            <TranscriptBox
              title={`Переклад у навушниках (Українська) [${incomingVoice}]`}
              text={state.incoming.translated_text}
              history={state.incoming.translated_history}
              placeholder="Український голос та переклад з'являться тут..."
              themeColor="emerald"
            />
          </div>
        </section>
      </div>

      {/* Main Action Button */}
      <div className="flex justify-center pt-2">
        <button
          onClick={onToggleCall}
          disabled={isLoading || state.is_testing_active || state.is_mic_test_active}
          className={`w-full max-w-md py-3.5 px-6 rounded-xl font-semibold text-sm flex items-center justify-center gap-2.5 shadow-lg transition-all ${
            state.is_call_active
              ? "bg-rose-600 hover:bg-rose-500 text-white shadow-rose-900/40"
              : "bg-indigo-600 hover:bg-indigo-500 text-white shadow-indigo-900/40"
          } disabled:opacity-50 disabled:cursor-not-allowed`}
        >
          {state.is_call_active ? (
            <>
              <Square className="w-5 h-5" /> Зупинити синхронний дзвінок
            </>
          ) : (
            <>
              <Play className="w-5 h-5" /> Запустити синхронний переклад дзвінка (Full Duplex)
            </>
          )}
        </button>
      </div>
    </div>
  );
});

