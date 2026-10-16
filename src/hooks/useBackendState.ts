import { useSyncExternalStore, useRef, useCallback } from 'react';
import { DualBackendState } from '../api';
import { WS_URL } from '../constants';

const DEFAULT_STATE: DualBackendState = {
  is_call_active: false,
  is_dubbing_active: false,
  is_testing_active: false,
  is_mic_test_active: false,
  active_sample_id: null,
  partner_lang: "en",
  outgoing_voice: "Puck",
  incoming_voice: "Aoede",
  jitter_buffer_ms: 40,
  mic_test_latency_ms: null,
  last_error: null,
  logs: [],
  outgoing: { stt_text: "", translated_text: "", volume_db: -100 },
  incoming: { stt_text: "", translated_text: "", volume_db: -100, is_ducking: false },
};

type Listener = () => void;

class BackendStateStore {
  private state: DualBackendState = DEFAULT_STATE;
  private listeners: Set<Listener> = new Set();
  private ws: WebSocket | null = null;
  private isClosing = false;
  private retryDelay = 1500;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private static readonly MAX_RETRY_DELAY = 30000;

  constructor() {
    this.connect();
  }

  private connect = (): void => {
    if (this.isClosing) return;

    this.ws = new WebSocket(WS_URL);

    this.ws.onopen = () => {
      this.retryDelay = 1500;
    };

    this.ws.onmessage = (event) => {
      try {
        const data: DualBackendState = JSON.parse(event.data);
        this.state = {
          ...data,
          logs: data.logs ?? this.state.logs ?? [],
          outgoing: {
            ...data.outgoing,
            stt_history: data.outgoing?.stt_history ?? this.state.outgoing?.stt_history ?? [],
            translated_history: data.outgoing?.translated_history ?? this.state.outgoing?.translated_history ?? [],
          },
          incoming: {
            ...data.incoming,
            stt_history: data.incoming?.stt_history ?? this.state.incoming?.stt_history ?? [],
            translated_history: data.incoming?.translated_history ?? this.state.incoming?.translated_history ?? [],
          },
        };
        this.emitChange();
      } catch (err) {
        console.error("[WS] Parse error:", err);
      }
    };

    this.ws.onerror = (err) => {
      console.warn("[WS] Connection error:", err);
    };

    this.ws.onclose = () => {
      if (!this.isClosing) {
        this.retryTimer = setTimeout(this.connect, this.retryDelay);
        this.retryDelay = Math.min(this.retryDelay * 2, BackendStateStore.MAX_RETRY_DELAY);
      }
    };
  };

  getSnapshot = (): DualBackendState => this.state;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private emitChange(): void {
    for (const listener of this.listeners) listener();
  }

  destroy(): void {
    this.isClosing = true;
    if (this.retryTimer) clearTimeout(this.retryTimer);
    if (this.ws) this.ws.close();
  }
}

const store = new BackendStateStore();

export function useBackendState(): DualBackendState;
export function useBackendState<T>(selector: (state: DualBackendState) => T): T;
export function useBackendState<T>(selector?: (state: DualBackendState) => T): T | DualBackendState {
  const selectorRef = useRef(selector);
  selectorRef.current = selector;
  
  const getSnapshot = useCallback(() => {
    const state = store.getSnapshot();
    return selectorRef.current ? selectorRef.current(state) : state;
  }, []);
  
  return useSyncExternalStore(store.subscribe, getSnapshot);
}

export { store as backendStateStore };
