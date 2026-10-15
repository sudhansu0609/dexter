// Client half of the local voice pipeline (v10).
//
// Captures the microphone in the renderer, downsamples to 16 kHz mono int16
// PCM, and streams it as binary WebSocket frames to /api/dexter/voice/ws. The
// backend runs wake word -> VAD -> STT and pushes back JSON voice events
// (listening / transcript / command / heard_nothing / interrupt), surfaced
// through the callbacks below. The mic stays hot while Dexter speaks - the
// server gates it - so the client's obligations are to keep audio flowing,
// report playback state (`speaking`), and say when this window should own the
// mic (`claim`): both shell windows run this pipeline, and the backend lets
// only the most recently shown or focused one hear and be heard.

import { voiceWsUrl } from '../api';

export interface VoiceCapabilities {
  enabled: boolean;
  sttReady: boolean;
  wakeReady: boolean;
  vadReady: boolean;
  model?: string;
  device?: string;
  wakeWord?: string;
  wakeMode?: WakeMode;
  bargeIn?: boolean;
  followUpWindow?: number;
  speakerEnrolled?: boolean;
  speakerStrict?: boolean;
}

export type ListenReason = 'wake' | 'followup' | 'manual' | 'name' | 'open';
export type WakeMode = 'open' | 'name' | 'model' | 'both';

export interface MicDevice {
  deviceId: string;
  label: string;
}

const MIC_KEY = 'dexterMicDeviceId';

// Windows lists virtual and idle endpoints as microphones; when one of these
// is the system default, the mic opens and delivers digital silence.
const VIRTUAL_MIC = /virtual|stereo mix|oculus|mixed reality|rtx voice|hands-free|iphone|what u hear|cable/i;
export const BRIO_MIC = /brio/i;

export function savedMicId(): string {
  try {
    return localStorage.getItem(MIC_KEY) || '';
  } catch {
    return '';
  }
}

export function saveMicId(id: string): void {
  try {
    if (id) localStorage.setItem(MIC_KEY, id);
    else localStorage.removeItem(MIC_KEY);
  } catch {
    // no storage
  }
}

