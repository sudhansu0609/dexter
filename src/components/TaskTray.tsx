import { Cpu, X } from 'lucide-react';
import type { BgTask } from '../types';

interface Props {
  tasks: BgTask[];
  onAnswer: (taskId: string, approve: boolean) => void;
  onCancel: (taskId: string) => void;
  onDismiss: (id: string) => void;
}

// Background task tray: live progress, checkpoint approvals, outcomes.
export function TaskTray({ tasks, onAnswer, onCancel, onDismiss }: Props) {
  if (tasks.length === 0) return null;

  return (
    <div style={{ marginBottom: '12px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
      {tasks.map((task) => {
        const active = task.status === 'running' || task.status === 'queued';
        const waiting = task.status === 'waiting_confirmation';
        const failed = task.status === 'failed' || task.status === 'cancelled';
        const accent = waiting ? 'var(--warn)' : failed ? 'var(--danger)' : active ? 'var(--accent)' : 'var(--success)';
        const lastProgress =
          task.progress && task.progress.length > 0 ? task.progress[task.progress.length - 1].text : '';
        return (
          <div
            key={task.id}
            className="dexter-panel"
            style={{
              padding: '10px 12px',
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              border: `1px solid ${accent}55`,
              background: `${accent}14`,
            }}
          >
            <Cpu size={16} style={{ color: accent, flexShrink: 0 }} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: '9px', color: accent, fontWeight: 700, letterSpacing: '0.5px' }}>
                TASK · {task.status.replace('_', ' ').toUpperCase()}
              </div>
              <div style={{ fontSize: '12px', color: 'var(--text)', wordBreak: 'break-word' }}>
                {task.title}
              </div>
              {(waiting ? task.question : task.result || lastProgress) && (
                <div style={{ fontSize: '11px', color: 'var(--muted)', wordBreak: 'break-word' }}>
                  {waiting ? task.question : task.result || lastProgress}
                </div>
              )}
            </div>
            {waiting && (
              <>
                <button
                  type="button"
                  onClick={() => onAnswer(task.id, true)}
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
                  Approve
                </button>
                <button
                  type="button"
                  onClick={() => onAnswer(task.id, false)}
                  style={{
                    background: 'var(--danger-soft)',
                    color: 'var(--danger)',
                    border: '1px solid #f4c2c2',
                    padding: '5px 10px',
                    borderRadius: '8px',
                    fontSize: '11px',
                    flexShrink: 0,
                  }}
                >
                  Deny
                </button>
              </>
            )}
            {active && (
              <button
                type="button"
                onClick={() => onCancel(task.id)}
                style={{
                  background: 'var(--surface-2)',
                  color: 'var(--muted)',
                  border: '1px solid var(--border)',
                  padding: '5px 10px',
                  borderRadius: '8px',
                  fontSize: '11px',
                  flexShrink: 0,
                }}
              >
                Cancel
              </button>
            )}
            {!active && !waiting && (
              <button
                type="button"
                onClick={() => onDismiss(task.id)}
                style={{
                  background: 'transparent',
                  color: 'var(--muted)',
                  border: 'none',
                  padding: '4px',
                  flexShrink: 0,
                  cursor: 'pointer',
                }}
                title="Dismiss"
              >
                <X size={14} />
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
