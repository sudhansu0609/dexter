import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import { audioUrl, generateTts, voiceInterrupt } from '../api';
import {
  LocalVoicePipeline,
  listMics,
  savedMicId,
  type ListenReason,
  type MicDevice,
  type VoiceCapabilities,
  type WakeMode,
} from '../voice/localPipeline';

// What the voice layer is doing right now, in the words the UI uses.
//   starting     the mic is being opened and the backend asked what it can do
//   unavailable  no mic, no speech-to-text, or voice is off on the server
//   off          the owner muted it (mic released, nothing spoken)
//   standby      connected, but the other shell window owns the mic
//   idle         listening for the name / open mic / the mic button
//   listening    capturing a command
//   thinking     the command was sent; a reply is on its way
//   speaking     playing Dexter's reply
//   enrolling    learning the owner's voice
export type VoiceState =
  | 'starting'
  | 'unavailable'
  | 'off'
  | 'standby'
  | 'idle'
  | 'listening'
  | 'thinking'
  | 'speaking'
  | 'enrolling';

export interface EnrollProgress {
  active: boolean;
  seconds: number;
  target: number;
  done?: boolean;
  ok?: boolean;
  message?: string;
}

interface VoiceDeps {
  // Which shell window this is; the backend lets one of them own the mic.
  windowName: 'full' | 'popup';
  // Chat owns the "thinking" flag; transcripts that land mid-turn are held back.
  isThinkingRef: MutableRefObject<boolean>;
  // Chat writes the current send handler here each render; the mic (registered
  // once on mount) dispatches recognised commands through it.
  commandHandlerRef: MutableRefObject<(text: string) => void>;
  // Ctrl+Space to talk, Esc to stop Dexter mid-sentence.
  hotkeys?: boolean;
}

const MUTED_KEY = 'dexterVoiceMuted';
const HINT_FLASH_MS = 3500;

function prettyWakeWord(model?: string): string {
  const words = (model || 'dexter').replace(/[_-]+/g, ' ').replace(/\.onnx$/i, '').trim();
  return words.replace(/\b\w/g, (c) => c.toUpperCase());
}

// A short two-note chime so "it heard you" is felt, not read.
function chime(kind: 'up' | 'down'): void {
  try {
    const Ctx = (window as any).AudioContext || (window as any).webkitAudioContext;
    if (!Ctx) return;
    const ctx = new Ctx();
    const notes = kind === 'up' ? [660, 880] : [440, 330];
    notes.forEach((freq, i) => {
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      gain.gain.value = 0.0001;
      osc.connect(gain);
      gain.connect(ctx.destination);
      const t = ctx.currentTime + i * 0.09;
      gain.gain.exponentialRampToValueAtTime(0.12, t + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.0001, t + 0.12);
      osc.start(t);
      osc.stop(t + 0.13);
    });
    setTimeout(() => ctx.close().catch(() => undefined), 400);
  } catch {
    // No audio output is not an error worth surfacing.
  }
}

