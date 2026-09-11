import { useState } from 'react';
import { AlertCircle, Check, ChevronRight, Plus, Target } from 'lucide-react';
import type { Commitment, WorkbookSnapshot } from '../types';

interface Props {
  snapshot: WorkbookSnapshot | null;
  busy: boolean;
  onCommit: (text: string, when?: string) => void;
  onKeep: (id: number) => void;
  onRoll: (id: number) => void;
  onCloseOut: () => void;
}

const dim = { color: 'var(--muted)', fontSize: '11px' };

function dueLabel(commitment: Commitment): string {
  if (!commitment.due_date) return '';
  const today = new Date().toISOString().slice(0, 10);
  if (commitment.due_date === today) return commitment.due_time ?? 'today';
  return new Date(commitment.due_date + 'T00:00:00').toLocaleDateString(undefined, {
    weekday: 'short',
  });
}

function Row({
  commitment,
  onKeep,
  onRoll,
  tone,
}: {
  commitment: Commitment;
  onKeep: (id: number) => void;
  onRoll: (id: number) => void;
  tone?: 'overdue';
}) {
  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        padding: '6px 0',
        borderBottom: '1px solid var(--surface-2)',
      }}
    >
      <button
        onClick={() => onKeep(commitment.id)}
        title="Mark as kept"
        style={{
          width: '18px',
          height: '18px',
          borderRadius: '5px',
          border: '1px solid var(--border)',
          background: 'transparent',
          cursor: 'pointer',
          flexShrink: 0,
        }}
      />
      <div style={{ flex: 1, minWidth: 0 }}>
        <div
          style={{
            fontSize: '12px',
            color: tone === 'overdue' ? 'var(--danger)' : 'var(--text)',
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
          }}
        >
          {commitment.text}
        </div>
        {/* The roll count is the whole point of the workbook: a task avoided for
            a week must not look like one written down this morning. */}
        {commitment.roll_count > 0 && (
          <div style={dim}>moved {commitment.roll_count}×</div>
        )}
      </div>
      <span style={{ ...dim, flexShrink: 0 }}>{dueLabel(commitment)}</span>
      <button
        onClick={() => onRoll(commitment.id)}
        title="Push to tomorrow"
        style={{
          background: 'transparent',
          border: 'none',
          color: 'var(--muted)',
          cursor: 'pointer',
          padding: '2px',
          flexShrink: 0,
        }}
      >
        <ChevronRight size={14} />
      </button>
    </div>
  );
}

/**
 * The cockpit view of the workbook - what you said you'd do, what you're
 * carrying, and what has been moving for too long.
 */
export function WorkbookPanel({ snapshot, busy, onCommit, onKeep, onRoll, onCloseOut }: Props) {
  const [draft, setDraft] = useState('');

  if (!snapshot) return null;

  const { today, goals, slipping, closedOut } = snapshot;
  const submit = () => {
    const text = draft.trim();
    if (!text) return;
    onCommit(text);
    setDraft('');
  };

  return (
    <div className="dexter-panel" style={{ padding: '12px', marginBottom: '14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
        <Target size={15} style={{ color: 'var(--accent)' }} />
        <h2 style={{ fontSize: '13px', color: 'var(--text)', margin: 0, flex: 1 }}>Today</h2>
        <span style={dim}>
          {today.done.length} kept · {today.due.length + today.overdue.length} open
        </span>
      </div>

      {goals.length > 0 && (
        <div style={{ ...dim, marginBottom: '8px' }}>
          Towards: {goals.map((g) => g.title).join(' · ')}
        </div>
      )}

      {today.overdue.length > 0 && (
        <>
          <div style={{ ...dim, color: 'var(--danger)', marginTop: '4px' }}>
            Carried over ({today.overdue.length})
          </div>
          {today.overdue.map((c) => (
            <Row key={c.id} commitment={c} onKeep={onKeep} onRoll={onRoll} tone="overdue" />
          ))}
        </>
      )}

      {today.due.map((c) => (
        <Row key={c.id} commitment={c} onKeep={onKeep} onRoll={onRoll} />
      ))}

      {today.due.length === 0 && today.overdue.length === 0 && (
        <div style={{ ...dim, padding: '6px 0' }}>
          {today.done.length > 0 ? 'All clear — everything kept.' : 'Nothing committed yet today.'}
        </div>
      )}

      {today.done.length > 0 && (
        <div style={{ ...dim, marginTop: '6px', display: 'flex', alignItems: 'center', gap: '4px' }}>
          <Check size={12} style={{ color: '#4ade80' }} />
          {today.done.length} kept today
        </div>
      )}

      {slipping.length > 0 && (
        <div
          style={{
            marginTop: '10px',
            padding: '8px',
            borderRadius: '8px',
            background: 'var(--warn-soft)',
            border: '1px solid #f3dcb0',
          }}
        >
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '4px' }}>
            <AlertCircle size={13} style={{ color: 'var(--warn)' }} />
            <span style={{ fontSize: '11px', color: 'var(--warn)' }}>
              {slipping.length} slipping
            </span>
          </div>
          {slipping.slice(0, 3).map((c) => (
            <div key={c.id} style={dim}>
              {c.text}
              {c.roll_count >= 3 && ` — moved ${c.roll_count}×`}
              {c.daysLate ? ` — ${c.daysLate}d late` : ''}
            </div>
          ))}
        </div>
      )}

      <div style={{ display: 'flex', gap: '6px', marginTop: '10px' }}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && submit()}
          placeholder="Commit to something…"
          style={{
            flex: 1,
            background: 'var(--surface-2)',
            border: '1px solid var(--border)',
            borderRadius: '8px',
            padding: '6px 8px',
            color: 'var(--text)',
            fontSize: '12px',
          }}
        />
        <button
          onClick={submit}
          disabled={busy || !draft.trim()}
          style={{
            background: 'var(--accent-soft)',
            border: '1px solid var(--accent-border)',
            borderRadius: '8px',
            color: '#7dd3fc',
            cursor: busy ? 'default' : 'pointer',
            padding: '6px 8px',
          }}
        >
          <Plus size={14} />
        </button>
      </div>

      {!closedOut && (today.due.length > 0 || today.done.length > 0) && (
        <button
          onClick={onCloseOut}
          disabled={busy}
          style={{
            width: '100%',
            marginTop: '8px',
            background: 'transparent',
            border: '1px solid var(--border)',
            borderRadius: '8px',
            color: 'var(--muted)',
            fontSize: '11px',
            padding: '6px',
            cursor: busy ? 'default' : 'pointer',
          }}
        >
          Close out the day
        </button>
      )}
    </div>
  );
}
