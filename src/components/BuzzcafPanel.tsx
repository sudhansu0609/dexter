import { Cpu, Play, RefreshCw, Zap } from 'lucide-react';
import type { BuzzcafProject } from '../types';

interface Props {
  projects: BuzzcafProject[];
  online: boolean;
  isExecutingStep: boolean;
  isStarting: boolean;
  onExecute: (projectId: string) => void;
  onStart: () => void;
}

// BuzzcafAI Studio projects panel.
export function BuzzcafPanel({ projects, online, isExecutingStep, isStarting, onExecute, onStart }: Props) {
  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
        <h3 style={{ fontSize: '13px', color: '#10b981', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Cpu size={15} /> BuzzcafAI Projects
        </h3>
        <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: online ? '#10b981' : '#ef4444' }} />
          <span style={{ fontSize: '10px', color: online ? '#10b981' : '#ef4444' }}>{online ? 'Online' : 'Offline'}</span>
        </div>
      </div>

      {!online ? (
        <div
          style={{
            fontSize: '11px',
            color: '#94a3b8',
            padding: '10px',
            background: 'rgba(15, 23, 42, 0.6)',
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
              background: 'rgba(16, 185, 129, 0.2)',
              color: '#10b981',
              border: '1px solid rgba(16, 185, 129, 0.4)',
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
      ) : projects.length === 0 ? (
        <div style={{ fontSize: '11px', color: '#94a3b8', padding: '10px', background: 'rgba(15, 23, 42, 0.6)', borderRadius: '8px' }}>
          No projects yet. Say "start a video about [topic]" to create one!
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '180px', overflowY: 'auto' }}>
          {projects.slice(0, 5).map((project) => (
            <div
              key={project.id}
              style={{
                background: 'rgba(15, 23, 42, 0.6)',
                padding: '10px',
                borderRadius: '8px',
                border:
                  project.status === 'completed'
                    ? '1px solid rgba(16, 185, 129, 0.3)'
                    : '1px solid rgba(56, 189, 248, 0.3)',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: '4px' }}>
                <div style={{ fontSize: '11px', fontWeight: 700, color: '#f8fafc' }}>{project.name}</div>
                <span
                  style={{
                    fontSize: '9px',
                    padding: '2px 6px',
                    borderRadius: '4px',
                    background: project.status === 'completed' ? 'rgba(16, 185, 129, 0.2)' : 'rgba(56, 189, 248, 0.2)',
                    color: project.status === 'completed' ? '#10b981' : '#38bdf8',
                  }}
                >
                  {project.status}
                </span>
              </div>
              <div style={{ fontSize: '10px', color: '#94a3b8', marginBottom: '6px' }}>
                {project.brand} • Step: {project.current_step}
              </div>
              {project.status !== 'completed' && (
                <button
                  type="button"
                  onClick={() => onExecute(project.id)}
                  disabled={isExecutingStep}
                  style={{
                    background: 'rgba(16, 185, 129, 0.2)',
                    color: '#10b981',
                    border: '1px solid rgba(16, 185, 129, 0.4)',
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
