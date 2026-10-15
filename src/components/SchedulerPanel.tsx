import { useMemo, useState, type FormEvent } from 'react';
import { AlarmClock, Bot, Calendar, PlayCircle, Terminal, Trash2 } from 'lucide-react';
import type { ScheduleAction, ScheduleActionType, ScheduleItem } from '../types';

interface Props {
  items: ScheduleItem[];
  loading: boolean;
  onAdd: (action: ScheduleAction, when: string) => Promise<unknown>;
  onRemove: (id: string) => void;
  onRunNow: (id: string) => void;
}

const TYPES: { value: ScheduleActionType; label: string }[] = [
  { value: 'reminder', label: 'Reminder' },
  { value: 'goal', label: 'Task (goal)' },
  { value: 'app', label: 'Open app' },
  { value: 'command', label: 'Run command' },
  { value: 'workflow', label: 'Buzzcaf workflow' },
  { value: 'code_task', label: 'Code task' },
];

// Actions that reach out and change the machine - flagged so a scheduled one is
// never a surprise.
const POWERFUL = new Set<ScheduleActionType>(['command', 'code_task']);

function whenLabel(item: ScheduleItem): string {
  if (item.cron) return `repeats · ${item.whenText || 'schedule'}`;
  if (item.whenText) return item.whenText;
  if (item.at) {
    try {
      return new Date(item.at).toLocaleString();
    } catch {
      return item.at;
    }
  }
  return 'scheduled';
}

function typeIcon(t: ScheduleActionType) {
  if (t === 'reminder') return <AlarmClock size={13} />;
  if (t === 'command' || t === 'code_task') return <Terminal size={13} />;
  if (t === 'app' || t === 'workflow') return <PlayCircle size={13} />;
  return <Bot size={13} />;
}

// The unified scheduler: reminders and scheduled actions in one list, one form
// to add any kind. Built to be usable one-handed on a phone.
export function SchedulerPanel({ items, loading, onAdd, onRemove, onRunNow }: Props) {
  const [type, setType] = useState<ScheduleActionType>('reminder');
  const [primary, setPrimary] = useState('');
  const [project, setProject] = useState('');
  const [shell, setShell] = useState<'powershell' | 'cmd'>('powershell');
  const [when, setWhen] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const primaryPlaceholder = useMemo(() => {
    switch (type) {
      case 'reminder': return 'Remind me to…';
      case 'goal': return 'e.g. check my calendar and email, then brief me';
      case 'app': return 'App name, e.g. Notepad';
      case 'command': return 'Shell command, e.g. Restart-Service Foo';
      case 'workflow': return 'Buzzcaf project id';
      case 'code_task': return 'What to code';
      default: return '';
    }
  }, [type]);

  const buildAction = (): ScheduleAction => {
    switch (type) {
      case 'reminder': return { type, text: primary.trim() };
      case 'goal': return { type, goal: primary.trim() };
      case 'app': return { type, app: primary.trim() };
      case 'command': return { type, command: primary.trim(), shell };
      case 'workflow': return { type, projectId: primary.trim() };
      case 'code_task': return { type, task: primary.trim(), project: project.trim() || undefined };
      default: return { type };
    }
  };

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!primary.trim() || !when.trim()) {
      setError('Fill in what and when.');
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await onAdd(buildAction(), when.trim());
      setPrimary('');
      setProject('');
      setWhen('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not schedule that.');
    } finally {
      setBusy(false);
    }
  };

  const inputStyle = { width: '100%', fontSize: '13px', padding: '10px 12px', boxSizing: 'border-box' as const };

  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
        <h3 style={{ fontSize: '13px', color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Calendar size={15} /> Scheduler
        </h3>
        <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>{items.length} scheduled</span>
      </div>

      <form onSubmit={submit} style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '14px' }}>
        <select
          value={type}
          onChange={(e) => { setType(e.target.value as ScheduleActionType); setError(null); }}
          style={inputStyle}
        >
          {TYPES.map((t) => (
            <option key={t.value} value={t.value}>{t.label}</option>
          ))}
        </select>

        <input type="text" placeholder={primaryPlaceholder} value={primary} onChange={(e) => setPrimary(e.target.value)} style={inputStyle} />

        {type === 'code_task' && (
          <input type="text" placeholder="Project/repo (optional)" value={project} onChange={(e) => setProject(e.target.value)} style={inputStyle} />
        )}
        {type === 'command' && (
          <select value={shell} onChange={(e) => setShell(e.target.value as 'powershell' | 'cmd')} style={inputStyle}>
            <option value="powershell">PowerShell</option>
            <option value="cmd">cmd</option>
          </select>
        )}

        <input type="text" placeholder="When — e.g. in 10 minutes, tomorrow 9am, every weekday at 17:00" value={when} onChange={(e) => setWhen(e.target.value)} style={inputStyle} />

        {POWERFUL.has(type) && (
          <div style={{ fontSize: '10px', color: 'var(--warn, #d19a00)' }}>
            This runs on your PC unattended when it fires.
          </div>
        )}
        {error && <div style={{ fontSize: '11px', color: 'var(--danger, #e5484d)' }}>{error}</div>}

        <button
          type="submit"
          disabled={busy}
          style={{
            background: 'var(--accent)', color: 'var(--bg)', padding: '10px',
            borderRadius: '10px', fontWeight: 800, fontSize: '13px', cursor: busy ? 'wait' : 'pointer',
          }}
        >
          {busy ? 'Scheduling…' : 'Schedule it'}
        </button>
      </form>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '360px', overflowY: 'auto' }}>
        {items.length === 0 && !loading && (
          <div style={{ fontSize: '11px', color: 'var(--muted)', textAlign: 'center', padding: '12px' }}>
            Nothing scheduled yet.
          </div>
        )}
        {items.map((item) => (
          <div
            key={item.id}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px',
              padding: '10px 12px', background: 'var(--surface-2)', borderRadius: '10px',
              border: '1px solid var(--bg-card-border)', fontSize: '12px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'flex-start', gap: '8px', minWidth: 0 }}>
              <span style={{ color: 'var(--accent)', marginTop: '1px' }}>{typeIcon(item.actionType)}</span>
              <div style={{ minWidth: 0 }}>
                <div style={{ color: 'var(--text)', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.label}</div>
                <div style={{ fontSize: '10px', color: 'var(--muted)' }}>{whenLabel(item)}</div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: '6px', flexShrink: 0 }}>
              {item.kind === 'action' && (
                <button
                  type="button"
                  title="Run now"
                  onClick={() => onRunNow(item.id)}
                  style={{ background: 'transparent', color: 'var(--accent)', padding: '4px' }}
                >
                  <PlayCircle size={16} />
                </button>
              )}
              <button
                type="button"
                title="Cancel"
                onClick={() => onRemove(item.id)}
                style={{ background: 'transparent', color: 'var(--muted)', padding: '4px' }}
              >
                <Trash2 size={16} />
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
