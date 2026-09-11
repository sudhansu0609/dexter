import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// The pop-up (v11.1): the microphone and the model can be changed without
// opening the full window. Same isolation as App.test.tsx - every network call
// goes through src/api.ts, so one mock covers the UI.
vi.mock('../api', () => ({
  API_BASE: 'http://localhost',
  apiFetch: vi.fn(async () => new Response('{}')),
  eventsUrl: vi.fn(async () => 'http://localhost/events'),
  getStatus: vi.fn(async () => ({ status: 'online', runtime: null })),
  getHistory: vi.fn(async () => ({ session: 'default', turns: [] })),
  chatStream: vi.fn(async () => {}),
  voiceWsUrl: vi.fn(async () => 'ws://localhost/api/dexter/voice/ws'),
  generateTts: vi.fn(async () => new Blob()),
  audioUrl: vi.fn((u: string) => u),
  voiceInterrupt: vi.fn(async () => ({ ok: true })),
  getModels: vi.fn(async () => ({
    preferred: 'ollama',
    providers: {
      ollama: { online: true, models: ['qwen2.5:7b', 'qwen2.5:3b'] },
      lmstudio: { online: false, models: [] },
    },
  })),
  runPhrase: vi.fn(async () => ({ reply: '', action: '' })),
}));

import Popup from '../Popup';

describe('Popup quick settings', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('has a gear that opens microphone and model choices', async () => {
    render(<Popup />);
    expect(screen.queryByRole('dialog', { name: 'Quick settings' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Quick settings' }));
    expect(screen.getByRole('dialog', { name: 'Quick settings' })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: 'Microphone' })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: 'LLM provider' })).toBeTruthy();
    expect(screen.getByRole('combobox', { name: 'Model' })).toBeTruthy();
    // jsdom has no microphone: the panel says so rather than showing an empty list.
    expect(screen.getByText(/Voice unavailable|No microphones listed/)).toBeTruthy();
  });

  it('picking a backend persists it for every chat turn, and lists its models', async () => {
    render(<Popup />);
    fireEvent.click(screen.getByRole('button', { name: 'Quick settings' }));

    const provider = screen.getByRole('combobox', { name: 'LLM provider' }) as HTMLSelectElement;
    await waitFor(() => expect(provider.querySelector('option[value="auto"]')?.textContent).toContain('Ollama'));
    fireEvent.change(provider, { target: { value: 'ollama' } });
    expect(localStorage.getItem('dexterProvider')).toBe('ollama');

    const model = screen.getByRole('combobox', { name: 'Model' }) as HTMLSelectElement;
    await waitFor(() => expect(model.disabled).toBe(false));
    fireEvent.change(model, { target: { value: 'qwen2.5:3b' } });
    expect(localStorage.getItem('dexterModel')).toBe('qwen2.5:3b');
    expect(screen.getByText(/Ollama is online: qwen2.5:3b/)).toBeTruthy();
  });

  it('closes with its own button and with Esc', () => {
    render(<Popup />);
    fireEvent.click(screen.getByRole('button', { name: 'Quick settings' }));
    fireEvent.click(screen.getByRole('button', { name: 'Close settings' }));
    expect(screen.queryByRole('dialog', { name: 'Quick settings' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Quick settings' }));
    fireEvent.keyDown(window, { key: 'Escape' });
    expect(screen.queryByRole('dialog', { name: 'Quick settings' })).toBeNull();
  });
});
