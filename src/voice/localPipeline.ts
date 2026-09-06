// Client half of the local voice pipeline (roadmap Phase 4, item 4.2).
//
// Captures the microphone in the renderer, downsamples to 16 kHz mono int16
// PCM, and streams it as binary WebSocket frames to /api/dexter/voice/ws. The
// backend runs wake word -> VAD -> STT and pushes back JSON voice events
// (wake / speech_start / transcript / interrupt) which are surfaced through
// the callbacks below. The mic stays hot while Dexter speaks - that is what
// makes server-side barge-in possible - so the client's only obligations are
// to keep the audio flowing and to report TTS playback state via the
// `speaking` control message.

import { voiceWsUrl } from '../api';

export interface VoiceCapabilities {
  sttReady: boolean;
  wakeReady: boolean;
  vadReady: boolean;
  model?: string;
}

export interface LocalPipelineCallbacks {
  // The backend can hear and transcribe; capture is running.
  onReady: (caps: VoiceCapabilities) => void;
  // The backend reported it cannot transcribe (deps missing) or the socket /
  // mic could not be opened at all. The caller should fall back to the
  // browser recognizer.
  onUnavailable: (reason: string) => void;
  onWake: () => void;
  onTranscript: (text: string) => void;
  // The user talked over Dexter; generation is already cancelled server-side.
  onInterrupt: () => void;
  // Connection dropped after a successful start (backend restart). The
  // pipeline reconnects by itself; this just lets the UI dim the mic icon.
  onConnectionChange: (connected: boolean) => void;
}

const TARGET_SAMPLE_RATE = 16000;
const RECONNECT_DELAY_MS = 4000;

// AudioWorklet source, inlined so no extra asset needs to survive the Vite
// build. It batches the 128-sample render quanta into ~128ms blocks before
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

export class LocalVoicePipeline {
  private callbacks: LocalPipelineCallbacks;
  private session: string;

  private ws: WebSocket | null = null;
  private stream: MediaStream | null = null;
  private context: AudioContext | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private processorNode: ScriptProcessorNode | null = null;
  private sourceNode: MediaStreamAudioSourceNode | null = null;

  private started = false;
  private everConnected = false;
  private speaking = false;
  private retryTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(callbacks: LocalPipelineCallbacks, session = 'default') {
    this.callbacks = callbacks;
    this.session = session;
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
    this.connect();
  }

  stop(): void {
    this.started = false;
    if (this.retryTimer) {
      clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
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
  }

  setSpeaking(on: boolean): void {
    this.speaking = on;
    this.sendControl({ type: 'speaking', on });
  }

  // ───────────────────────── internals ─────────────────────────

  private async openMic(): Promise<void> {
    // Echo cancellation matters twice over here: it keeps Dexter's own TTS out
    // of the mic (server-side echo gating is coarse) and improves barge-in VAD.
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
    });

    let context: AudioContext;
    try {
      context = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
    } catch {
      context = new AudioContext();
    }
    this.context = context;
    this.sourceNode = context.createMediaStreamSource(this.stream);

    const handleBlock = (block: Float32Array) => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      const at16k = resampleTo16k(block, context.sampleRate);
      this.ws.send(floatTo16BitPcm(at16k).buffer);
    };

    if (context.audioWorklet) {
      const moduleUrl = URL.createObjectURL(
        new Blob([WORKLET_SOURCE], { type: 'application/javascript' }),
      );
      try {
        await context.audioWorklet.addModule(moduleUrl);
      } finally {
        URL.revokeObjectURL(moduleUrl);
      }
      this.workletNode = new AudioWorkletNode(context, 'pcm-capture');
      this.workletNode.port.onmessage = (evt: MessageEvent<Float32Array>) =>
        handleBlock(evt.data);
      this.sourceNode.connect(this.workletNode);
    } else {
      // ScriptProcessor is deprecated but still the only fallback where
      // AudioWorklet is missing.
      this.processorNode = context.createScriptProcessor(4096, 1, 1);
      this.processorNode.onaudioprocess = (evt) =>
        handleBlock(new Float32Array(evt.inputBuffer.getChannelData(0)));
      this.sourceNode.connect(this.processorNode);
      this.processorNode.connect(context.destination);
    }

    // A freshly created AudioContext starts "suspended": its graph does not run,
    // so no mic audio is captured and the backend hears silence (the wake word
    // never fires) until it is resumed - which the shell may only permit after a
    // user gesture. Resume now, and fall back to the first interaction otherwise.
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

  private connect(): void {
    if (!this.started) return;
    voiceWsUrl(this.session)
      .then((url) => {
        if (!this.started) return;
        const ws = new WebSocket(url);
        ws.binaryType = 'arraybuffer';
        this.ws = ws;

        ws.onmessage = (evt: MessageEvent) => this.handleMessage(evt);
        ws.onopen = () => {
          this.callbacks.onConnectionChange(true);
          // Re-assert playback state after a reconnect so the fresh server
          // session doesn't treat TTS output as a command.
          if (this.speaking) this.sendControl({ type: 'speaking', on: true });
        };
        ws.onclose = () => this.handleDisconnect();
        ws.onerror = () => {
          // onclose always follows; nothing to do here.
        };
      })
      .catch(() => this.handleDisconnect());
  }

  private handleMessage(evt: MessageEvent): void {
    let msg: Record<string, unknown>;
    try {
      msg = JSON.parse(String(evt.data));
    } catch {
      return;
    }
    switch (msg.type) {
      case 'status': {
        const caps: VoiceCapabilities = {
          sttReady: Boolean(msg.sttReady),
          wakeReady: Boolean(msg.wakeReady),
          vadReady: Boolean(msg.vadReady),
          model: typeof msg.model === 'string' ? msg.model : undefined,
        };
        // Without STT the pipeline can never produce a transcript - tell the
        // caller to fall back rather than sit deaf. Wake word / VAD degrade
        // more gracefully server-side.
        if (!caps.sttReady || !caps.vadReady) {
          this.stop();
          this.callbacks.onUnavailable(
            `server pipeline not ready (stt=${caps.sttReady}, vad=${caps.vadReady})`,
          );
          return;
        }
        this.everConnected = true;
        this.callbacks.onReady(caps);
        break;
      }
      case 'wake':
        this.callbacks.onWake();
        break;
      case 'transcript':
        if (typeof msg.text === 'string' && msg.text.trim()) {
          this.callbacks.onTranscript(msg.text.trim());
        }
        break;
      case 'interrupt':
        this.callbacks.onInterrupt();
        break;
      default:
        break;
    }
  }

  private handleDisconnect(): void {
    this.ws = null;
    if (!this.started) return;
    this.callbacks.onConnectionChange(false);
    if (!this.everConnected) {
      // Never got a healthy session - treat as unavailable so the caller can
      // fall back instead of retrying into the void.
      this.stop();
      this.callbacks.onUnavailable('voice socket could not connect');
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
