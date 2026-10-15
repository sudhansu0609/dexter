import { useCallback, useEffect, useRef, useState, type MutableRefObject } from 'react';
import { audioUrl, generateTts, postVoiceBus, voiceInterrupt, voiceMic } from '../api';
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

// v13: dictation, as the UI needs to know about it. The note's text is never
// mirrored here - it is in the file (or the app) the owner chose.
export interface DictationState {
  active: boolean;
  where: string;
  words: number;
  lines: number;
  paused?: boolean;
}

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
// v13: "mute Dexter" from the pop-up. Separate from MUTED_KEY on purpose -
// muting the *microphone* and muting the *voice* are different wishes, and the
// common one is "stop talking, keep listening" (a call, a recording, a room with
// someone else in it). Shared with the full window through localStorage.
const SILENT_KEY = 'dexterVoiceSilent';
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
  const [silent, setSilentState] = useState<boolean>(() => {
    try {
      return localStorage.getItem(SILENT_KEY) === '1';
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
  // v13: what dictation is doing, for the one-line hint and the pop-up badge.
  const [dictation, setDictation] = useState<DictationState | null>(null);
  // What Dexter just heard, shown in the pop-up the moment it is recognised -
  // before the chat turn resolves and the chat log catches up.
  const [heard, setHeard] = useState<string>('');

  const pipelineRef = useRef<LocalVoicePipeline | null>(null);
  // Set below, once applyMute is defined; the pipeline is built before it.
  const applyMuteRef = useRef<(next: boolean, tellServer: boolean) => void>(() => undefined);
  const mutedRef = useRef(muted);
  const silentRef = useRef(silent);
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
    silentRef.current = silent;
  }, [silent]);
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
      if (!url || mutedRef.current || silentRef.current) return;
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
      if (!text || mutedRef.current || silentRef.current || !activeRef.current || phase !== 'ready') return;
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
          setHeard('');
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
          setHeard(text);
          commandHandlerRef.current(text);
        },
        onCommand: (name) => {
          listeningRef.current = null;
          setListening(null);
          // v13.1: "mute yourself" latches the voice off and leaves the mic open;
          // "stop" only cuts the reply that is playing. Written out rather than
          // calling setSilent, which is defined below this closure.
          if (name === 'mute' || name === 'unmute') {
            const quiet = name === 'mute';
            silentRef.current = quiet;
            setSilentState(quiet);
            try {
              localStorage.setItem(SILENT_KEY, quiet ? '1' : '0');
            } catch {
              // no storage - the choice lasts for this window only
            }
            if (quiet) stopSpeaking(false);
            showFlash(quiet ? 'Muted - I will answer in writing.' : 'Speaking again.');
            return;
          }
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
        // The mic was turned off/on by the other window or the server. Apply it
        // here without echoing it back, and off the socket callback stack so
        // tearing the pipeline down is safe.
        onMicMuted: (isMuted) => {
          if (isMuted === mutedRef.current) return;
          window.setTimeout(() => {
            if (isMuted !== mutedRef.current) applyMuteRef.current(isMuted, false);
          }, 0);
        },
        onNotYou: () => {
          listeningRef.current = null;
          setListening(null);
          // Once in a while, not on every sentence a visitor says.
          if (Date.now() - lastNotYouAt.current > 15000) {
            lastNotYouAt.current = Date.now();
            showFlash("That wasn't your voice, so I left it.");
          }
        },
        // v13: dictation. The words went into the note, so there is nothing to
        // answer - the UI just shows what was written, and says where.
        onDictation: (evt) => {
          listeningRef.current = null;
          setListening(null);
          if (evt.state === 'written') {
            setDictation({ active: true, where: evt.where, words: evt.words, lines: evt.lines });
            showFlash(`Noted: ${evt.line.slice(0, 60)}`);
          } else if (evt.state === 'ended') {
            setDictation(null);
            showFlash(`Note saved - ${evt.words} word(s) in ${evt.where}.`);
            chime('down');
          } else if (evt.state === 'paused') {
            setDictation((p) => (p ? { ...p, paused: true } : p));
            showFlash('Notes paused. Say "resume notes".');
          } else if (evt.state === 'resumed') {
            setDictation((p) => (p ? { ...p, paused: false } : p));
            showFlash('Taking notes again.');
          } else if (evt.state === 'scratched') {
            showFlash('Dropped that line.');
          } else if (evt.state === 'failed') {
            showFlash(`I couldn't write that: ${evt.error.slice(0, 80)}`);
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

  // Turn the mic off / on. `tellServer` mutes the *session* over HTTP, which is
  // what makes "Mic off" actually stop Dexter hearing you: muting used to only
  // close this window's socket, and the backend then handed the still-open mic
  // to the other shell window. `tellServer` is false when we are only reacting
  // to a mute the server (or the other window) already applied.
  const applyMute = useCallback(
    (next: boolean, tellServer: boolean) => {
      mutedRef.current = next;
      setMutedState(next);
      try {
        localStorage.setItem(MUTED_KEY, next ? '1' : '0');
      } catch {
        // no storage - the other window learns via the server broadcast instead
      }
      if (next) {
        stopSpeaking(true);
        const release = () => {
          stopPipeline();
          setPhase('ready');
        };
        if (tellServer) {
          // Mute the session first, *then* let go of our mic - so the moment the
          // other window is handed the mic, the session is already deaf.
          voiceMic(true)
            .catch(() => undefined)
            .finally(release);
        } else {
          release();
        }
      } else {
        if (tellServer) voiceMic(false).catch(() => undefined);
        startPipeline();
      }
    },
    [startPipeline, stopPipeline, stopSpeaking],
  );

  const setMuted = useCallback((next: boolean) => applyMute(next, true), [applyMute]);

  const toggleMuted = useCallback(() => setMuted(!mutedRef.current), [setMuted]);

  // The pipeline (built before applyMute exists) reaches the latest applyMute
  // through this ref, for the server's "mic was muted elsewhere" broadcast.
  useEffect(() => {
    applyMuteRef.current = applyMute;
  }, [applyMute]);

  // A second shell window shares this choice through localStorage; the write
  // there fires a storage event here. Covers the case its socket is already
  // closed (muted) and so cannot get the server broadcast.
  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key !== MUTED_KEY) return;
      const next = e.newValue === '1';
      if (next !== mutedRef.current) applyMute(next, false);
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [applyMute]);

  // Mute Dexter's *voice*, leaving the mic where it was (v13). Anything already
  // queued stops at once, because "mute" that finishes the sentence it was on is
  // not mute.
  const setSilent = useCallback(
    (next: boolean) => {
      setSilentState(next);
      silentRef.current = next;
      try {
        localStorage.setItem(SILENT_KEY, next ? '1' : '0');
      } catch {
        // no storage - the choice lasts for this window only
      }
      if (next) stopSpeaking(false);
    },
    [stopSpeaking],
  );

  const toggleSilent = useCallback(() => setSilent(!silentRef.current), [setSilent]);

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
      } else if (e.key === 'Escape' && (speakingRef.current || isThinkingRef?.current)) {
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

  // Mirror the state to the ai-visualizer face bus. The face only knows four
  // states, so everything that is not actively listening/thinking/speaking reads
  // as idle. Fire-and-forget, deduped, and never allowed to throw into the loop.
  const lastBusRef = useRef<string>('');
  useEffect(() => {
    const face =
      state === 'speaking' || state === 'listening' || state === 'thinking' ? state : 'idle';
    if (face === lastBusRef.current) return;
    lastBusRef.current = face;
    void postVoiceBus(face).catch(() => undefined);
  }, [state]);

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
    // v13: while dictation is on, that is the only thing worth saying - every
    // other hint ("Listening…") would suggest Dexter is about to answer.
    (dictation?.active
      ? dictation.paused
        ? `Notes paused (${dictation.words} words in ${dictation.where}). Say "resume notes".`
        : `Taking notes into ${dictation.where} - ${dictation.words} word(s). Say "stop taking notes".`
      : state === 'starting'
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
    heard,
    muted,
    silent,
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
    dictation,
    // shared with chat
    mutedRef,
    lastSpokenTextRef,
    setThinking,
    // controls
    toggleMuted,
    setMuted,
    toggleSilent,
    setSilent,
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
    canSpeak: () => !mutedRef.current && !silentRef.current && phase === 'ready' && activeRef.current,
  };
}

export type VoiceApi = ReturnType<typeof useVoice>;
