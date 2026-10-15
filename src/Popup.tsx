import { useEffect, useRef, useState } from 'react';
import { Hand, Maximize2, Send, Settings as SettingsIcon, Square, Volume2, VolumeX, X } from 'lucide-react';
import { useChat } from './hooks/useChat';
import { useVoice } from './hooks/useVoice';
import { useServerEvents } from './hooks/useServerEvents';
import { useOnlineStatus } from './hooks/useOnlineStatus';
import { useHandMouse } from './hooks/useHandMouse';
import { liveLine } from './components/HandMousePanel';
import { MicButton, VoiceHint } from './components/VoiceControl';
import PopupSettings from './components/PopupSettings';

// The desktop shell (backend/desktop_app.py) exposes these through pywebview;
// in a plain browser tab they are simply absent and the buttons do nothing.
type ShellApi = {
  show_popup?: () => Promise<unknown>;
  hide_popup?: () => Promise<unknown>;
  // Returns false when the shell could not bring the window up, so the button
  // can say so instead of looking broken (v13.2).
  open_full?: () => Promise<unknown>;
  maximize_full?: () => Promise<unknown>;
};
const shell = (): ShellApi => ((window as any).pywebview?.api as ShellApi) || {};

// How long a message Dexter started (a check-in, a reminder, a finished job)
// stays up before the pop-up tucks itself away again. Anything the user typed
// stays until they close it.
const AUTO_HIDE_MS = 30_000;

