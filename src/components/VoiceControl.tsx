import { Mic, MicOff, Square } from 'lucide-react';
import type { VoiceState } from '../hooks/useVoice';
import type { WakeMode } from '../voice/localPipeline';

interface MicProps {
  state: VoiceState;
  level: number;
  onPress: () => void;
  size?: number;
}

// The one button for voice: press to talk, press again to stop Dexter, press
// when off to turn voice on. Its look is the state; the ring is the mic level
// so you can see it hears you before anything is transcribed.
export function MicButton({ state, level, onPress, size = 36 }: MicProps) {
  const title =
    state === 'speaking'
      ? 'Stop talking (Esc)'
      : state === 'listening'
        ? 'Listening… press to cancel'
        : state === 'off'
          ? 'Voice is off. Press to turn it on.'
          : state === 'unavailable'
            ? 'Voice unavailable'
            : state === 'thinking'
              ? 'Working on it'
              : state === 'enrolling'
                ? 'Learning your voice'
                : 'Talk to Dexter (Ctrl+Space)';
  const ring = state === 'listening' || state === 'enrolling' ? 4 + Math.round(level * 14) : 0;
  return (
    <button
      type="button"
      className={`dexter-mic ${state}`}
      onClick={onPress}
      title={title}
      aria-label={title}
      aria-pressed={state === 'listening'}
      disabled={state === 'unavailable' || state === 'starting' || state === 'enrolling'}
      style={{
        width: size,
        height: size,
        boxShadow: ring ? `0 0 0 ${ring}px var(--accent-soft)` : undefined,
      }}
    >
      {state === 'speaking' ? <Square size={14} /> : state === 'off' || state === 'unavailable' ? <MicOff size={15} /> : <Mic size={15} />}
    </button>
  );
}

interface HintProps {
  state: VoiceState;
  hint: string;
  level?: number;
  // Optional one-click switch between "start with Dexter" and open mic.
  wakeMode?: WakeMode;
  onWakeMode?: (mode: WakeMode) => void;
}

// One quiet line under the composer: what voice is doing and what to say next,
// a hair-thin input meter so a dead mic is visible at a glance, and the
// name / open-mic switch.
export function VoiceHint({ state, hint, level = 0, wakeMode, onWakeMode }: HintProps) {
  const live = state !== 'off' && state !== 'unavailable' && state !== 'starting';
  return (
    <div className={`dexter-voice-hint ${state}`} aria-live="polite">
      {state === 'listening' && <span className="dexter-voice-dot" />}
      <span className="dexter-voice-hint-text">{hint}</span>
      {live && (
        <span className="dexter-voice-meter" title="Microphone level">
          <span style={{ width: `${Math.round(Math.min(1, level) * 100)}%` }} />
        </span>
      )}
      {live && onWakeMode && wakeMode && wakeMode !== 'model' && wakeMode !== 'both' && (
        <span className="dexter-voice-modes" role="group" aria-label="How to wake Dexter">
          <button
            type="button"
            className={wakeMode === 'name' ? 'on' : ''}
            onClick={() => onWakeMode('name')}
            title="Say “Dexter” before what you want"
          >
            Name
          </button>
          <button
            type="button"
            className={wakeMode === 'open' ? 'on' : ''}
            onClick={() => onWakeMode('open')}
            title="Open mic: everything you say is for Dexter until you mute"
          >
            Open
          </button>
        </span>
      )}
    </div>
  );
}
