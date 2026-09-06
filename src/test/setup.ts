import '@testing-library/jest-dom/vitest';
import { vi } from 'vitest';

// jsdom implements none of the browser APIs the voice layer reaches for, and an
// unimplemented one throws on mount rather than degrading. Stub the minimum so a
// smoke test exercises the real component tree instead of a mocked shell.

// This jsdom build exposes a localStorage that is not a working Storage, and
// useLlmChoice reads it during render. An in-memory shim keeps the real
// preference logic under test rather than mocking the hook away.
const storage = new Map<string, string>();
Object.defineProperty(window, 'localStorage', {
  writable: true,
  value: {
    getItem: (key: string) => (storage.has(key) ? storage.get(key)! : null),
    setItem: (key: string, value: string) => void storage.set(key, String(value)),
    removeItem: (key: string) => void storage.delete(key),
    clear: () => storage.clear(),
    key: (index: number) => Array.from(storage.keys())[index] ?? null,
    get length() {
      return storage.size;
    },
  },
});

class MockAudioContext {
  state = 'running';
  sampleRate = 16000;
  destination = {};
  createMediaStreamSource() {
    return { connect: () => {}, disconnect: () => {} };
  }
  createAnalyser() {
    return {
      fftSize: 2048,
      frequencyBinCount: 1024,
      connect: () => {},
      disconnect: () => {},
      getByteFrequencyData: () => {},
      getByteTimeDomainData: () => {},
    };
  }
  createGain() {
    return { gain: { value: 1 }, connect: () => {}, disconnect: () => {} };
  }
  close() {
    return Promise.resolve();
  }
  resume() {
    return Promise.resolve();
  }
}

vi.stubGlobal('AudioContext', MockAudioContext);
vi.stubGlobal('webkitAudioContext', MockAudioContext);

Object.defineProperty(window, 'matchMedia', {
  writable: true,
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});

Object.defineProperty(navigator, 'mediaDevices', {
  writable: true,
  value: { getUserMedia: () => Promise.reject(new Error('no mic in jsdom')) },
});

// EventSource backs the SSE bus; without it useServerEvents throws on mount.
class MockEventSource {
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onopen: ((event: Event) => void) | null = null;
  close() {}
  addEventListener() {}
  removeEventListener() {}
}
vi.stubGlobal('EventSource', MockEventSource);

// jsdom implements no layout, so scrollIntoView does not exist; ChatFeed calls
// it on every render.
window.HTMLElement.prototype.scrollIntoView = () => {};

window.HTMLMediaElement.prototype.play = () => Promise.resolve();
window.HTMLMediaElement.prototype.pause = () => {};
