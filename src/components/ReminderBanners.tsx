import { Bell } from 'lucide-react';
import type { ReminderBanner } from '../types';

interface Props {
  reminders: ReminderBanner[];
  onSnooze: (reminder: ReminderBanner) => void;
  onDismiss: (id: string) => void;
}

// Reminders pushed from the scheduler. One of three delivery channels - the
// others are the spoken reply and a native Windows toast.
export function ReminderBanners({ reminders, onSnooze, onDismiss }: Props) {
  if (reminders.length === 0) return null;

  return (
    <div style={{ marginBottom: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {reminders.map((reminder) => (
        <div
          key={reminder.id}
          className="dexter-panel brain-active-pulse"
          style={{
            padding: '12px 14px',
            display: 'flex',
            alignItems: 'center',
            gap: '10px',
            border: '1px solid rgba(251, 191, 36, 0.5)',
            background: 'rgba(251, 191, 36, 0.12)',
          }}
        >
          <Bell size={18} style={{ color: '#fbbf24', flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: '9px', color: '#fbbf24', fontWeight: 700, letterSpacing: '0.5px' }}>
              {reminder.missed ? 'MISSED REMINDER' : 'REMINDER'}
            </div>
            <div style={{ fontSize: '13px', color: '#f8fafc', wordBreak: 'break-word' }}>
              {reminder.text}
            </div>
          </div>
          <button
            type="button"
            onClick={() => onSnooze(reminder)}
            style={{
              background: 'rgba(148, 163, 184, 0.15)',
              color: '#cbd5e1',
              border: '1px solid rgba(148, 163, 184, 0.3)',
              padding: '5px 10px',
              borderRadius: '8px',
              fontSize: '11px',
              flexShrink: 0,
            }}
            title="Remind me again in 10 minutes"
          >
            Snooze
          </button>
          <button
            type="button"
            onClick={() => onDismiss(reminder.id)}
            style={{
              background: 'rgba(16, 185, 129, 0.2)',
              color: '#10b981',
              border: '1px solid rgba(16, 185, 129, 0.4)',
              padding: '5px 10px',
              borderRadius: '8px',
              fontSize: '11px',
              fontWeight: 700,
              flexShrink: 0,
            }}
          >
            Done
          </button>
        </div>
      ))}
    </div>
  );
}
