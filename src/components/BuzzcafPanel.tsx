import { CheckCircle2, Cpu, Play, RefreshCw, Zap } from 'lucide-react';
import type { BuzzcafPending, BuzzcafProject } from '../types';

interface Props {
  projects: BuzzcafProject[];
  pending?: BuzzcafPending[];
  online: boolean;
  isExecutingStep: boolean;
  isStarting: boolean;
  isApproving?: boolean;
  onExecute: (projectId: string) => void;
  onApprove?: (projectId: string) => void;
  onStart: () => void;
}

// Buzzcaf Studio panel: projects, steps waiting for approval, and launch.
export function BuzzcafPanel({
  projects, pending = [], online, isExecutingStep, isStarting, isApproving = false, onExecute, onApprove, onStart,
}: Props) {
  const pendingIds = new Set(pending.map((p) => p.project_id));
  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
        <h3 style={{ fontSize: '13px', color: 'var(--success)', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Cpu size={15} /> BuzzcafAI Projects
        </h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: online ? 'var(--success)' : 'var(--danger)' }} />
          <span style={{ fontSize: '10px', color: online ? 'var(--success)' : 'var(--danger)' }}>{online ? 'Online' : 'Offline'}</span>
        </div>
      </div>

      {!online ? (
        <div
          style={{
            fontSize: '11px',
            color: 'var(--muted)',
            padding: '10px',
            background: 'var(--surface-2)',
            borderRadius: '8px',
            display: 'flex',
            flexDirection: 'column',
            gap: '8px',
          }}
        >
          <span>BuzzcafAI Studio is offline.</span>
          <button
            type="button"
            onClick={onStart}
            disabled={isStarting}
            style={{
              background: 'var(--success-soft)',
              color: 'var(--success)',
              border: '1px solid #bfe6cc',
              padding: '6px 12px',
              borderRadius: '8px',
              fontSize: '11px',
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '6px',
              cursor: isStarting ? 'wait' : 'pointer',
            }}
          >
            {isStarting ? (
              <>
                <RefreshCw size={12} className="brain-active-pulse" /> Starting...
              </>
            ) : (
              <>
                <Zap size={12} /> Start BuzzcafAI Studio
              </>
            )}
          </button>
        </div>
      ) : null}

      {online && pending.length > 0 && (
        <div style={{ marginBottom: '8px', display: 'flex', flexDirection: 'column', gap: '6px' }}>
          {pending.map((p) => (
            <div
              key={`${p.project_id}-${p.step}`}
              style={{ background: 'var(--warn-soft)', border: '1px solid #f3dcb0', borderRadius: '8px', padding: '8px 10px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}
            >
              <div style={{ fontSize: '10px', color: 'var(--warn)' }}>
                <strong>{p.name}</strong> is waiting at {p.step || 'a step'}
              </div>
              {onApprove && (
                <button
                  type="button"
                  onClick={() => onApprove(p.project_id)}
                  disabled={isApproving}
                  style={{ background: 'var(--warn-soft)', color: 'var(--warn)', border: '1px solid #f3dcb0', padding: '4px 8px', borderRadius: '6px', fontSize: '10px', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '4px', cursor: isApproving ? 'wait' : 'pointer' }}
                >
                  <CheckCircle2 size={10} /> {isApproving ? 'Approving...' : 'Approve'}
                </button>
              )}
            </div>
          ))}
        </div>
      )}

      {!online ? null : projects.length === 0 ? (
        <div style={{ fontSize: '11px', color: 'var(--muted)', padding: '10px', background: 'var(--surface-2)', borderRadius: '8px' }}>
          No projects yet. Say "start a video about [topic]" to create one!
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '180px', overflowY: 'auto' }}>
          {projects.slice(0, 5).map((project) => (
            <div
              key={project.id}
              style={{
                background: 'var(--surface-2)',
                padding: '10px',
                borderRadius: '8px',
                border:
                  project.status === 'completed'
                    ? '1px solid #bfe6cc'
                    : '1px solid var(--accent-border)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '4px' }}>
                <div style={{ fontSize: '11px', fontWeight: 700, color: 'var(--text)' }}>{project.name}</div>
                <span
                  style={{
                    fontSize: '9px',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: project.status === 'completed' ? 'var(--success-soft)' : 'var(--accent-soft)',
                    color: project.status === 'completed' ? 'var(--success)' : 'var(--accent)',
                  }}
                >
                  {project.status}
                </span>
              </div>
              <div style={{ fontSize: '10px', color: 'var(--muted)', marginBottom: '6px' }}>
                {project.brand} • Step: {project.current_step}
              </div>
              {project.status !== 'completed' && !pendingIds.has(project.id) && (
                <button
                  type="button"
                  onClick={() => onExecute(project.id)}
                  disabled={isExecutingStep}
                  style={{
                    background: 'var(--success-soft)',
                    color: 'var(--success)',
                    border: '1px solid #bfe6cc',
                    padding: '4px 8px',
                    borderRadius: '6px',
                    fontSize: '10px',
                    fontWeight: 700,
                    display: 'flex',
                    alignItems: 'center',
                    gap: '4px',
                    cursor: isExecutingStep ? 'wait' : 'pointer',
                  }}
                >
                  {isExecutingStep ? <RefreshCw size={10} className="brain-active-pulse" /> : <Play size={10} />}
                  {isExecutingStep ? 'Running...' : 'Run Next Step'}
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