// One message from Dexter and one line to answer it. The conversation, the
// mission, the planner and everything else live in the full window, which the
// tray icon opens (v8: "a single pop-up, not the whole thing").
export default function Popup() {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const hideTimer = useRef<number | null>(null);
  const [fromDexter, setFromDexter] = useState(false);
  // v11.1: the mic and the model can be changed here, not only in the full window.
  const [showSettings, setShowSettings] = useState(false);
  const online = useOnlineStatus();
  const hand = useHandMouse();

  // Voice (v10): the pop-up is the window that is usually up, so it listens
  // too. The backend gives the mic to whichever window was shown last.
  const isThinkingRef = useRef(false);
  const commandHandlerRef = useRef<(text: string) => void>(() => {});
  const voice = useVoice({ windowName: 'popup', isThinkingRef, commandHandlerRef, hotkeys: true });

  const chat = useChat({ voice, isThinkingRef, commandHandlerRef, onTurnComplete: () => {} });
  const pushRef = useRef(chat.pushDexterMessage);
  pushRef.current = chat.pushDexterMessage;

  const events = useServerEvents({
    refreshPlanner: () => {},
    refreshBuzzcaf: () => {},
    onCompanion: (p) => {
      pushRef.current(p.text, 'companion', { kind: p.kind, quickReplies: p.quickReplies });
      setFromDexter(true);
      shell().show_popup?.();
    },
    onSpeak: (text, emotion, isReminder) => voice.speakText(text, emotion, isReminder),
  });

  // A spoken command should be seen, not just heard: bring the pop-up up when
  // voice is doing something, and keep it up while Dexter talks.
  useEffect(() => {
    if (voice.state === 'listening' || voice.state === 'speaking') {
      setFromDexter(true);
      shell().show_popup?.();
    }
  }, [voice.state]);

  // A reminder firing is the other reason Dexter speaks first.
  const reminder = events.reminders[events.reminders.length - 1];
  useEffect(() => {
    if (reminder) {
      setFromDexter(true);
      shell().show_popup?.();
    }
  }, [reminder?.id]);

  // ...and so is the machine: the GPU taken, a drive gone red, Sentinel having
  // stopped something. These arrive while the owner is doing something else, so
  // the pop-up is exactly the right size for them (v9, D5).
  const notice = events.notices[events.notices.length - 1];
  useEffect(() => {
    if (notice) {
      setFromDexter(true);
      shell().show_popup?.();
    }
  }, [notice?.id]);

  // Esc stops Dexter mid-sentence if it is talking, else hides; focus lands in
  // the input whenever the window comes forward.
  const voiceRef = useRef(voice);
  voiceRef.current = voice;
  const chatRef = useRef(chat);
  chatRef.current = chat;
  const showSettingsRef = useRef(showSettings);
  showSettingsRef.current = showSettings;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        if (chatRef.current.isBusy || voiceRef.current.state === 'speaking') {
          e.preventDefault();
          chatRef.current.cancelTurn();
          return;
        }
        // Esc closes the settings first; a second Esc hides the pop-up.
        if (showSettingsRef.current) setShowSettings(false);
        else shell().hide_popup?.();
      }
    };
    const onFocus = () => inputRef.current?.focus();
    window.addEventListener('keydown', onKey);
    window.addEventListener('focus', onFocus);
    inputRef.current?.focus();
    return () => {
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('focus', onFocus);
    };
  }, []);

  // Auto-hide only what Dexter started, and only while nobody is touching it.
  const armHide = () => {
    if (hideTimer.current) window.clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(() => {
      if (!chat.inputMsg && document.activeElement !== inputRef.current) {
        shell().hide_popup?.();
        setFromDexter(false);
      }
    }, AUTO_HIDE_MS);
  };
  useEffect(() => {
    if (fromDexter && !chat.isThinking && voice.state !== 'speaking' && voice.state !== 'listening') armHide();
    return () => {
      if (hideTimer.current) window.clearTimeout(hideTimer.current);
    };
  }, [fromDexter, chat.chatLog.length, chat.isThinking, voice.state]);

  const send = (text?: string) => {
    setFromDexter(false);
    if (hideTimer.current) window.clearTimeout(hideTimer.current);
    chat.handleSendMessage(text);
  };

  // What to show: the last thing Dexter said, and the line it answered.
  const log = chat.chatLog;
  const lastDexterIdx = [...log].map((m) => m.sender).lastIndexOf('dexter');
  const last = lastDexterIdx >= 0 ? log[lastDexterIdx] : undefined;
  const youIdx = [...log].map((m) => m.sender).lastIndexOf('user');
  const you = youIdx >= 0 ? log[youIdx] : undefined;
  const youIsNewer = you && (lastDexterIdx < 0 || youIdx > lastDexterIdx);

  // What Dexter heard, shown the instant it is recognised - before the chat
  // log gains the matching entry. Once the log's own "you" line says the same
  // thing, that line takes over and this one steps aside.
  const heardPending = Boolean(voice.heard) && voice.heard !== you?.text;

  const state = !online
    ? 'offline'
    : chat.isThinking
      ? chat.toolActivity || 'thinking'
      : chat.toolActivity
        ? chat.toolActivity
        : voice.state === 'listening'
          ? 'listening'
          : voice.state === 'speaking'
            ? 'speaking'
            : '';

  return (
    <div className="dexter-popup" onMouseMove={fromDexter ? armHide : undefined}>
      <div className="dexter-popup-bar">
        <span className={`dexter-status-dot${!online ? ' off' : chat.isThinking ? ' busy' : ''}`} />
        <span className="title">Dexter</span>
        <span className="state">{state}</span>
        {/* v13.6: the hand mouse, when it is on. The pop-up is the window that is
            always to hand, and something quietly driving the pointer has to be
            visible - and stoppable - from it. */}
        {hand.status?.running && (
          <button
            type="button"
            title={`${liveLine(hand.status)} - click to stop`}
            aria-label="Stop the hand mouse"
            style={{ color: hand.status.seeingHand ? 'var(--success)' : 'var(--warn)' }}
            onClick={() => hand.stop()}
          >
            <Hand size={14} />
          </button>
        )}
        {/* v13: mute from the pop-up. This silences Dexter's *voice* and leaves
            the mic alone - the common case is "stop talking" while a call or a
            recording is going, not "stop listening" (that is the mic button). */}
        <button
          type="button"
          title={voice.silent ? 'Unmute Dexter (it will speak again)' : 'Mute Dexter (it keeps listening)'}
          aria-label={voice.silent ? 'Unmute Dexter' : 'Mute Dexter'}
          aria-pressed={voice.silent}
          onClick={() => voice.toggleSilent()}
        >
          {voice.silent ? <VolumeX size={14} /> : <Volume2 size={14} />}
        </button>
        <button
          type="button"
          title="Microphone and model"
          aria-label="Quick settings"
          aria-pressed={showSettings}
          onClick={() => setShowSettings((v) => !v)}
        >
          <SettingsIcon size={14} />
        </button>
        <button
          type="button"
          title="Open the main Dexter window"
          aria-label="Open the main Dexter window"
          onClick={async () => {
            // v13.2: the shell now raises the window and tucks the pop-up away.
            // If it cannot (no shell, or Windows refused the foreground), say so -
            // silence here reads as a dead button, which is how this was reported.
            const api = shell();
            if (!api.open_full) {
              pushRef.current(
                'I can only open the main window from the desktop shell. Use the tray icon.',
                'companion',
              );
              return;
            }
            try {
              const ok = await api.open_full();
              if (ok === false) {
                pushRef.current(
                  "I couldn't bring the main window forward. Try the tray icon (near the clock).",
                  'companion',
                );
              }
            } catch {
              pushRef.current('The main window did not answer. Try the tray icon.', 'companion');
            }
          }}
        >
          <Maximize2 size={14} />
        </button>
        <button type="button" title="Hide (Esc)" onClick={() => shell().hide_popup?.()}>
          <X size={15} />
        </button>
      </div>

      {showSettings && (
        <PopupSettings
          voice={voice}
          onClose={() => setShowSettings(false)}
          onOpenFull={() => {
            setShowSettings(false);
            shell().open_full?.();
          }}
        />
      )}

      <div className="dexter-popup-body">
        {reminder && (
          <div style={{ background: 'var(--warn-soft)', border: '1px solid #f3dcb0', borderRadius: 10, padding: '8px 10px' }}>
            <div style={{ fontSize: 11, color: 'var(--warn)', fontWeight: 600, marginBottom: 2 }}>
              {reminder.missed ? 'Missed reminder' : 'Reminder'}
            </div>
            <div className="dexter-popup-msg">{reminder.text}</div>
            <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
              <button type="button" className="dexter-chip" onClick={() => events.dismissReminder(reminder.id)}>Done</button>
              <button type="button" className="dexter-chip" onClick={() => events.snoozeReminder(reminder)}>Snooze 10 min</button>
            </div>
          </div>
        )}

        {notice && (
          <div style={{ background: 'var(--warn-soft)', border: '1px solid #f3dcb0', borderRadius: 10, padding: '8px 10px' }}>
            <div style={{ fontSize: 11, color: 'var(--warn)', fontWeight: 600, marginBottom: 2 }}>
              {notice.kind === 'disk_tight' ? 'Disk' : notice.kind === 'evicted' ? 'Sentinel' : 'GPU'}
            </div>
            <div className="dexter-popup-msg">{notice.text}</div>
            <div style={{ display: 'flex', gap: 6, marginTop: 8 }}>
              {notice.kind === 'disk_tight' && (
                <button type="button" className="dexter-chip" onClick={() => send('clean up space')}>Clean up space</button>
              )}
              {notice.kind === 'gpu_busy' && (
                <button type="button" className="dexter-chip" onClick={() => send('pause batch jobs')}>Pause batch jobs</button>
              )}
              <button type="button" className="dexter-chip" onClick={() => events.dismissNotice(notice.id)}>Dismiss</button>
            </div>
          </div>
        )}

        {heardPending ? (
          <div className="dexter-popup-you">You said: {voice.heard}</div>
        ) : (
          you &&
          (youIsNewer || last) && (
            <div className="dexter-popup-you">
              {you.via === 'voice' ? 'You said: ' : 'You: '}
              {you.text}
            </div>
          )
        )}

        {youIsNewer && chat.isThinking ? (
          <div className="dexter-dots"><span /><span /><span /></div>
        ) : (
          last && <div className="dexter-popup-msg">{last.text}</div>
        )}

        {last && last.quickReplies && last.quickReplies.length > 0 && !youIsNewer && (
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 2 }}>
            {last.quickReplies.map((q) => (
              <button key={q} type="button" className="dexter-chip" onClick={() => send(q)}>
                {q}
              </button>
            ))}
          </div>
        )}
      </div>

      <div className="dexter-popup-input">
        <input
          ref={inputRef}
          type="text"
          placeholder={voice.state !== 'off' && voice.state !== 'unavailable' ? `Say “${voice.wakeWord}” or type...` : 'Ask or tell Dexter...'}
          value={chat.inputMsg}
          onChange={(e) => chat.setInputMsg(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              send();
            } else if (e.key === 'Escape' && (chat.isBusy || voice.state === 'speaking')) {
              e.preventDefault();
              chat.cancelTurn();
            }
          }}
        />
        <MicButton
          state={voice.state}
          level={voice.level}
          size={32}
          onPress={voice.state === 'speaking' ? () => voice.stopSpeaking(true) : voice.talkNow}
        />
        {chat.isBusy || voice.state === 'speaking' ? (
          <button
            type="button"
            onClick={() => chat.cancelTurn()}
            title="Stop (Esc)"
            style={{
              background: '#ef4444',
              color: '#ffffff',
              cursor: 'pointer',
            }}
          >
            <Square size={14} fill="currentColor" />
          </button>
        ) : (
          <button type="button" onClick={() => send()} title="Send">
            <Send size={15} />
          </button>
        )}
      </div>
      <div style={{ padding: '0 10px 6px' }}>
        <VoiceHint state={voice.state} hint={voice.hint} level={voice.level} wakeMode={voice.wakeMode} onWakeMode={voice.setWakeMode} />
      </div>
    </div>
  );
}
