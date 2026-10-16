import { useState, useCallback } from 'react';
import { startCall, stopCall, startDubbing, stopDubbing, startSampleTest, stopSampleTest, startMicTest, stopMicTest, updateDuckingFactor, updateJitterBuffer, MicTestResult, CallStartPayload, DubbingStartPayload, SampleStartPayload, MicTestStartPayload } from '../api';
import { useBackendState } from './useBackendState';

export interface SessionControls {
  isLoading: boolean;
  handleToggleCall: (params: CallStartPayload) => Promise<void>;
  handleToggleDubbing: (params: DubbingStartPayload) => Promise<void>;
  handleToggleSampleTest: (params: SampleStartPayload) => Promise<void>;
  handleStartMicTest: (params: MicTestStartPayload) => Promise<void>;
  handleStopMicTest: () => Promise<MicTestResult | undefined>;
  handleDuckingChange: (val: number) => Promise<void>;
  handleJitterBufferChange: (val: number) => Promise<void>;
}

export function useSessionControls(setDuckingFactor: (val: number) => void, setJitterBufferMs: (val: number) => void): SessionControls {
  const [isLoading, setIsLoading] = useState<boolean>(false);
  
  const is_call_active = useBackendState(state => state.is_call_active);
  const is_dubbing_active = useBackendState(state => state.is_dubbing_active);
  const is_testing_active = useBackendState(state => state.is_testing_active);
  const is_stopping = useBackendState(state => state.is_stopping ?? false);

  const handleToggleCall = useCallback(async (params: CallStartPayload) => {
    setIsLoading(true);
    try {
      if (is_call_active) {
        await stopCall();
      } else {
        await startCall(params);
      }
    } catch (err) {
      console.error("[Call Toggle Error]", err);
    } finally {
      setIsLoading(false);
    }
  }, [is_call_active]);

  const handleToggleDubbing = useCallback(async (params: DubbingStartPayload) => {
    setIsLoading(true);
    try {
      if (is_dubbing_active) {
        await stopDubbing();
      } else {
        await startDubbing(params);
      }
    } catch (err) {
      console.error("[Dubbing Toggle Error]", err);
    } finally {
      setIsLoading(false);
    }
  }, [is_dubbing_active]);

  const handleToggleSampleTest = useCallback(async (params: SampleStartPayload) => {
    setIsLoading(true);
    try {
      if (is_testing_active) {
        await stopSampleTest();
      } else {
        await startSampleTest(params);
      }
    } catch (err) {
      console.error("[Sample Toggle Error]", err);
    } finally {
      setIsLoading(false);
    }
  }, [is_testing_active]);

  const handleStartMicTest = useCallback(async (params: MicTestStartPayload) => {
    setIsLoading(true);
    try {
      await startMicTest(params);
    } catch (err) {
      console.error("[Mic Test Start Error]", err);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleStopMicTest = useCallback(async (): Promise<MicTestResult | undefined> => {
    setIsLoading(true);
    try {
      return await stopMicTest();
    } catch (err) {
      console.error("[Mic Test Stop Error]", err);
      return undefined;
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleDuckingChange = useCallback(async (val: number) => {
    setDuckingFactor(val);
    try {
      await updateDuckingFactor(val);
    } catch (err) {
      console.error("[Ducking Change Error]", err);
    }
  }, [setDuckingFactor]);

  const handleJitterBufferChange = useCallback(async (val: number) => {
    setJitterBufferMs(val);
    try {
      await updateJitterBuffer(val);
    } catch (err) {
      console.error("[Jitter Buffer Change Error]", err);
    }
  }, [setJitterBufferMs]);

  return {
    isLoading: isLoading || is_stopping,
    handleToggleCall,
    handleToggleDubbing,
    handleToggleSampleTest,
    handleStartMicTest,
    handleStopMicTest,
    handleDuckingChange,
    handleJitterBufferChange
  };
}
