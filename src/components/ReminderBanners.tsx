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
            border: '1px solid #f3dcb0',
            background: 'var(--warn-soft)',
          }}
        >
          <Bell size={18} style={{ color: 'var(--warn)', flexShrink: 0 }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: '9px', color: 'var(--warn)', fontWeight: 700, letterSpacing: '0.5px' }}>
              {reminder.missed ? 'MISSED REMINDER' : 'REMINDER'}
            </div>
            <div style={{ fontSize: '13px', color: 'var(--text)', wordBreak: 'break-word' }}>
              {reminder.text}
            </div>
          </div>
          <button
            type="button"
            onClick={() => onSnooze(reminder)}
            style={{
              background: 'var(--surface-2)',
              color: 'var(--muted)',
              border: '1px solid var(--border)',
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
              background: 'var(--success-soft)',
              color: 'var(--success)',
              border: '1px solid #bfe6cc',
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
