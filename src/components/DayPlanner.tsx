import type { FormEvent } from 'react';
import { Calendar, CheckCircle, Plus } from 'lucide-react';
import type { Task } from '../types';

interface Props {
  tasks: Task[];
  newTaskTitle: string;
  setNewTaskTitle: (v: string) => void;
  onAdd: (e: FormEvent) => void;
  onToggle: (taskId: string) => void;
  isAdding: boolean;
}

// Day-planner agenda widget.
export function DayPlanner({ tasks, newTaskTitle, setNewTaskTitle, onAdd, onToggle, isAdding }: Props) {
  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
        <h3 style={{ fontSize: '13px', color: '#38bdf8', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Calendar size={15} /> Today
        </h3>
        <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>{tasks.length} Tasks</span>
      </div>

      <form onSubmit={onAdd} style={{ display: 'flex', gap: '6px', marginBottom: '12px' }}>
        <input
          type="text"
          placeholder="Commit to something today..."
          value={newTaskTitle}
          onChange={(e) => setNewTaskTitle(e.target.value)}
          style={{ flex: 1, fontSize: '11px', padding: '6px 10px' }}
        />
        <button
          type="submit"
          disabled={isAdding}
          style={{
            background: '#38bdf8',
            color: '#090d16',
            padding: '6px 10px',
            borderRadius: '8px',
            fontWeight: 800,
            fontSize: '11px',
            cursor: isAdding ? 'wait' : 'pointer',
          }}
        >
          <Plus size={12} />
        </button>
      </form>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '180px', overflowY: 'auto' }}>
        {tasks.map((t) => (
          <div
            key={t.id}
            onClick={() => onToggle(t.id)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              padding: '6px 10px',
              background: 'rgba(15, 23, 42, 0.6)',
              borderRadius: '8px',
              border: '1px solid var(--bg-card-border)',
              fontSize: '11px',
              cursor: 'pointer',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
              <CheckCircle size={12} color={t.completed ? '#10b981' : '#64748b'} />
              <span
                style={{
                  textDecoration: t.completed ? 'line-through' : 'none',
                  color: t.completed ? '#64748b' : '#f8fafc',
                }}
              >
                {t.title}
              </span>
            </div>
            <span style={{ fontSize: '9px', color: '#94a3b8' }}>{t.time}</span>
          </div>
        ))}
      </div>
    </div>
  );
}