// The microphones the shell can see. Labels are only filled in once the mic
// permission has been granted, which the pipeline does on start.
export async function listMics(): Promise<MicDevice[]> {
  if (!navigator.mediaDevices?.enumerateDevices) return [];
  const all = await navigator.mediaDevices.enumerateDevices();
  return all
    .filter((d) => d.kind === 'audioinput')
    .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Microphone ${i + 1}` }));
}

// Which mic to open: the one chosen in Settings, else Brio if available,
// else the system default unless that looks like a virtual endpoint, in which case the first real one.
export async function pickMic(): Promise<{ deviceId?: string; label: string }> {
  const chosen = savedMicId();
  let mics: MicDevice[] = [];
  try {
    mics = await listMics();
  } catch {
    // enumeration can fail before permission; the default will do
  }
  if (chosen && mics.some((m) => m.deviceId === chosen)) {
    return { deviceId: chosen, label: mics.find((m) => m.deviceId === chosen)!.label };
  }
  // Default mic preference: prioritize Logitech Brio microphone if connected
  const brio = mics.find((m) => BRIO_MIC.test(m.label));
  if (brio) {
    return { deviceId: brio.deviceId, label: brio.label };
  }
  const dflt = mics.find((m) => m.deviceId === 'default') || mics[0];
  if (dflt && VIRTUAL_MIC.test(dflt.label)) {
    const real = mics.find((m) => m.deviceId !== 'default' && m.deviceId !== 'communications' && !VIRTUAL_MIC.test(m.label));
    if (real) return { deviceId: real.deviceId, label: real.label };
  }
  return { deviceId: undefined, label: dflt?.label || 'system default' };
}

// What the voice layer does with a control phrase rather than passing it on.
export type VoiceCommand = 'stop' | 'cancel' | 'mute' | 'unmute';

export interface DictationEvent {
  state: 'written' | 'ended' | 'paused' | 'resumed' | 'scratched' | 'failed';
  line: string;
  words: number;
  lines: number;
  where: string;
  path: string;
  error: string;
}

export interface LocalPipelineCallbacks {
  // The backend can hear and transcribe; capture is running.
  onReady: (caps: VoiceCapabilities) => void;
  // The backend cannot transcribe (deps missing, voice disabled) or the socket
  // / mic could not be opened at all.
  onUnavailable: (reason: string) => void;
  // Capture began (the wake word fired, a follow-up started, or the mic was pressed).
  onListening: (reason: ListenReason) => void;
  onTranscript: (text: string, meta: { after: ListenReason; confidence: number; seconds: number }) => void;
  // "stop" / "cancel" / "mute yourself" / "you can speak again", said on its own.
  onCommand: (name: VoiceCommand) => void;
  // Capture ended with nothing usable.
  onHeardNothing: (reason: 'silence' | 'unclear', after: ListenReason) => void;
  // The user talked over Dexter (or pressed the mic mid-reply); generation is
  // already cancelled server-side.
  onInterrupt: () => void;
  // Whether this window currently owns the mic.
  onActiveChange: (active: boolean) => void;
  // Connection dropped after a successful start (backend restart). The
  // pipeline reconnects by itself; this just lets the UI dim the mic.
  onConnectionChange: (connected: boolean) => void;
  // Input level 0..1, ~10 times a second, so the UI can show it is hearing you.
  onLevel?: (level: number) => void;
  // The mic is open but has delivered only digital silence for a while
  // (wrong device, muted at the OS) - or it has started hearing something.
  onMicSilent?: (silent: boolean) => void;
  // Someone other than the enrolled owner spoke to Dexter.
  onNotYou?: (score: number) => void;
  // v13: dictation is running and a line was written (or it ended, paused, or
  // could not be written). Never a chat turn - the words went into the note.
  onDictation?: (event: DictationEvent) => void;
  // Enrolment ("Learn my voice") progress and result.
  onEnroll?: (evt: { type: 'started' | 'progress' | 'done'; seconds?: number; target?: number; ok?: boolean; reason?: string }) => void;
  // Which mic was opened.
  onMic?: (label: string) => void;
  // The session's mic was turned off/on (from this window or the other one).
  // Both windows are told, so each can release the mic device and show it.
  onMicMuted?: (muted: boolean) => void;
}

const TARGET_SAMPLE_RATE = 16000;
const RECONNECT_DELAY_MS = 4000;

// AudioWorklet source, inlined so no extra asset needs to survive the Vite
// build. It batches the 128-sample render quanta into ~128 ms blocks before
// posting, keeping message traffic (and WS frames) low.
const WORKLET_SOURCE = `
class PcmCaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._chunks = [];
    this._length = 0;
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel && channel.length) {
      this._chunks.push(new Float32Array(channel));
      this._length += channel.length;
      if (this._length >= 2048) {
        const out = new Float32Array(this._length);
        let offset = 0;
        for (const chunk of this._chunks) {
          out.set(chunk, offset);
          offset += chunk.length;
        }
        this._chunks = [];
        this._length = 0;
        this.port.postMessage(out, [out.buffer]);
      }
    }
    return true;
  }
}
registerProcessor('pcm-capture', PcmCaptureProcessor);
`;

function floatTo16BitPcm(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    const s = Math.max(-1, Math.min(1, input[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

// Naive decimation for the rare case where the AudioContext refuses to run at
// 16 kHz (some devices pin the context to the hardware rate).
function resampleTo16k(input: Float32Array, fromRate: number): Float32Array {
  if (fromRate === TARGET_SAMPLE_RATE) return input;
  const ratio = fromRate / TARGET_SAMPLE_RATE;
  const length = Math.floor(input.length / ratio);
  const out = new Float32Array(length);
  for (let i = 0; i < length; i++) {
    out[i] = input[Math.floor(i * ratio)];
  }
  return out;
}

function rms(input: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < input.length; i++) sum += input[i] * input[i];
  return Math.sqrt(sum / (input.length || 1));
}

export class LocalVoicePipeline {
  private callbacks: LocalPipelineCallbacks;
  private session: string;
  private windowName: string;

  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private processorNode: ScriptProcessorNode | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;

  private started = false;
  private everConnected = false;
  private speaking = false;
  private active = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;
  private lastLevelAt = 0;
  private detachVisibility: (() => void) | null = null;
  private peakSince = 0;
  private peakMax = 0;
  private silentReported: boolean | null = null;
  // The mic-off state this window wants the session in, re-asserted on every
  // (re)connect so a backend restart cannot silently un-mute the mic.
  private desiredMuted = false;

  constructor(callbacks: LocalPipelineCallbacks, windowName = 'full', session = 'default') {
    this.callbacks = callbacks;
    this.windowName = windowName;
    this.session = session;
  }

  get isActive(): boolean {
    return this.active;
  }

  async start(): Promise<void> {
    if (this.started) return;
    this.started = true;
    try {
      await this.openMic();
    } catch (e) {
      this.started = false;
      this.callbacks.onUnavailable(`microphone unavailable: ${e}`);
      return;
    }
    if (!this.started) {
      // stop() ran while the mic was opening (a re-mount): let go of it.
      this.stop();
      return;
    }
    this.watchVisibility();
    this.connect();
  }

  stop(): void {
    this.started = false;
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
    this.detachVisibility?.();
    this.detachVisibility = null;
    if (this.ws) {
      try {
        this.ws.onclose = null;
        this.ws.close();
      } catch {
        // already closed
      }
      this.ws = null;
    }
    this.workletNode?.disconnect();
    this.processorNode?.disconnect();
    this.sourceNode?.disconnect();
    this.workletNode = null;
    this.processorNode = null;
    this.sourceNode = null;
    this.context?.close().catch(() => undefined);
    this.context = null;
    this.stream?.getTracks().forEach((t) => t.stop());
    this.stream = null;
    this.active = false;
  }

  // ── controls ──

  setSpeaking(on: boolean): void {
    this.speaking = on;
    this.sendControl({ type: 'speaking', on });
  }

  // Mic off/on for the whole session (the "Mic off" switch). Goes over the
  // socket to the session, so it holds even when the mic is handed to the other
  // window; remembered so it is re-asserted after a reconnect.
  setMuted(on: boolean): void {
    this.desiredMuted = on;
    this.sendControl({ type: 'mute', on });
  }

  // Push-to-talk: capture now, no wake word. Interrupts a reply in progress.
  listen(): void {
    this.claim();
    this.sendControl({ type: 'listen' });
  }

  // Drop any capture and cut any reply.
  stopListening(): void {
    this.sendControl({ type: 'stop' });
  }

  // A reply landed without speech (voice output off): open the follow-up
  // window anyway so the next sentence needs no wake word.
  armFollowUp(): void {
    this.sendControl({ type: 'followup' });
  }

  // This window should own the mic (it was shown, focused, or pressed).
  claim(): void {
    this.sendControl({ type: 'claim' });
  }

  // "Learn my voice": collect ~10 s of the owner talking.
  enroll(seconds?: number): void {
    this.claim();
    this.sendControl({ type: 'enroll', seconds });
  }

  cancelEnroll(): void {
    this.sendControl({ type: 'enroll_cancel' });
  }

  // open (just talk) / name (say "Dexter") / model / both - saved server-side.
  setMode(mode: WakeMode): void {
    this.sendControl({ type: 'mode', mode });
  }

  // Re-open the capture on a different microphone.
  async switchMic(deviceId: string): Promise<void> {
    saveMicId(deviceId);
    if (!this.started) return;
    this.stop();
    await this.start();
  }

  // ───────────────────────── internals ─────────────────────────

  private async openMic(): Promise<void> {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('getUserMedia is not available in this shell');
    }
    // Echo cancellation keeps Dexter's own voice out of the mic (the server's
    // echo gating is coarse) and improves barge-in VAD.
    const mic = await pickMic();
    const audio: MediaTrackConstraints = {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    };
    if (mic.deviceId) audio.deviceId = { exact: mic.deviceId };
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({ audio });
    } catch (e) {
      if (!mic.deviceId) throw e;
      // The remembered mic is gone: fall back to the default rather than die.
      saveMicId('');
      this.stream = await navigator.mediaDevices.getUserMedia({ audio: { ...audio, deviceId: undefined } });
    }
    let track = this.stream.getAudioTracks()[0];
    let label = track?.label || mic.label;

    // If no specific mic was saved and we didn't catch Brio on the first pass
    // (e.g. device labels were not yet exposed prior to getUserMedia permission),
    // re-enumerate now that permission is granted and switch to Brio if available:
    if (!savedMicId() && !BRIO_MIC.test(label)) {
      try {
        const freshMics = await listMics();
        const brio = freshMics.find((m) => BRIO_MIC.test(m.label));
        if (brio && brio.deviceId) {
          try {
            const brioStream = await navigator.mediaDevices.getUserMedia({
              audio: { ...audio, deviceId: { exact: brio.deviceId } },
            });
            this.stream.getTracks().forEach((t) => t.stop());
            this.stream = brioStream;
            track = this.stream.getAudioTracks()[0];
            label = track?.label || brio.label;
          } catch {
            // Keep current stream if Brio switch fails
          }
        }
      } catch {
        // enumeration can fail silently
      }
    }

    this.callbacks.onMic?.(label);
    this.peakSince = Date.now();
    this.peakMax = 0;
    this.silentReported = null;

    let context: AudioContext;
    try {
      context = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
    } catch {
      context = new AudioContext();
    }
    this.context = context;
    this.sourceNode = context.createMediaStreamSource(this.stream);

    const handleBlock = (block: Float32Array) => {
      const now = Date.now();
      const level = rms(block);
      if (this.callbacks.onLevel && now - this.lastLevelAt > 90) {
        this.lastLevelAt = now;
        // Speech peaks around 0.1-0.3 RMS; scale so a normal voice fills the meter.
        this.callbacks.onLevel(Math.min(1, level * 6));
      }
      // A real mic's noise floor is ~0.001-0.01 RMS; a virtual or muted one
      // sits at ~0.0003. Report after 6 s so Settings can say "pick another".
      this.peakMax = Math.max(this.peakMax, level);
      if (now - this.peakSince > 6000) {
        const silent = this.peakMax < 0.0015;
        if (silent !== this.silentReported) {
          this.silentReported = silent;
          this.callbacks.onMicSilent?.(silent);
        }
        this.peakSince = now;
        this.peakMax = 0;
      }
      if (!this.active || !this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      const at16k = resampleTo16k(block, context.sampleRate);
      this.ws.send(floatTo16BitPcm(at16k).buffer);
    };

    if (context.audioWorklet) {
      const moduleUrl = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: 'application/javascript' }));
      try {
        await context.audioWorklet.addModule(moduleUrl);
      } finally {
        URL.revokeObjectURL(moduleUrl);
      }
      this.workletNode = new AudioWorkletNode(context, 'pcm-capture');
      this.workletNode.port.onmessage = (evt: MessageEvent<Float32Array>) => handleBlock(evt.data);
      this.sourceNode.connect(this.workletNode);
    } else {
      // ScriptProcessor is deprecated but still the only fallback where
      // AudioWorklet is missing.
      this.processorNode = context.createScriptProcessor(4096, 1, 1);
      this.processorNode.onaudioprocess = (evt) => handleBlock(new Float32Array(evt.inputBuffer.getChannelData(0)));
      this.sourceNode.connect(this.processorNode);
      this.processorNode.connect(context.destination);
    }

    // A freshly created AudioContext may start "suspended": its graph does not
    // run, so the backend hears silence until it is resumed - which the shell
    // may only permit after a user gesture. Resume now, else on first click.
    try {
      await context.resume();
    } catch {
      // Autoplay policy wants a gesture; handled below.
    }
    if (context.state !== 'running') {
      const resumeOnGesture = () => {
        context.resume().catch(() => undefined);
        if (context.state === 'running') {
          window.removeEventListener('pointerdown', resumeOnGesture);
          window.removeEventListener('keydown', resumeOnGesture);
        }
      };
      window.addEventListener('pointerdown', resumeOnGesture);
      window.addEventListener('keydown', resumeOnGesture);
    }
    console.log('[Voice] capture AudioContext:', context.state, '@', context.sampleRate, 'Hz');
  }

  private watchVisibility(): void {
    // The window that comes to the front is the one the owner is talking to.
    const onVisible = () => {
      if (document.visibilityState === 'visible') this.claim();
    };
    const onFocus = () => this.claim();
    document.addEventListener('visibilitychange', onVisible);
    window.addEventListener('focus', onFocus);
    this.detachVisibility = () => {
      document.removeEventListener('visibilitychange', onVisible);
      window.removeEventListener('focus', onFocus);
    };
  }

  private connect(): void {
    if (!this.started) return;
    voiceWsUrl(this.windowName, this.session)
      .then((url) => {
        if (!this.started) return;
        const ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';
        this.ws = ws;

        ws.onmessage = (evt: MessageEvent) => this.handleMessage(evt);
        ws.onopen = () => {
          this.callbacks.onConnectionChange(true);
          // A visible window takes the mic; a hidden one stays on standby
          // until it is shown. Re-assert playback state after a reconnect so
          // the fresh server session doesn't treat TTS output as a command.
          if (document.visibilityState === 'visible') this.claim();
          if (this.speaking) this.sendControl({ type: 'speaking', on: true });
          // Re-assert mic-off after a reconnect (never auto-unmute) so a backend
          // restart cannot bring the mic back on behind the owner's back.
          if (this.desiredMuted) this.sendControl({ type: 'mute', on: true });
        };
        ws.onclose = () => this.handleDisconnect();
        ws.onerror = () => {
          // onclose always follows; nothing to do here.
        };
      })
      .catch(() => this.handleDisconnect());
  }

  private handleMessage(evt: MessageEvent): void {
    let msg: Record<string, any>;
    try {
      msg = JSON.parse(String(evt.data));
    } catch {
      return;
    }
    switch (msg.type) {
      case 'status': {
        const caps: VoiceCapabilities = {
          enabled: msg.enabled !== false,
          sttReady: Boolean(msg.sttReady),
          wakeReady: Boolean(msg.wakeReady),
          vadReady: Boolean(msg.vadReady),
          model: typeof msg.model === 'string' ? msg.model : undefined,
          device: typeof msg.device === 'string' ? msg.device : undefined,
          wakeWord: typeof msg.wakeWord === 'string' ? msg.wakeWord : undefined,
          wakeMode: (msg.wakeMode as WakeMode) || 'name',
          bargeIn: Boolean(msg.bargeIn),
          followUpWindow: typeof msg.followUpWindow === 'number' ? msg.followUpWindow : undefined,
          speakerEnrolled: Boolean(msg.speaker?.enrolled),
          speakerStrict: Boolean(msg.speaker?.strict),
        };
        if (!caps.enabled) {
          this.stop();
          this.callbacks.onUnavailable('voice is turned off on the server (DEXTER_VOICE=0)');
          return;
        }
        // Without STT the pipeline can never produce a transcript; without VAD
        // it cannot tell when you stopped talking.
        if (!caps.sttReady || !caps.vadReady) {
          this.stop();
          this.callbacks.onUnavailable(
            !caps.sttReady ? 'speech-to-text is not installed (faster-whisper)' : 'voice detection is not installed (silero-vad)',
          );
          return;
        }
        this.everConnected = true;
        this.setActive(Boolean(msg.active));
        this.callbacks.onReady(caps);
        if (typeof msg.muted === 'boolean') {
          this.desiredMuted = msg.muted;
          this.callbacks.onMicMuted?.(msg.muted);
        }
        break;
      }
      case 'active':
        this.setActive(true);
        break;
      case 'mic_muted':
        this.desiredMuted = Boolean(msg.muted);
        this.callbacks.onMicMuted?.(Boolean(msg.muted));
        break;
      case 'standby':
        this.setActive(false);
        break;
      case 'listening':
        this.callbacks.onListening((msg.reason as ListenReason) || 'wake');
        break;
      case 'transcript':
        if (typeof msg.text === 'string' && msg.text.trim()) {
          this.callbacks.onTranscript(msg.text.trim(), {
            after: (msg.after as ListenReason) || 'wake',
            confidence: Number(msg.confidence) || 0,
            seconds: Number(msg.seconds) || 0,
          });
        }
        break;
      case 'command':
        if (msg.name === 'stop' || msg.name === 'cancel' || msg.name === 'mute' || msg.name === 'unmute') {
          this.callbacks.onCommand(msg.name as VoiceCommand);
        }
        break;
      case 'heard_nothing':
        this.callbacks.onHeardNothing(msg.reason === 'unclear' ? 'unclear' : 'silence', (msg.after as ListenReason) || 'wake');
        break;
      case 'interrupt':
        this.callbacks.onInterrupt();
        break;
      case 'not_you':
        this.callbacks.onNotYou?.(Number(msg.score) || 0);
        break;
      case 'dictation':
        this.callbacks.onDictation?.({
          state: (msg.state as DictationEvent['state']) || 'written',
          line: typeof msg.line === 'string' ? msg.line : '',
          words: Number(msg.words) || 0,
          lines: Number(msg.lines) || 0,
          where: typeof msg.where === 'string' ? msg.where : '',
          path: typeof msg.path === 'string' ? msg.path : '',
          error: typeof msg.error === 'string' ? msg.error : '',
        });
        break;
      case 'enroll_started':
        this.callbacks.onEnroll?.({ type: 'started', target: Number(msg.seconds) || 10 });
        break;
      case 'enroll_progress':
        this.callbacks.onEnroll?.({ type: 'progress', seconds: Number(msg.seconds) || 0, target: Number(msg.target) || 10 });
        break;
      case 'enrolled':
        this.callbacks.onEnroll?.({
          type: 'done',
          ok: Boolean(msg.ok),
          seconds: Number(msg.seconds) || 0,
          reason: typeof msg.reason === 'string' ? msg.reason : undefined,
        });
        break;
      default:
        break;
    }
  }

  private setActive(active: boolean): void {
    if (this.active === active) return;
    this.active = active;
    this.callbacks.onActiveChange(active);
  }

  private handleDisconnect(): void {
    this.ws = null;
    this.setActive(false);
    if (!this.started) return;
    this.callbacks.onConnectionChange(false);
    if (!this.everConnected) {
      // Never got a healthy session - treat as unavailable so the caller can
      // say so instead of retrying into the void.
      this.stop();
      this.callbacks.onUnavailable('could not reach the voice channel');
      return;
    }
    this.retryTimer = setTimeout(() => this.connect(), RECONNECT_DELAY_MS);
  }

  private sendControl(payload: Record<string, unknown>): void {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      try {
        this.ws.send(JSON.stringify(payload));
      } catch {
        // socket is closing; the reconnect handler owns recovery
      }
    }
  }
}
