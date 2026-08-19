import { Tv } from 'lucide-react';
import type { OverNightItem } from '../types';

interface Props {
  items: OverNightItem[];
  isCurating: boolean;
  onCurate: () => void;
}

// spilledCoffeeAI overnight content queue.
export function OvernightQueue({ items, isCurating, onCurate }: Props) {
  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
        <h3 style={{ fontSize: '13px', color: '#f472b6', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Tv size={15} /> spilledCoffeeAI Queue
        </h3>
        <button
          type="button"
          onClick={onCurate}
          disabled={isCurating}
          style={{
            background: 'rgba(244, 114, 182, 0.2)',
            color: '#f472b6',
            border: '1px solid rgba(244, 114, 182, 0.4)',
            padding: '4px 8px',
            borderRadius: '6px',
            fontSize: '10px',
            fontWeight: 700,
            cursor: isCurating ? 'wait' : 'pointer',
          }}
        >
          {isCurating ? '...' : '⚡ Curate'}
        </button>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '180px', overflowY: 'auto' }}>
        {items.map((item) => (
          <div
            key={item.id}
            style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '8px', borderRadius: '8px', border: '1px solid rgba(244, 114, 182, 0.3)' }}
          >
            <div style={{ fontSize: '11px', fontWeight: 800, color: '#f472b6' }}>{item.title}</div>
            <div style={{ fontSize: '10px', color: '#e2e8f0', fontStyle: 'italic' }}>"{item.hook}"</div>
          </div>
        ))}
      </div>
    </div>
  );
}
