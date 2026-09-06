import { Maximize2, MicOff, Minimize2, Radio, Settings, WifiOff, X } from 'lucide-react';

interface Props {
  isVoiceMuted: boolean;
  onToggleMute: () => void;
  isExpanded: boolean;
  onToggleExpand: () => void;
  online: boolean;
  onToggleSettings: () => void;
}

// Frameless desktop window title bar: brand, connectivity, voice mute, and the
// window action controls.
export function Header({
  isVoiceMuted,
  onToggleMute,
  isExpanded,
  onToggleExpand,
  online,
  onToggleSettings,
}: Props) {
  return (
    <header
      className="dexter-panel dexter-header"
      style={
        {
          padding: '10px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '14px',
          appRegion: 'drag',
        } as any
      }
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <div
          style={{
            width: '32px',
            height: '32px',
            borderRadius: '10px',
            background: 'linear-gradient(135deg, #38bdf8 0%, #a855f7 100%)',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '18px',
          }}
        >
          🧠
        </div>
        <div>
          <h1
            style={{
              fontSize: '15px',
              color: '#f8fafc',
              margin: 0,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
            }}
          >
            DEXTER{' '}
            <span
              style={{
                fontSize: '9px',
                background: 'rgba(56, 189, 248, 0.2)',
                color: '#38bdf8',
                padding: '1px 6px',
                borderRadius: '8px',
                border: '1px solid rgba(56, 189, 248, 0.4)',
              }}
            >
              DESKTOP AI
            </span>
            {!online && (
              <span
                title="No network connection"
                style={{
                  fontSize: '9px',
                  background: 'rgba(239, 68, 68, 0.2)',
                  color: '#f87171',
                  padding: '1px 6px',
                  borderRadius: '8px',
                  border: '1px solid rgba(239, 68, 68, 0.4)',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '3px',
                }}
              >
                <WifiOff size={9} /> OFFLINE
              </span>
            )}
          </h1>
        </div>
      </div>

      {/* Window Action Controls & Voice Mute */}
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', appRegion: 'no-drag' } as any}>
        {/* Dedicated Voice Mute Button */}
        <button
          type="button"
          onClick={onToggleMute}
          style={{
            background: isVoiceMuted ? 'rgba(239, 68, 68, 0.2)' : 'rgba(16, 185, 129, 0.2)',
            color: isVoiceMuted ? '#f87171' : '#10b981',
            border: isVoiceMuted ? '1px solid rgba(239, 68, 68, 0.4)' : '1px solid rgba(16, 185, 129, 0.4)',
            padding: '6px 10px',
            borderRadius: '10px',
            fontSize: '11px',
            fontWeight: 700,
            display: 'flex',
            alignItems: 'center',
            gap: '4px',
          }}
          title={isVoiceMuted ? 'Unmute Voice' : 'Disable/Mute Voice'}
        >
          {isVoiceMuted ? <MicOff size={14} /> : <Radio className="brain-active-pulse" size={14} />}
          {/* The wake phrase is openWakeWord's stock "hey jarvis" model - no
              Dexter model exists and training one was declined. Telling the user
              to say "Dexter" made every interaction start with a word that does
              not wake anything. */}
          {isVoiceMuted ? 'Muted' : 'Say "Hey Jarvis"'}
        </button>

        <button
          type="button"
          onClick={onToggleSettings}
          style={{
            background: 'rgba(148, 163, 184, 0.15)',
            color: '#94a3b8',
            border: '1px solid rgba(148, 163, 184, 0.3)',
            padding: '6px 8px',
            borderRadius: '10px',
            display: 'flex',
            alignItems: 'center',
          }}
          title="Settings"
        >
          <Settings size={14} />
        </button>

        {/* Window Expand / Compact Toggle */}
        <button
          type="button"
          onClick={onToggleExpand}
          style={{
            background: 'rgba(15, 23, 42, 0.6)',
            color: '#38bdf8',
            border: '1px solid var(--bg-card-border)',
            padding: '6px 8px',
            borderRadius: '8px',
          }}
          title={isExpanded ? 'Compact View' : 'Expand Full Dashboard'}
        >
          {isExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>

        {/* Electron Close Window */}
        {(window as any).electronAPI && (
          <button
            type="button"
            onClick={() => (window as any).electronAPI.close()}
            style={{
              background: 'rgba(239, 68, 68, 0.2)',
              color: '#f87171',
              border: '1px solid rgba(239, 68, 68, 0.4)',
              padding: '6px 8px',
              borderRadius: '8px',
            }}
          >
            <X size={14} />
          </button>
        )}
      </div>
    </header>
  );
}
