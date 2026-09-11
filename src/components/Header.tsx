import type { CSSProperties } from 'react';
import { Maximize2, Mic, MicOff, Minimize2, Settings, WifiOff, X } from 'lucide-react';
import type { VoiceState } from '../hooks/useVoice';

interface Props {
  isExpanded: boolean;
  onToggleExpand: () => void;
  online: boolean;
  onToggleSettings: () => void;
  // v10: the voice on/off switch. Absent in shells without voice.
  voiceState?: VoiceState;
  voiceReason?: string;
  voiceMode?: string;
  onToggleVoice?: () => void;
}

const VOICE_LABEL: Record<VoiceState, string> = {
  starting: 'Voice…',
  unavailable: 'No voice',
  off: 'Voice off',
  standby: 'Voice',
  idle: 'Voice on',
  listening: 'Listening',
  thinking: 'Voice on',
  speaking: 'Speaking',
  enrolling: 'Learning',
};

const iconButton: CSSProperties = {
  background: 'transparent',
  color: 'var(--muted)',
  border: '1px solid transparent',
  width: '30px',
  height: '30px',
  borderRadius: '9px',
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
};

// Title bar: name, connectivity, voice switch, settings, and the compact / full toggle.
export function Header({ isExpanded, onToggleExpand, online, onToggleSettings, voiceState, voiceReason, voiceMode, onToggleVoice }: Props) {
  return (
    <header
      className="dexter-header"
      style={
        {
          padding: '4px 4px 10px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          marginBottom: '6px',
          appRegion: 'drag',
        } as any
      }
    >
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
        <div
          style={{
            width: '28px',
            height: '28px',
            borderRadius: '9px',
            background: 'var(--accent)',
            color: '#fff',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            fontSize: '13px',
            fontWeight: 700,
          }}
        >
          D
        </div>
        <h1 style={{ fontSize: '15px', margin: 0, display: 'flex', alignItems: 'center', gap: '8px' }}>
          Dexter
          <span className={`dexter-status-dot${online ? '' : ' off'}`} title={online ? 'Online' : 'No network connection'} />
          {!online && (
            <span style={{ fontSize: '11px', color: 'var(--danger)', display: 'flex', alignItems: 'center', gap: '3px', fontWeight: 500 }}>
              <WifiOff size={11} /> offline
            </span>
          )}
        </h1>
      </div>

      <div style={{ display: 'flex', alignItems: 'center', gap: '4px', appRegion: 'no-drag' } as any}>
        {voiceState && onToggleVoice && (
          <button
            type="button"
            className={`dexter-voice-toggle ${voiceState}`}
            onClick={onToggleVoice}
            disabled={voiceState === 'unavailable' || voiceState === 'starting'}
            title={
              voiceState === 'unavailable'
                ? `Voice unavailable: ${voiceReason || 'no microphone'}`
                : voiceState === 'off'
                  ? 'Turn voice on'
                  : 'Turn voice off (mic released, replies not spoken)'
            }
          >
            {voiceState === 'off' || voiceState === 'unavailable' ? <MicOff size={13} /> : <Mic size={13} />}
            {(voiceState === 'idle' || voiceState === 'thinking') && voiceMode === 'open' ? 'Open mic' : VOICE_LABEL[voiceState]}
          </button>
        )}
        <button type="button" onClick={onToggleSettings} style={iconButton} title="Settings">
          <Settings size={15} />
        </button>
        <button type="button" onClick={onToggleExpand} style={iconButton} title={isExpanded ? 'Compact view' : 'Full dashboard'}>
          {isExpanded ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
        </button>
        {(window as any).electronAPI && (
          <button type="button" onClick={() => (window as any).electronAPI.close()} style={iconButton} title="Close">
            <X size={15} />
          </button>
        )}
      </div>
    </header>
  );
}
