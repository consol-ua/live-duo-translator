import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import { fetchAudioDevices, fetchVoices, fetchSamples, AudioDevice, SampleInfo, GeminiVoice } from '../api';
import { DEFAULT_VOICES } from '../constants';

export interface AudioDeviceState {
  devices: AudioDevice[];
  samples: SampleInfo[];
  voices: GeminiVoice[];
  inputDevices: AudioDevice[];
  outputDevices: AudioDevice[];
  myMicIndex: number | undefined;
  callVirtualMicIndex: number | undefined;
  callInputIndex: number | undefined;
  dubbingInputIndex: number | undefined;
  headphonesIndex: number | undefined;
  setMyMicIndex: (idx: number) => void;
  setCallVirtualMicIndex: (idx: number) => void;
  setCallInputIndex: (idx: number) => void;
  setDubbingInputIndex: (idx: number) => void;
  setHeadphonesIndex: (idx: number) => void;
  isLoopbackRisk: boolean;
  refreshDevices: () => Promise<void>;
  isRefreshing: boolean;
}

export function useAudioDevices(): AudioDeviceState {
  const [devices, setDevices] = useState<AudioDevice[]>([]);
  const [samples, setSamples] = useState<SampleInfo[]>([]);
  const [voices, setVoices] = useState<GeminiVoice[]>(DEFAULT_VOICES);
  const [isRefreshing, setIsRefreshing] = useState<boolean>(false);

  const [myMicIndex, setMyMicIndex] = useState<number | undefined>();
  const [callVirtualMicIndex, setCallVirtualMicIndex] = useState<number | undefined>();
  const [callInputIndex, setCallInputIndex] = useState<number | undefined>();
  const [dubbingInputIndex, setDubbingInputIndex] = useState<number | undefined>();
  const [headphonesIndex, setHeadphonesIndex] = useState<number | undefined>();

  // Track the actual device names to preserve selections across device list re-indexing
  const selectedNamesRef = useRef<{
    myMic?: string;
    callVirtualMic?: string;
    callInput?: string;
    dubbingInput?: string;
    headphones?: string;
  }>({});

  const applyDeviceSelection = useCallback((devs: AudioDevice[]): void => {
    const inputs = devs.filter((d) => d.max_input_channels > 0);
    const outputs = devs.filter((d) => d.max_output_channels > 0);

    // Helper to identify Bluetooth or external headsets/headphones
    const isBluetoothOrExternal = (name: string): boolean => {
      const lower = name.toLowerCase();
      return (
        !lower.includes("blackhole") &&
        !lower.includes("динаміки") &&
        !lower.includes("speakers") &&
        !lower.includes("built-in") &&
        !lower.includes("internal")
      );
    };

    // 1. My Mic
    let myMic = inputs.find((d) => d.name === selectedNamesRef.current.myMic);
    if (!myMic) {
      myMic = inputs.find((d) => !d.name.toLowerCase().includes("blackhole")) || inputs[0];
    }
    if (myMic) {
      setMyMicIndex(myMic.index);
      selectedNamesRef.current.myMic = myMic.name;
    }

    // 2. Virtual Mic (BlackHole 2ch output)
    let virtualMic = outputs.find((d) => d.name === selectedNamesRef.current.callVirtualMic);
    if (!virtualMic) {
      const blackhole2chOut = outputs.find((d) => d.name.toLowerCase().includes("blackhole 2ch"));
      const blackholeGeneralOut = outputs.find((d) => d.name.toLowerCase().includes("blackhole"));
      virtualMic = blackhole2chOut || blackholeGeneralOut;
    }
    if (virtualMic) {
      setCallVirtualMicIndex(virtualMic.index);
      selectedNamesRef.current.callVirtualMic = virtualMic.name;
    }

    // 3. Call Input (BlackHole 16ch input)
    let callInput = inputs.find((d) => d.name === selectedNamesRef.current.callInput);
    if (!callInput) {
      const blackhole16chIn = inputs.find((d) => d.name.toLowerCase().includes("blackhole 16ch"));
      const blackhole64chIn = inputs.find((d) => d.name.toLowerCase().includes("blackhole 64ch"));
      const otherVirtualIn = inputs.find(
        (d) =>
          d.name.toLowerCase().includes("blackhole") &&
          d.name.toLowerCase() !== virtualMic?.name.toLowerCase()
      );
      callInput = blackhole16chIn || blackhole64chIn || otherVirtualIn;
      if (!callInput) {
        callInput = inputs.find(
          (d) => d.index !== myMic?.index && d.name.toLowerCase() !== virtualMic?.name.toLowerCase()
        );
      }
    }
    if (callInput) {
      setCallInputIndex(callInput.index);
      selectedNamesRef.current.callInput = callInput.name;
    }

    // 4. Dubbing Input
    let dubbingIn = inputs.find((d) => d.name === selectedNamesRef.current.dubbingInput);
    if (!dubbingIn) {
      dubbingIn =
        inputs.find((d) => d.name.toLowerCase().includes("blackhole 16ch")) ||
        inputs.find((d) => d.name.toLowerCase().includes("blackhole")) ||
        inputs[0];
    }
    if (dubbingIn) {
      setDubbingInputIndex(dubbingIn.index);
      selectedNamesRef.current.dubbingInput = dubbingIn.name;
    }

    // 5. Headphones (Output)
    const bluetoothOutput = outputs.find((d) => isBluetoothOrExternal(d.name));
    let headphones = outputs.find((d) => d.name === selectedNamesRef.current.headphones);

    // If no previous selection, or if the previously selected was built-in speakers and now a Bluetooth device is connected, prefer Bluetooth:
    if (!headphones) {
      headphones = bluetoothOutput || outputs.find((d) => !d.name.toLowerCase().includes("blackhole")) || outputs[0];
    } else if (bluetoothOutput && !isBluetoothOrExternal(headphones.name)) {
      headphones = bluetoothOutput;
    }

    if (headphones) {
      setHeadphonesIndex(headphones.index);
      selectedNamesRef.current.headphones = headphones.name;
    }
  }, []);

  const refreshDevices = useCallback(async (): Promise<void> => {
    setIsRefreshing(true);
    try {
      const devs = await fetchAudioDevices();
      setDevices(devs);
      applyDeviceSelection(devs);
    } catch (err) {
      console.error("Failed to refresh audio devices:", err);
    } finally {
      setIsRefreshing(false);
    }
  }, [applyDeviceSelection]);

  useEffect(() => {
    Promise.all([fetchAudioDevices(), fetchSamples(), fetchVoices()]).then(
      ([devs, smps, vcs]) => {
        setDevices(devs);
        setSamples(smps);
        if (vcs.length > 0) setVoices(vcs);
        applyDeviceSelection(devs);
      }
    );
  }, [applyDeviceSelection]);

  // Automatically refresh devices when window gains focus (e.g. user paired Bluetooth in macOS settings and returned)
  useEffect(() => {
    const handleFocus = () => {
      refreshDevices();
    };
    window.addEventListener("focus", handleFocus);
    return () => {
      window.removeEventListener("focus", handleFocus);
    };
  }, [refreshDevices]);

  const handleSetMyMicIndex = useCallback((idx: number) => {
    setMyMicIndex(idx);
    const d = devices.find((dev) => dev.index === idx);
    if (d) selectedNamesRef.current.myMic = d.name;
  }, [devices]);

  const handleSetCallVirtualMicIndex = useCallback((idx: number) => {
    setCallVirtualMicIndex(idx);
    const d = devices.find((dev) => dev.index === idx);
    if (d) selectedNamesRef.current.callVirtualMic = d.name;
  }, [devices]);

  const handleSetCallInputIndex = useCallback((idx: number) => {
    setCallInputIndex(idx);
    const d = devices.find((dev) => dev.index === idx);
    if (d) selectedNamesRef.current.callInput = d.name;
  }, [devices]);

  const handleSetDubbingInputIndex = useCallback((idx: number) => {
    setDubbingInputIndex(idx);
    const d = devices.find((dev) => dev.index === idx);
    if (d) selectedNamesRef.current.dubbingInput = d.name;
  }, [devices]);

  const handleSetHeadphonesIndex = useCallback((idx: number) => {
    setHeadphonesIndex(idx);
    const d = devices.find((dev) => dev.index === idx);
    if (d) selectedNamesRef.current.headphones = d.name;
  }, [devices]);

  const inputDevices = useMemo(() => devices.filter(d => d.max_input_channels > 0), [devices]);
  const outputDevices = useMemo(() => devices.filter(d => d.max_output_channels > 0), [devices]);

  const selectedVirtualMic = useMemo(() => outputDevices.find((d) => d.index === callVirtualMicIndex), [outputDevices, callVirtualMicIndex]);
  const selectedCallInput = useMemo(() => inputDevices.find((d) => d.index === callInputIndex), [inputDevices, callInputIndex]);

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

  return {
    devices,
    samples,
    voices,
    inputDevices,
    outputDevices,
    myMicIndex,
    callVirtualMicIndex,
    callInputIndex,
    dubbingInputIndex,
    headphonesIndex,
    setMyMicIndex: handleSetMyMicIndex,
    setCallVirtualMicIndex: handleSetCallVirtualMicIndex,
    setCallInputIndex: handleSetCallInputIndex,
    setDubbingInputIndex: handleSetDubbingInputIndex,
    setHeadphonesIndex: handleSetHeadphonesIndex,
    isLoopbackRisk,
    refreshDevices,
    isRefreshing,
  };
}
