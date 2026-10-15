import { describe, expect, it, beforeEach, vi } from 'vitest';
import { pickMic, saveMicId, savedMicId, BRIO_MIC } from '../voice/localPipeline';

describe('pickMic microphone selection', () => {
  beforeEach(() => {
    saveMicId('');
    vi.restoreAllMocks();
  });

  it('identifies brio mic correctly with BRIO_MIC regex', () => {
    expect(BRIO_MIC.test('Logitech BRIO')).toBe(true);
    expect(BRIO_MIC.test('Microphone (Logitech Brio 4K)')).toBe(true);
    expect(BRIO_MIC.test('BRIO Webcam Mic')).toBe(true);
    expect(BRIO_MIC.test('Realtek High Definition Audio')).toBe(false);
  });

  it('prefers Brio microphone when no user preference is saved', async () => {
    const mockMics = [
      { deviceId: 'default', kind: 'audioinput', label: 'Default - Realtek Audio' },
      { deviceId: 'realtek-123', kind: 'audioinput', label: 'Realtek Audio' },
      { deviceId: 'brio-456', kind: 'audioinput', label: 'Logitech BRIO' },
    ];
    (navigator.mediaDevices as any).enumerateDevices = vi.fn().mockResolvedValue(mockMics);

    const chosen = await pickMic();
    expect(chosen.deviceId).toBe('brio-456');
    expect(chosen.label).toBe('Logitech BRIO');
  });

  it('respects user manual selection over Brio', async () => {
    const mockMics = [
      { deviceId: 'default', kind: 'audioinput', label: 'Default - Realtek Audio' },
      { deviceId: 'headset-999', kind: 'audioinput', label: 'HyperX Cloud Headset' },
      { deviceId: 'brio-456', kind: 'audioinput', label: 'Logitech BRIO' },
    ];
    (navigator.mediaDevices as any).enumerateDevices = vi.fn().mockResolvedValue(mockMics);

    saveMicId('headset-999');
    expect(savedMicId()).toBe('headset-999');

    const chosen = await pickMic();
    expect(chosen.deviceId).toBe('headset-999');
    expect(chosen.label).toBe('HyperX Cloud Headset');
  });

  it('falls back to default when Brio is not available', async () => {
    const mockMics = [
      { deviceId: 'default', kind: 'audioinput', label: 'Realtek High Definition Audio' },
      { deviceId: 'mic-2', kind: 'audioinput', label: 'External Mic' },
    ];
    (navigator.mediaDevices as any).enumerateDevices = vi.fn().mockResolvedValue(mockMics);

    const chosen = await pickMic();
    expect(chosen.deviceId).toBeUndefined();
    expect(chosen.label).toBe('Realtek High Definition Audio');
  });

  it('skips virtual endpoints when default is virtual and no Brio is present', async () => {
    const mockMics = [
      { deviceId: 'default', kind: 'audioinput', label: 'Stereo Mix (Virtual Cable)' },
      { deviceId: 'real-mic', kind: 'audioinput', label: 'Realtek Microphone' },
    ];
    (navigator.mediaDevices as any).enumerateDevices = vi.fn().mockResolvedValue(mockMics);

    const chosen = await pickMic();
    expect(chosen.deviceId).toBe('real-mic');
    expect(chosen.label).toBe('Realtek Microphone');
  });
});
