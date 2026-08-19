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
          color: '#a855f7',
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
              background: 'rgba(15, 23, 42, 0.6)',
              padding: '6px 8px',
              borderRadius: '8px',
              border: '1px solid rgba(168, 85, 247, 0.25)',
              fontSize: '10px',
            }}
          >
            <div style={{ fontWeight: 700, color: '#a855f7' }}>⚡ {s.name}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
