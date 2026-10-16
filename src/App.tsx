import React, { useState, useMemo } from "react";
import { useBackendState } from "./hooks/useBackendState";
import { useAudioDevices } from "./hooks/useAudioDevices";
import { useSessionControls } from "./hooks/useSessionControls";
import { SUPPORTED_LANGUAGES } from "./constants";
import { CallView } from "./components/CallView";
import { DubbingView } from "./components/DubbingView";
import { TestingView } from "./components/TestingView";
import { LogConsole } from "./components/LogConsole";
import { AudioDiagnostics } from "./components/AudioDiagnostics";
import {
  Radio,
  PhoneCall,
  FlaskConical,
  Globe,
  Key,
  Eye,
  EyeOff,
  Video,
} from "lucide-react";

export const App: React.FC = () => {
  const [activeTab, setActiveTab] = useState<"call" | "dubbing" | "testing">("call");
  
  const [partnerLang, setPartnerLang] = useState<string>("en");
  const [outgoingVoice, setOutgoingVoice] = useState<string>("Puck");
  const [incomingVoice, setIncomingVoice] = useState<string>("Aoede");
  const [dubbingVoice, setDubbingVoice] = useState<string>("Aoede");
  const [sampleVoice, setSampleVoice] = useState<string>("Aoede");
  const [micTestVoice, setMicTestVoice] = useState<string>("Puck");
  const [selectedSampleId, setSelectedSampleId] = useState<string>("it_standup");

  const [apiKey, setApiKey] = useState<string>(() => localStorage.getItem("GEMINI_API_KEY") || "");
  const [showApiKey, setShowApiKey] = useState<boolean>(false);

  const [duckingFactor, setDuckingFactor] = useState<number>(0.2);
  const [jitterBufferMs, setJitterBufferMs] = useState<number>(40);

  const backendState = useBackendState();
  const audioDevices = useAudioDevices();
  const sessionControls = useSessionControls(setDuckingFactor, setJitterBufferMs);

  const handleApiKeyChange = (val: string) => {
    setApiKey(val);
    localStorage.setItem("GEMINI_API_KEY", val);
  };

  const partnerLangOption = useMemo(
    () => SUPPORTED_LANGUAGES.find((l) => l.code === partnerLang) || SUPPORTED_LANGUAGES[0],
    [partnerLang]
  );

  return (
    <div className="flex flex-col min-h-screen bg-slate-950 text-slate-100 p-6 space-y-6">
      {/* Global Header */}
      <header className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div>
          <h1 className="text-xl font-bold tracking-wide flex items-center gap-2.5">
            <Radio className="w-5 h-5 text-indigo-400 animate-pulse" />
            Gemini Live Duo Translator
          </h1>
          <p className="text-xs text-slate-400 mt-1">
            Двосторонній синхронний AI-перекладач для Zoom та Google Meet (gemini-3.5-live-translate)
          </p>
        </div>

        {/* Global Status Indicators */}
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 px-3 py-1.5 rounded-lg">
            <span
              className={`inline-block w-2.5 h-2.5 rounded-full ${
                backendState.is_call_active || backendState.is_dubbing_active
                  ? "bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.8)]"
                  : backendState.is_testing_active || backendState.is_mic_test_active
                  ? "bg-amber-400 shadow-[0_0_8px_rgba(251,191,36,0.8)]"
                  : "bg-slate-600"
              }`}
            />
            <span className="text-xs font-semibold text-slate-300">
              {backendState.is_stopping
                ? "Завершення перекладу…"
                : backendState.is_call_active
                ? "Дзвінок активний"
                : backendState.is_dubbing_active
                ? "Дублювання відео активне"
                : backendState.is_testing_active
                ? "Тестування запису"
                : backendState.is_mic_test_active
                ? "Запис тесту мікрофона"
                : "В очікуванні"}
            </span>
          </div>
        </div>
      </header>

      {backendState.audio_metrics && <AudioDiagnostics metrics={backendState.audio_metrics} />}

      {/* Shared Configuration Bar: Partner Language & API Key */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 bg-slate-900/80 border border-slate-800/80 rounded-xl p-4">
        {/* Partner Language Selection */}
        <div className="space-y-1.5">
          <label className="text-xs font-semibold text-slate-300 flex items-center gap-1.5">
            <Globe className="w-4 h-4 text-indigo-400" />
            Мова співрозмовника / відео (Ваша мова завжди Українська 🇺🇦)
          </label>
          <select
            value={partnerLang}
            onChange={(e) => setPartnerLang(e.target.value)}
            disabled={
              backendState.is_call_active ||
              backendState.is_dubbing_active ||
              backendState.is_testing_active ||
              backendState.is_mic_test_active
            }
            className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50 font-medium"
          >
            {SUPPORTED_LANGUAGES.map((lang) => (
              <option key={lang.code} value={lang.code}>
                {lang.label}
              </option>
            ))}
          </select>
        </div>

        {/* Gemini API Key */}
        <div className="space-y-1.5">
          <label className="text-xs font-semibold text-slate-300 flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <Key className="w-4 h-4 text-indigo-400" />
              Gemini API Key
            </span>
            <button
              type="button"
              onClick={() => setShowApiKey(!showApiKey)}
              className="text-[11px] text-slate-400 hover:text-slate-200 flex items-center gap-1"
            >
              {showApiKey ? <EyeOff className="w-3.5 h-3.5" /> : <Eye className="w-3.5 h-3.5" />}
              {showApiKey ? "Сховати" : "Показати"}
            </button>
          </label>
          <input
            type={showApiKey ? "text" : "password"}
            placeholder="Введіть AIzaSy... (або залиште порожнім, якщо є .env)"
            value={apiKey}
            onChange={(e) => handleApiKeyChange(e.target.value)}
            disabled={
              backendState.is_call_active ||
              backendState.is_dubbing_active ||
              backendState.is_testing_active ||
              backendState.is_mic_test_active
            }
            className="w-full bg-slate-950 border border-slate-700 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50 font-mono"
          />
        </div>
      </div>

      {/* Tab Navigation */}
      <div className="flex border-b border-slate-800 gap-2">
        <button
          onClick={() => setActiveTab("call")}
          className={`flex items-center gap-2 px-5 py-2.5 text-xs font-semibold rounded-t-lg transition-all ${
            activeTab === "call"
              ? "bg-slate-900 text-indigo-400 border-t border-l border-r border-slate-800"
              : "text-slate-400 hover:text-slate-200 hover:bg-slate-900/40"
          }`}
        >
          <PhoneCall className="w-4 h-4" />
          Синхронний дзвінок (Live Call)
        </button>

        <button
          onClick={() => setActiveTab("dubbing")}
          className={`flex items-center gap-2 px-5 py-2.5 text-xs font-semibold rounded-t-lg transition-all ${
            activeTab === "dubbing"
              ? "bg-slate-900 text-indigo-400 border-t border-l border-r border-slate-800"
              : "text-slate-400 hover:text-slate-200 hover:bg-slate-900/40"
          }`}
        >
          <Video className="w-4 h-4" />
          Дублювання відео (YouTube / Media)
        </button>

        <button
          onClick={() => setActiveTab("testing")}
          className={`flex items-center gap-2 px-5 py-2.5 text-xs font-semibold rounded-t-lg transition-all ${
            activeTab === "testing"
              ? "bg-slate-900 text-indigo-400 border-t border-l border-r border-slate-800"
              : "text-slate-400 hover:text-slate-200 hover:bg-slate-900/40"
          }`}
        >
          <FlaskConical className="w-4 h-4" />
          Тестування та мікрофон (Playground)
        </button>
      </div>

      {/* Main Tab Content */}
      <main className="space-y-6">
        {activeTab === "call" ? (
          <CallView
            devices={audioDevices.devices}
            voices={audioDevices.voices}
            myMicIndex={audioDevices.myMicIndex}
            onSelectMyMic={audioDevices.setMyMicIndex}
            callVirtualMicIndex={audioDevices.callVirtualMicIndex}
            onSelectCallVirtualMic={audioDevices.setCallVirtualMicIndex}
            callInputIndex={audioDevices.callInputIndex}
            onSelectCallInput={audioDevices.setCallInputIndex}
            headphonesIndex={audioDevices.headphonesIndex}
            onSelectHeadphones={audioDevices.setHeadphonesIndex}
            partnerLangLabel={partnerLangOption.label}
            outgoingVoice={outgoingVoice}
            onSelectOutgoingVoice={setOutgoingVoice}
            incomingVoice={incomingVoice}
            onSelectIncomingVoice={setIncomingVoice}
            duckingFactor={duckingFactor}
            onDuckingChange={sessionControls.handleDuckingChange}
            jitterBufferMs={jitterBufferMs}
            onJitterBufferChange={sessionControls.handleJitterBufferChange}
            state={backendState}
            isLoading={sessionControls.isLoading}
            onToggleCall={() => sessionControls.handleToggleCall({
              my_mic_index: audioDevices.myMicIndex,
              call_virtual_mic_index: audioDevices.callVirtualMicIndex,
              call_input_index: audioDevices.callInputIndex,
              headphones_index: audioDevices.headphonesIndex,
              partner_lang: partnerLang,
              outgoing_voice: outgoingVoice,
              incoming_voice: incomingVoice,
              ducking_factor: duckingFactor,
              jitter_buffer_ms: jitterBufferMs,
              api_key: apiKey || undefined,
            })}
          />
        ) : activeTab === "dubbing" ? (
          <DubbingView
            devices={audioDevices.devices}
            voices={audioDevices.voices}
            dubbingInputIndex={audioDevices.dubbingInputIndex}
            onSelectDubbingInput={audioDevices.setDubbingInputIndex}
            headphonesIndex={audioDevices.headphonesIndex}
            onSelectHeadphones={audioDevices.setHeadphonesIndex}
            sourceLangLabel={partnerLangOption.label}
            sourceLangCode={partnerLangOption.code}
            dubbingVoice={dubbingVoice}
            onSelectDubbingVoice={setDubbingVoice}
            duckingFactor={duckingFactor}
            onDuckingChange={sessionControls.handleDuckingChange}
            jitterBufferMs={jitterBufferMs}
            onJitterBufferChange={sessionControls.handleJitterBufferChange}
            state={backendState}
            isLoading={sessionControls.isLoading}
            onToggleDubbing={() => sessionControls.handleToggleDubbing({
              input_device_index: audioDevices.dubbingInputIndex,
              headphones_index: audioDevices.headphonesIndex,
              source_lang: partnerLang,
              voice_name: dubbingVoice,
              ducking_factor: duckingFactor,
              jitter_buffer_ms: jitterBufferMs,
              api_key: apiKey || undefined,
            })}
          />
        ) : (
          <TestingView
            samples={audioDevices.samples}
            voices={audioDevices.voices}
            selectedSampleId={selectedSampleId}
            onSelectSample={setSelectedSampleId}
            devices={audioDevices.devices}
            myMicIndex={audioDevices.myMicIndex}
            onSelectMyMic={audioDevices.setMyMicIndex}
            headphonesIndex={audioDevices.headphonesIndex}
            onSelectHeadphones={audioDevices.setHeadphonesIndex}
            partnerLangLabel={partnerLangOption.label}
            sampleVoice={sampleVoice}
            onSelectSampleVoice={setSampleVoice}
            micTestVoice={micTestVoice}
            onSelectMicTestVoice={setMicTestVoice}
            duckingFactor={duckingFactor}
            onDuckingChange={sessionControls.handleDuckingChange}
            jitterBufferMs={jitterBufferMs}
            onJitterBufferChange={sessionControls.handleJitterBufferChange}
            state={backendState}
            isLoading={sessionControls.isLoading}
            onToggleSampleTest={() => sessionControls.handleToggleSampleTest({
              sample_id: selectedSampleId,
              headphones_index: audioDevices.headphonesIndex,
              partner_lang: partnerLang,
              voice_name: sampleVoice,
              ducking_factor: duckingFactor,
              jitter_buffer_ms: jitterBufferMs,
              api_key: apiKey || undefined,
            })}
            onStartMicTest={() => sessionControls.handleStartMicTest({
              mic_index: audioDevices.myMicIndex,
              partner_lang: partnerLang,
              voice_name: micTestVoice,
              api_key: apiKey || undefined,
            })}
            onStopMicTest={sessionControls.handleStopMicTest}
          />
        )}

        {/* Global Log & Diagnostic Console */}
        <LogConsole
          logs={backendState.logs}
          lastError={backendState.last_error}
        />
      </main>
    </div>
  );
};
export default App;
