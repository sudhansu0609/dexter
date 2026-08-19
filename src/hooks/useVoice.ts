import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { audioUrl, generateTts } from '../api';

const FOLLOW_UP_WINDOW_MS = 9000;

interface VoiceDeps {
  // Chat owns the "thinking" flag; the mic restart guards read it so the mic
  // does not resume while a turn is still being generated.
  isThinkingRef: MutableRefObject<boolean>;
  // Chat writes the current send handler here each render; the mic (registered
  // once on mount) dispatches recognised commands through it.
  commandHandlerRef: MutableRefObject<(text: string) => void>;
}

// Owns the entire hands-free voice loop: continuous webkitSpeechRecognition,
// wake word + conversational follow-up window, self-echo filtering, barge-in,
// Kokoro TTS playback, and the streamed-speech audio queue. Extracted verbatim
// from the App monolith so behaviour is unchanged (roadmap Phase 6, item 9).
export function useVoice({ isThinkingRef, commandHandlerRef }: VoiceDeps) {
  const [isVoiceMuted, setIsVoiceMuted] = useState(false);
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);

  const recognitionRef = useRef<any>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const isSpeakingRef = useRef(false);
  const isVoiceMutedRef = useRef(false);
  const lastSpeechEndTimeRef = useRef<number>(0);
  const lastSpokenTextRef = useRef<string>('');
  // Sentence-chunked audio arrives faster than it plays, so it queues up.
  const audioQueueRef = useRef<string[]>([]);
  const isPlayingQueueRef = useRef(false);
  // Grace period after Dexter speaks during which no wake word is needed, so a
  // follow-up ("and close it") works the way a conversation actually goes.
  const followUpUntilRef = useRef<number>(0);

  useEffect(() => {
    isSpeakingRef.current = isSpeaking;
  }, [isSpeaking]);

  useEffect(() => {
    isVoiceMutedRef.current = isVoiceMuted;
    if (isVoiceMuted) {
      stopListening();
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    } else {
      startListening();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isVoiceMuted]);

  useEffect(() => {
    initSpeechRecognition();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopSpeaking = () => {
    // Drop anything still queued, otherwise "stop" only silences the current
    // chunk and the next one starts playing a moment later.
    audioQueueRef.current = [];
    isPlayingQueueRef.current = false;
    if (audioRef.current) {
      try {
        audioRef.current.onended = null;
        audioRef.current.onerror = null;
        audioRef.current.pause();
        audioRef.current.currentTime = 0;
      } catch (e) {
        console.warn('Could not stop current audio', e);
      }
    }
    if ('speechSynthesis' in window) {
      try {
        window.speechSynthesis.cancel();
      } catch (e) {
        console.warn('Could not cancel speech synthesis', e);
      }
    }
    setIsSpeaking(false);
    isSpeakingRef.current = false;
    lastSpeechEndTimeRef.current = Date.now();
    setTimeout(() => {
      if (!isVoiceMutedRef.current && !isThinkingRef.current) {
        startListening();
      }
    }, 800);
  };

  // Continuous Hands-Free Speech Recognition Loop
  const initSpeechRecognition = () => {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      const SpeechRecognition =
        (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      const rec = new SpeechRecognition();
      rec.continuous = true;
      rec.interimResults = true; // Enable interim results to detect 'stop' instantly
      rec.lang = 'en-US';

      rec.onresult = (e: any) => {
        const lastResult = e.results[e.results.length - 1];
        if (!lastResult) return;

        const rawTranscript = (lastResult[0]?.transcript || '').trim().toLowerCase();

        // ── Voice Interrupt Detection while Dexter is speaking ──
        if (isSpeakingRef.current) {
          const INTERRUPT_KEYWORDS = [
            'stop',
            'shut up',
            'quiet',
            'pause',
            'be quiet',
            'stop speaking',
            'stop talking',
            'cancel',
          ];
          if (INTERRUPT_KEYWORDS.some((k) => rawTranscript.includes(k))) {
            console.log('[Voice Interrupt] Stop keyword detected while speaking:', rawTranscript);
            stopSpeaking();
            return;
          }
          return;
        }

        if (isVoiceMutedRef.current || isThinkingRef.current) return;
        if (Date.now() - lastSpeechEndTimeRef.current < 1500) return;

        if (lastResult.isFinal) {
          const rawText = lastResult[0].transcript.trim();
          if (rawText.length < 3) return;

          const lowerText = rawText.toLowerCase();

          // ── Wake word, with a conversational follow-up window ──
          const WAKE_WORDS = ['dexter', 'hey dexter', 'ok dexter', 'okay dexter', 'hi dexter'];
          const hasWakeWord = WAKE_WORDS.some((w) => lowerText.includes(w));
          // Right after Dexter finishes speaking, accept a follow-up without the
          // wake word. Saying "Dexter" before every single sentence is the main
          // thing that made this feel like a command line rather than a chat.
          const inFollowUp = Date.now() < followUpUntilRef.current;

          if (!hasWakeWord && !inFollowUp) {
            console.log('[Wake Word Filter] Ignored ambient speech (no "Dexter" detected):', rawText);
            return;
          }

          // Extract clean command by stripping wake word prefixes/suffixes
          let cleanCommand = rawText.replace(/^(hey|ok|okay|hi)?\s*dexter[,!:]?\s*/i, '');
          cleanCommand = cleanCommand.replace(/\s*dexter[,!:]?\s*$/i, '').trim();

          // Self-Echo / Deduplication Filter
          const cleanTranscript = (cleanCommand || rawText).toLowerCase();
          const cleanLastSpoken = lastSpokenTextRef.current.toLowerCase();
          if (cleanLastSpoken && cleanLastSpoken.includes(cleanTranscript)) {
            console.log('[Mic Filter] Ignored self-echo transcript:', cleanTranscript);
            return;
          }

          // If the user just said "Dexter" or "Hey Dexter" with no extra command
          if (!cleanCommand || cleanCommand.length < 2) {
            speakText('Yes? I am listening.');
            return;
          }

          // Consumed - a follow-up must be earned by Dexter replying again.
          followUpUntilRef.current = 0;
          commandHandlerRef.current(cleanCommand);
        }
      };

      rec.onerror = () => {
        // Silently handle error
      };

      rec.onend = () => {
        setIsListening(false);
        // Only restart if idle, not muted, not speaking, not thinking, and past cooldown
        if (
          !isVoiceMutedRef.current &&
          !isSpeakingRef.current &&
          !isThinkingRef.current &&
          Date.now() - lastSpeechEndTimeRef.current >= 1500
        ) {
          setTimeout(() => {
            startListening();
          }, 400);
        }
      };

      recognitionRef.current = rec;
      startListening();
    }
  };

  const startListening = () => {
    if (recognitionRef.current && !isVoiceMutedRef.current) {
      try {
        recognitionRef.current.start();
        setIsListening(true);
      } catch (e) {
        // start() throws if recognition is already running - safe to ignore.
      }
    }
  };

  const stopListening = () => {
    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop();
        setIsListening(false);
      } catch (e) {
        console.warn('Could not stop recognition', e);
      }
    }
  };

  const toggleVoiceMute = () => {
    if (isSpeaking) {
      stopSpeaking();
    }
    setIsVoiceMuted((prev) => !prev);
  };

  // ── Sequential playback of streamed speech chunks ──

  const finishSpeaking = () => {
    isPlayingQueueRef.current = false;
    setIsSpeaking(false);
    isSpeakingRef.current = false;
    lastSpeechEndTimeRef.current = Date.now();
    // Dexter just spoke, so a reply is expected - drop the wake-word
    // requirement briefly to allow a natural follow-up.
    followUpUntilRef.current = Date.now() + FOLLOW_UP_WINDOW_MS;
    setTimeout(() => {
      if (!isVoiceMutedRef.current && !isSpeakingRef.current && !isThinkingRef.current) {
        startListening();
      }
    }, 1200);
  };

  const playNextChunk = () => {
    const next = audioQueueRef.current.shift();
    if (!next) {
      finishSpeaking();
      return;
    }

    const audio = new Audio(audioUrl(next));
    audioRef.current = audio;
    audio.onended = playNextChunk;
    audio.onerror = playNextChunk;
    audio.play().catch(() => playNextChunk());
  };

  const enqueueAudio = (url: string) => {
    if (!url || isVoiceMutedRef.current) return;
    audioQueueRef.current.push(url);
    if (isPlayingQueueRef.current) return;

    isPlayingQueueRef.current = true;
    stopListening();
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort();
      } catch (e) {
        // abort() throws if not started - safe to ignore.
      }
    }
    setIsSpeaking(true);
    isSpeakingRef.current = true;
    playNextChunk();
  };

  const speakText = async (text: string, emotion?: string, isReminder = false) => {
    if (isVoiceMutedRef.current) {
      startListening();
      return;
    }

    // Save spoken text ref for deduplication filtering
    lastSpokenTextRef.current = text;

    // 1. Immediately abort mic recognition & set speaking flags
    stopListening();
    if (recognitionRef.current) {
      try {
        recognitionRef.current.abort();
      } catch (e) {
        // abort() throws if not started - safe to ignore.
      }
    }

    setIsSpeaking(true);
    isSpeakingRef.current = true;

    try {
      // 2. Fetch Kokoro ONNX audio from backend endpoint
      const blob = await generateTts(text, emotion, isReminder);
      const url = URL.createObjectURL(blob);

      if (audioRef.current) {
        audioRef.current.pause();
      }

      const audio = new Audio(url);
      audioRef.current = audio;

      // Every spoken reply used to leak its blob URL for the lifetime of the
      // session; release it once playback settles either way.
      const release = () => URL.revokeObjectURL(url);

      audio.onended = () => {
        release();
        setIsSpeaking(false);
        isSpeakingRef.current = false;
        lastSpeechEndTimeRef.current = Date.now();
        // Resume listening after 1.5s cooldown so mic doesn't catch lingering room echo
        setTimeout(() => {
          if (!isVoiceMutedRef.current && !isSpeakingRef.current && !isThinkingRef.current) {
            startListening();
          }
        }, 1500);
      };

      audio.onerror = () => {
        release();
        setIsSpeaking(false);
        isSpeakingRef.current = false;
        lastSpeechEndTimeRef.current = Date.now();
        if (!isVoiceMutedRef.current) startListening();
      };

      await audio.play();
    } catch (e) {
      console.warn('Backend Kokoro TTS playback error, falling back to browser synthesis', e);
      // Fallback
      if ('speechSynthesis' in window && !isVoiceMutedRef.current) {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text.substring(0, 200));
        utterance.onend = () => {
          setIsSpeaking(false);
          isSpeakingRef.current = false;
          lastSpeechEndTimeRef.current = Date.now();
          setTimeout(() => {
            if (!isVoiceMutedRef.current) startListening();
          }, 1500);
        };
        utterance.onerror = () => {
          setIsSpeaking(false);
          isSpeakingRef.current = false;
          lastSpeechEndTimeRef.current = Date.now();
          if (!isVoiceMutedRef.current) startListening();
        };
        window.speechSynthesis.speak(utterance);
      } else {
        setIsSpeaking(false);
        isSpeakingRef.current = false;
        lastSpeechEndTimeRef.current = Date.now();
        startListening();
      }
    }
  };

  return {
    // state for the UI
    isVoiceMuted,
    isListening,
    isSpeaking,
    // refs shared with chat
    isVoiceMutedRef,
    lastSpokenTextRef,
    audioQueueRef,
    isPlayingQueueRef,
    // controls
    toggleVoiceMute,
    startListening,
    stopListening,
    stopSpeaking,
    enqueueAudio,
    speakText,
  };
}

export type VoiceApi = ReturnType<typeof useVoice>;
