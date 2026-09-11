import { useEffect, useRef } from 'react';
import { Mic, Send } from 'lucide-react';
import type { ChatMessage } from '../types';
import type { VoiceApi } from '../hooks/useVoice';
import { MicButton, VoiceHint } from './VoiceControl';

interface Props {
  chatLog: ChatMessage[];
  isThinking: boolean;
  toolActivity: string;
  inputMsg: string;
  setInputMsg: (v: string) => void;
  onSend: () => void;
  onQuickReply: (text: string) => void;
  isExpanded: boolean;
  // The voice layer (v10): the mic button and its status line live here,
  // next to the composer, because that is where you look when you talk.
  voice?: VoiceApi;
}

// Chat feed + prompt input. Renders the live token stream (each Dexter message
// grows as tokens arrive) and a tool-activity label instead of a bare spinner.
export function ChatFeed({
  chatLog,
  isThinking,
  toolActivity,
  inputMsg,
  setInputMsg,
  onSend,
  onQuickReply,
  isExpanded,
  voice,
}: Props) {
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatLog, isThinking]);

  return (
    <div
      className="dexter-panel"
      style={{
        padding: '12px',
        height: isExpanded ? '520px' : '300px',
        display: 'flex',
        flexDirection: 'column',
      }}
    >
      <div
        style={{
          flex: 1,
          overflowY: 'auto',
          display: 'flex',
          flexDirection: 'column',
          gap: '8px',
          paddingRight: '4px',
        }}
      >
        {chatLog.map((m) => {
          const mine = m.sender === 'user';
          return (
            <div key={m.id} style={{ display: 'flex', justifyContent: mine ? 'flex-end' : 'flex-start' }}>
              <div
                style={{
                  maxWidth: '86%',
                  padding: '8px 12px',
                  borderRadius: mine ? '14px 14px 4px 14px' : '14px 14px 14px 4px',
                  background: mine ? 'var(--accent)' : 'var(--surface-2)',
                  color: mine ? '#ffffff' : 'var(--text)',
                  fontSize: '13px',
                  lineHeight: 1.5,
                }}
              >
                <div style={{ whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{m.text}</div>
                {mine && m.via === 'voice' && (
                  <div className="dexter-via-voice" title="Said out loud">
                    <Mic size={10} /> spoken
                  </div>
                )}
                {m.quickReplies && m.quickReplies.length > 0 && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '8px' }}>
                    {m.quickReplies.map((q) => (
                      <button key={q} type="button" className="dexter-chip" onClick={() => onQuickReply(q)}>
                        {q}
                      </button>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
        {isThinking && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: 'var(--muted)', fontSize: '12px', padding: '4px 2px' }}>
            <span className="dexter-dots"><span /><span /><span /></span>
            {/* Naming the running tool turns a generic spinner into real
                feedback about what Dexter is actually doing. */}
            <span>{toolActivity ? `${toolActivity}...` : ''}</span>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '10px' }}>
        <input
          type="text"
          placeholder={
            voice && voice.state !== 'off' && voice.state !== 'unavailable'
              ? `Say “${voice.wakeWord}”, or type here...`
              : 'Ask or tell Dexter...'
          }
          value={inputMsg}
          onChange={(e) => setInputMsg(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSend()}
          style={{ flex: 1, background: 'var(--surface-2)', borderColor: 'transparent' }}
        />
        {voice && <MicButton state={voice.state} level={voice.level} onPress={voice.state === 'speaking' ? () => voice.stopSpeaking(true) : voice.talkNow} />}
        <button
          type="button"
          onClick={onSend}
          title="Send"
          style={{
            background: 'var(--accent)',
            color: '#ffffff',
            width: '38px',
            height: '36px',
            borderRadius: '10px',
            display: 'inline-flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexShrink: 0,
          }}
        >
          <Send size={15} />
        </button>
      </div>
      {voice && <VoiceHint state={voice.state} hint={voice.hint} level={voice.level} wakeMode={voice.wakeMode} onWakeMode={voice.setWakeMode} />}
    </div>
  );
}