// Owns the whole hands-free loop for one window: the local pipeline (name
// spotting / open mic / wake phrase + VAD + Whisper on the server), push-to-
// talk, the follow-up window, stop phrases, who-is-talking enrolment, spoken
// replies (Kokoro clips queued as they stream in), and the one-line status the
// UI shows. Voice was removed in v5 and rebuilt here (v10).
export function useVoice({ windowName, isThinkingRef, commandHandlerRef, hotkeys = false }: VoiceDeps) {
  const [muted, setMutedState] = useState<boolean>(() => {
    try {
      return localStorage.getItem(MUTED_KEY) === '1';
    } catch {
      return false;
    }
  });
  const [phase, setPhase] = useState<'starting' | 'unavailable' | 'ready'>('starting');
  const [unavailableReason, setUnavailableReason] = useState('');
  const [active, setActive] = useState(false);
  const [connected, setConnected] = useState(false);
  const [listening, setListening] = useState<ListenReason | null>(null);
  const [thinking, setThinking] = useState(false);
  const [speaking, setSpeaking] = useState(false);
  const [level, setLevel] = useState(0);
  const [caps, setCaps] = useState<VoiceCapabilities | null>(null);
  const [flash, setFlash] = useState<string>('');
  const [micLabel, setMicLabel] = useState('');
  const [micSilent, setMicSilent] = useState(false);
  const [mics, setMics] = useState<MicDevice[]>([]);
  const [micId, setMicId] = useState<string>(() => savedMicId());
  const [enroll, setEnroll] = useState<EnrollProgress>({ active: false, seconds: 0, target: 10 });

  const pipelineRef = useRef<LocalVoicePipeline | null>(null);
  const mutedRef = useRef(muted);
  const speakingRef = useRef(false);
  const listeningRef = useRef<ListenReason | null>(null);
  const lastSpokenTextRef = useRef('');
  const audioRef = useRef<HTMLAudioElement | null>(null);
  // Sentence-chunked audio arrives faster than it plays, so it queues up.
  const audioQueueRef = useRef<Array<{ url: string; revoke?: boolean }>>([]);
  const isPlayingQueueRef = useRef(false);
  const flashTimer = useRef<number | null>(null);
  const activeRef = useRef(false);
  const lastNotYouAt = useRef(0);

  useEffect(() => {
    mutedRef.current = muted;
  }, [muted]);
  useEffect(() => {
    activeRef.current = active;
  }, [active]);

  const showFlash = useCallback((text: string) => {
    setFlash(text);
    if (flashTimer.current) window.clearTimeout(flashTimer.current);
    flashTimer.current = window.setTimeout(() => setFlash(''), HINT_FLASH_MS);
  }, []);

  // Every place that flips the speaking flag must also tell the pipeline, so
  // the server ignores the mic while Dexter talks and opens the follow-up
  // window when it stops.
  const setSpeakingState = useCallback((on: boolean) => {
    speakingRef.current = on;
    setSpeaking(on);
    pipelineRef.current?.setSpeaking(on);
  }, []);

  // ── playback ──

  const finishSpeaking = useCallback(() => {
    isPlayingQueueRef.current = false;
    setSpeakingState(false);
  }, [setSpeakingState]);

  const playNextChunk = useCallback(() => {
    const next = audioQueueRef.current.shift();
    if (!next) {
      finishSpeaking();
      return;
    }
    const audio = new Audio(next.url);
    audioRef.current = audio;
    const done = () => {
      if (next.revoke) URL.revokeObjectURL(next.url);
      playNextChunk();
    };
    audio.onended = done;
    audio.onerror = done;
    audio.play().catch(done);
  }, [finishSpeaking]);

  const enqueueAudio = useCallback(
    (url: string, revoke = false) => {
      if (!url || mutedRef.current) return;
      audioQueueRef.current.push({ url: url.startsWith('blob:') ? url : audioUrl(url), revoke });
      if (isPlayingQueueRef.current) return;
      isPlayingQueueRef.current = true;
      setSpeakingState(true);
      playNextChunk();
    },
    [playNextChunk, setSpeakingState],
  );

  const stopSpeaking = useCallback(
    (cancelGeneration = true) => {
      // Drop anything still queued, otherwise "stop" only silences the current
      // chunk and the next one starts a moment later.
      for (const item of audioQueueRef.current) if (item.revoke) URL.revokeObjectURL(item.url);
      audioQueueRef.current = [];
      isPlayingQueueRef.current = false;
      if (audioRef.current) {
        try {
          audioRef.current.onended = null;
          audioRef.current.onerror = null;
          audioRef.current.pause();
          audioRef.current.currentTime = 0;
        } catch {
          // already stopped
        }
      }
      const wasSpeaking = speakingRef.current;
      setSpeakingState(false);
      if (cancelGeneration && (wasSpeaking || isThinkingRef.current)) {
        voiceInterrupt().catch(() => undefined);
      }
    },
    [isThinkingRef, setSpeakingState],
  );

  // Something Dexter says on its own: a reminder, a check-in. Only the window
  // that owns the mic speaks, or both windows would say it at once.
  const speakText = useCallback(
    async (text: string, emotion?: string, isReminder = false) => {
      if (!text || mutedRef.current || !activeRef.current || phase !== 'ready') return;
      lastSpokenTextRef.current = text;
      try {
        const blob = await generateTts(text, emotion, isReminder);
        enqueueAudio(URL.createObjectURL(blob), true);
      } catch (e) {
        console.warn('[Voice] Could not voice the message', e);
      }
    },
    [enqueueAudio, phase],
  );

  // Any clip handed to us (a voice preview from Settings).
  const playBlob = useCallback(
    (blob: Blob) => {
      stopSpeaking(false);
      enqueueAudio(URL.createObjectURL(blob), true);
    },
    [enqueueAudio, stopSpeaking],
  );

  // ── the pipeline ──

  const refreshMics = useCallback(async () => {
    try {
      setMics(await listMics());
    } catch {
      setMics([]);
    }
  }, []);

  const startPipeline = useCallback(() => {
    if (pipelineRef.current) return;
    setPhase('starting');
    const pipeline = new LocalVoicePipeline(
      {
        onReady: (c) => {
          setCaps(c);
          setPhase('ready');
          refreshMics();
          console.log(`[Voice] ready: stt=${c.model || 'whisper'}/${c.device || '?'} wake=${c.wakeMode}:${c.wakeWord}`);
        },
        onUnavailable: (reason) => {
          console.warn(`[Voice] unavailable: ${reason}`);
          pipelineRef.current = null;
          setUnavailableReason(reason);
          setPhase('unavailable');
          setActive(false);
        },
        onListening: (reason) => {
          listeningRef.current = reason;
          setListening(reason);
          setFlash('');
          if (reason === 'wake' || reason === 'manual' || reason === 'name') chime('up');
        },
        onTranscript: (text, meta) => {
          listeningRef.current = null;
          setListening(null);
          if (isThinkingRef.current) {
            showFlash('One moment, still on the last one.');
            return;
          }
          // The server drops its own echo; a fragment can still sneak through
          // on a slow stop. Anything that is a substring of what Dexter just
          // said was Dexter.
          const lower = text.toLowerCase();
          const lastSpoken = lastSpokenTextRef.current.toLowerCase();
          if (lastSpoken && lower.length > 6 && lastSpoken.includes(lower)) {
            console.log('[Voice] Ignored self-echo transcript:', text);
            return;
          }
          console.log(`[Voice] heard (${meta.after}, ${meta.confidence.toFixed(2)}): ${text}`);
          commandHandlerRef.current(text);
        },
        onCommand: (name) => {
          listeningRef.current = null;
          setListening(null);
          stopSpeaking(true);
          showFlash(name === 'stop' ? 'Stopped.' : 'Okay, never mind.');
        },
        onHeardNothing: (reason, after) => {
          listeningRef.current = null;
          setListening(null);
          if (after === 'followup') return; // nothing was expected; stay quiet
          chime('down');
          showFlash(reason === 'unclear' ? "Didn't catch that. Say it again?" : "I'm listening. Say what you need.");
        },
        onInterrupt: () => {
          // The user pressed the mic or talked over Dexter. Generation is
          // already cancelled server-side; kill playback so the floor is theirs.
          stopSpeaking(false);
        },
        onActiveChange: (isActive) => setActive(isActive),
        onConnectionChange: (isConnected) => setConnected(isConnected),
        onLevel: (v) => setLevel(v),
        onMic: (label) => setMicLabel(label),
        onMicSilent: (silent) => setMicSilent(silent),
        onNotYou: () => {
          listeningRef.current = null;
          setListening(null);
          // Once in a while, not on every sentence a visitor says.
          if (Date.now() - lastNotYouAt.current > 15000) {
            lastNotYouAt.current = Date.now();
            showFlash("That wasn't your voice, so I left it.");
          }
        },
        onEnroll: (evt) => {
          if (evt.type === 'started') {
            setEnroll({ active: true, seconds: 0, target: evt.target || 10 });
          } else if (evt.type === 'progress') {
            setEnroll((p) => ({ ...p, active: true, seconds: evt.seconds || 0, target: evt.target || p.target }));
          } else {
            setEnroll({
              active: false,
              seconds: evt.seconds || 0,
              target: 10,
              done: true,
              ok: Boolean(evt.ok),
              message: evt.ok ? `Got it. I know your voice now (${(evt.seconds || 0).toFixed(0)} s).` : evt.reason || 'That did not work.',
            });
            if (evt.ok) chime('up');
            // The status frame after enrolment carries speakerEnrolled; ask for it.
            pipelineRef.current?.claim();
          }
        },
      },
      windowName,
    );
    pipelineRef.current = pipeline;
    pipeline.start();
  }, [commandHandlerRef, isThinkingRef, refreshMics, showFlash, stopSpeaking, windowName]);

  const stopPipeline = useCallback(() => {
    pipelineRef.current?.stop();
    pipelineRef.current = null;
    setActive(false);
    setConnected(false);
    setListening(null);
    setLevel(0);
    setMicSilent(false);
  }, []);

  useEffect(() => {
    if (!muted) startPipeline();
    return () => stopPipeline();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setMuted = useCallback(
    (next: boolean) => {
      setMutedState(next);
      try {
        localStorage.setItem(MUTED_KEY, next ? '1' : '0');
      } catch {
        // no storage - the choice lasts for this window only
      }
      if (next) {
        stopSpeaking(true);
        stopPipeline();
        setPhase('ready');
      } else {
        startPipeline();
      }
    },
    [startPipeline, stopPipeline, stopSpeaking],
  );

  const toggleMuted = useCallback(() => setMuted(!mutedRef.current), [setMuted]);

  // Push-to-talk: the mic button / Ctrl+Space. Over a reply it is the
  // interruption; while listening it is "never mind"; muted, it unmutes.
  const talkNow = useCallback(() => {
    if (mutedRef.current) {
      setMuted(false);
      return;
    }
    const pipeline = pipelineRef.current;
    if (!pipeline) return;
    if (listeningRef.current) {
      pipeline.stopListening();
      listeningRef.current = null;
      setListening(null);
      return;
    }
    if (speakingRef.current) stopSpeaking(false);
    // Pressing the mic while Dexter is still working on the last thing means
    // "forget that, listen to me": cut the generation so the reply is free.
    if (isThinkingRef.current) voiceInterrupt().catch(() => undefined);
    pipeline.listen();
  }, [isThinkingRef, setMuted, stopSpeaking]);

  // Chat tells us a reply landed. Without speech (nothing to play), open the
  // follow-up window by hand so the next sentence needs no wake word.
  const replyLanded = useCallback((spoke: boolean, replyText: string) => {
    if (replyText) lastSpokenTextRef.current = replyText;
    if (!spoke) pipelineRef.current?.armFollowUp();
  }, []);

  // ── who is talking, how to wake, which mic ──

  const wakeMode: WakeMode = caps?.wakeMode || 'name';
  const setWakeMode = useCallback((mode: WakeMode) => {
    pipelineRef.current?.setMode(mode);
    setCaps((c) => (c ? { ...c, wakeMode: mode } : c));
  }, []);

  const startEnroll = useCallback(() => {
    if (mutedRef.current) setMuted(false);
    stopSpeaking(true);
    setEnroll({ active: true, seconds: 0, target: 10 });
    // The pipeline may still be opening after an unmute; give it a beat.
    window.setTimeout(() => pipelineRef.current?.enroll(), pipelineRef.current ? 0 : 800);
  }, [setMuted, stopSpeaking]);

  const cancelEnroll = useCallback(() => {
    pipelineRef.current?.cancelEnroll();
    setEnroll({ active: false, seconds: 0, target: 10 });
  }, []);

  const selectMic = useCallback(
    async (deviceId: string) => {
      setMicId(deviceId);
      setMicSilent(false);
      await pipelineRef.current?.switchMic(deviceId);
    },
    [],
  );

  // ── hotkeys ──

  useEffect(() => {
    if (!hotkeys) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.code === 'Space' && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        talkNow();
      } else if (e.key === 'Escape' && speakingRef.current) {
        e.preventDefault();
        stopSpeaking(true);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [hotkeys, stopSpeaking, talkNow]);

  // ── the one line the UI shows ──

  const state: VoiceState = muted
    ? 'off'
    : phase === 'unavailable'
      ? 'unavailable'
      : phase === 'starting'
        ? 'starting'
        : enroll.active
          ? 'enrolling'
          : speaking
            ? 'speaking'
            : listening
              ? 'listening'
              : thinking
                ? 'thinking'
                : !active || !connected
                  ? 'standby'
                  : 'idle';

  const wake = prettyWakeWord(caps?.wakeWord);
  const idleHint =
    wakeMode === 'open'
      ? caps?.speakerEnrolled
        ? 'Open mic: just talk. Only your voice counts. Mute when you are done.'
        : 'Open mic: just talk. Mute when you are done.'
      : wakeMode === 'model'
        ? `Say “${wake}”, or press the mic${hotkeys ? ' (Ctrl+Space)' : ''}.`
        : `Start with “${wake}”, or press the mic${hotkeys ? ' (Ctrl+Space)' : ''}.`;
  const hint =
    flash ||
    (state === 'starting'
      ? 'Opening the mic…'
      : state === 'unavailable'
        ? `Voice unavailable: ${unavailableReason}`
        : state === 'off'
          ? 'Voice is off. Press the mic to turn it on.'
          : micSilent && state !== 'speaking'
            ? `The mic (${micLabel || 'default'}) is silent. Pick another under the gear icon.`
            : state === 'standby'
              ? connected
                ? 'The other Dexter window has the mic. Click here to take it.'
                : 'Reconnecting to the voice channel…'
              : state === 'enrolling'
                ? `Learning your voice… ${enroll.seconds.toFixed(0)} / ${enroll.target.toFixed(0)} s. Keep talking.`
                : state === 'listening'
                  ? listening === 'followup'
                    ? 'Go on…'
                    : listening === 'open'
                      ? 'Hearing you…'
                      : 'Listening…'
                  : state === 'thinking'
                    ? 'On it…'
                    : state === 'speaking'
                      ? caps?.bargeIn
                        ? 'Speaking. Talk over me to cut in.'
                        : 'Speaking. Press the mic or Esc to cut in.'
                      : idleHint);

  return {
    state,
    hint,
    level,
    muted,
    active,
    caps,
    wakeWord: wake,
    wakeMode,
    unavailableReason,
    micLabel,
    micSilent,
    mics,
    micId,
    enroll,
    // shared with chat
    mutedRef,
    lastSpokenTextRef,
    setThinking,
    // controls
    toggleMuted,
    setMuted,
    talkNow,
    stopSpeaking,
    enqueueAudio,
    speakText,
    playBlob,
    replyLanded,
    setWakeMode,
    startEnroll,
    cancelEnroll,
    selectMic,
    refreshMics,
    claim: () => pipelineRef.current?.claim(),
    canSpeak: () => !mutedRef.current && phase === 'ready' && activeRef.current,
  };
}

export type VoiceApi = ReturnType<typeof useVoice>;
