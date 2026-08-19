import { useEffect, useRef } from 'react';
import { RefreshCw } from 'lucide-react';
import type { ChatMessage } from '../types';

interface Props {
  chatLog: ChatMessage[];
  isThinking: boolean;
  toolActivity: string;
  inputMsg: string;
  setInputMsg: (v: string) => void;
  onSend: () => void;
  isExpanded: boolean;
  isVoiceMuted: boolean;
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
  isExpanded,
  isVoiceMuted,
}: Props) {
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatLog, isThinking]);

  return (
    <div
      className="dexter-panel"
      style={{
        padding: '14px',
        height: isExpanded ? '460px' : '260px',
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
          gap: '10px',
          paddingRight: '4px',
        }}
      >
        {chatLog.map((m) => (
          <div key={m.id} style={{ display: 'flex', justifyContent: m.sender === 'user' ? 'flex-end' : 'flex-start' }}>
            <div
              style={{
                maxWidth: '85%',
                padding: '10px 14px',
                borderRadius: m.sender === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
                background:
                  m.sender === 'user'
                    ? 'linear-gradient(135deg, #38bdf8 0%, #0284c7 100%)'
                    : 'rgba(15, 23, 42, 0.8)',
                color: '#ffffff',
                border: m.sender === 'user' ? 'none' : '1px solid rgba(168, 85, 247, 0.3)',
                fontSize: '12px',
                lineHeight: 1.4,
              }}
            >
              <div
                style={{
                  fontSize: '10px',
                  fontWeight: 800,
                  opacity: 0.8,
                  marginBottom: '2px',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '4px',
                }}
              >
                {m.sender === 'user' ? '👤 YOU' : '🧠 DEXTER AI'}
              </div>
              <div>{m.text}</div>
            </div>
          </div>
        ))}
        {isThinking && (
          <div
            style={{
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              color: '#a855f7',
              fontSize: '11px',
              fontWeight: 600,
              background: 'rgba(168, 85, 247, 0.1)',
              padding: '8px 12px',
              borderRadius: '10px',
              width: 'fit-content',
            }}
          >
            <RefreshCw className="brain-active-pulse" size={12} />
            {/* Naming the running tool turns a generic spinner into real
                feedback about what Dexter is actually doing. */}
            <span>{toolActivity ? `Running ${toolActivity}...` : 'Dexter is thinking...'}</span>
          </div>
        )}
        <div ref={chatEndRef} />
      </div>

      {/* Prompt Input Control Bar */}
      <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '10px' }}>
        <input
          type="text"
          placeholder={isVoiceMuted ? 'Type command (Voice is muted)' : 'Speak hands-free or type here...'}
          value={inputMsg}
          onChange={(e) => setInputMsg(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && onSend()}
          style={{ flex: 1, borderRadius: '10px', fontSize: '12px', padding: '8px 12px' }}
        />
        <button
          type="button"
          onClick={onSend}
          style={{
            background: 'linear-gradient(135deg, #a855f7 0%, #38bdf8 100%)',
            color: '#ffffff',
            padding: '8px 14px',
            borderRadius: '10px',
            fontWeight: 800,
            fontSize: '12px',
            flexShrink: 0,
          }}
        >
          Send
        </button>
      </div>
    </div>
  );
}
