import { Compass, Flag, Target, AlertTriangle, Trophy } from 'lucide-react';
import type { MissionStatus } from '../types';

interface Props {
  mission: MissionStatus | null;
  // Pre-fills the chat input with a phrase Dexter understands without a model,
  // so setting the mission is a conversation, not a form.
  onPrefill: (text: string) => void;
}

// The shared mission, always in view above the conversation (roadmap v7).
// Empty slots are invitations: clicking one starts the sentence for you.
export function MissionCard({ mission, onPrefill }: Props) {
  if (!mission) return null;
  const line = (icon: JSX.Element, label: string, value: string, prompt: string, ask: string) => (
    <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', fontSize: '12px', lineHeight: 1.35 }}>
      <span style={{ color: '#a855f7', flexShrink: 0, marginTop: '1px' }}>{icon}</span>
      <span style={{ opacity: 0.7, flexShrink: 0, minWidth: '78px' }}>{label}</span>
      {value ? (
        <span style={{ color: '#f8fafc' }}>{value}</span>
      ) : (
        <button
          type="button"
          onClick={() => onPrefill(prompt)}
          style={{
            background: 'transparent',
            border: '1px dashed rgba(168, 85, 247, 0.5)',
            color: '#c4b5fd',
            borderRadius: '8px',
            padding: '2px 8px',
            fontSize: '11px',
            cursor: 'pointer',
          }}
        >
          {ask}
        </button>
      )}
    </div>
  );

  return (
    <div className="dexter-panel" style={{ padding: '12px 14px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
      <div style={{ fontSize: '10px', fontWeight: 800, letterSpacing: '0.08em', opacity: 0.8, display: 'flex', alignItems: 'center', gap: '6px' }}>
        <Compass size={12} /> OUR MISSION
        {mission.snoozedUntil > Date.now() / 1000 && <span style={{ marginLeft: 'auto', opacity: 0.6, fontWeight: 600 }}>check-ins paused</span>}
      </div>
      {line(<Target size={13} />, 'North star', mission.northStar, 'our mission is ', 'What are we working towards?')}
      {line(<Flag size={13} />, 'This week', mission.focus, "this week's focus is ", 'Set the focus')}
      {line(<Trophy size={13} />, "Today's win", mission.todayWin, "today's win is ", 'What makes today a win?')}
      {mission.blockers.length > 0 && (
        <div style={{ display: 'flex', gap: '8px', alignItems: 'flex-start', fontSize: '12px' }}>
          <span style={{ color: '#f59e0b', flexShrink: 0, marginTop: '1px' }}>
            <AlertTriangle size={13} />
          </span>
          <span style={{ opacity: 0.7, minWidth: '78px', flexShrink: 0 }}>Blocked by</span>
          <span style={{ color: '#fcd34d' }}>{mission.blockers.join(' · ')}</span>
        </div>
      )}
      {mission.openLoops.length > 0 && (
        <div style={{ fontSize: '11px', opacity: 0.75, paddingLeft: '21px' }}>Open loops: {mission.openLoops.join('; ')}</div>
      )}
    </div>
  );
}
