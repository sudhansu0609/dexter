import { AlertTriangle, X } from 'lucide-react';
import type { RuntimeWarning } from '../types';

interface Props {
  warnings: RuntimeWarning[];
  onDismiss: (code: string) => void;
}

/**
 * Says out loud when Dexter is running degraded, and what to do about it.
 *
 * Deliberately not a toast: the conditions this reports (the model running on
 * the CPU because something else holds the GPU) persist until the user acts, so
 * the notice should persist too rather than disappearing after four seconds.
 */
export function RuntimeBanner({ warnings, onDismiss }: Props) {
  if (warnings.length === 0) return null;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', marginBottom: '12px' }}>
      {warnings.map((warning) => (
        <div
          key={warning.code}
          className="dexter-panel"
          style={{
            display: 'flex',
            alignItems: 'flex-start',
            gap: '10px',
            padding: '10px 12px',
            border: '1px solid rgba(251, 191, 36, 0.35)',
            background: 'rgba(251, 191, 36, 0.08)',
            borderRadius: '10px',
          }}
        >
          <AlertTriangle size={16} style={{ color: '#fbbf24', flexShrink: 0, marginTop: '2px' }} />
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: '12px', color: '#fde68a', lineHeight: 1.45 }}>
              {warning.message}
            </div>
            <div style={{ fontSize: '11px', color: '#94a3b8', marginTop: '4px', lineHeight: 1.4 }}>
              {warning.action}
            </div>
          </div>
          <button
            onClick={() => onDismiss(warning.code)}
            title="Dismiss for this session"
            style={{
              background: 'transparent',
              border: 'none',
              color: '#94a3b8',
              cursor: 'pointer',
              padding: '2px',
              flexShrink: 0,
            }}
          >
            <X size={14} />
          </button>
        </div>
      ))}
    </div>
  );
}
