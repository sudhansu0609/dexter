import { Layers } from 'lucide-react';
import type { SkillItem } from '../types';

interface Props {
  skills: SkillItem[];
}

// Self-learned skills list (display-only).
export function SkillsPanel({ skills }: Props) {
  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <h3
        style={{
          fontSize: '13px',
          color: 'var(--accent)',
          marginBottom: '8px',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}
      >
        <Layers size={15} /> Self-Learned Skills ({skills.length})
      </h3>
      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '140px', overflowY: 'auto' }}>
        {skills.map((s, idx) => (
          <div
            key={idx}
            style={{
              background: 'var(--surface-2)',
              padding: '6px 8px',
              borderRadius: '8px',
              border: '1px solid var(--accent-border)',
              fontSize: '10px',
            }}
          >
            <div style={{ fontWeight: 700, color: 'var(--accent)' }}>⚡ {s.name}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
