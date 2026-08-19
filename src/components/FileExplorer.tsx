import type { FormEvent } from 'react';
import { ExternalLink, FileText, FolderSearch, Search } from 'lucide-react';
import type { FileResult } from '../types';

interface Props {
  query: string;
  setQuery: (v: string) => void;
  results: FileResult[];
  isSearching: boolean;
  onSearch: (e: FormEvent) => void;
  onOpen: (path: string) => void;
}

// Local PC file explorer tool.
export function FileExplorer({ query, setQuery, results, isSearching, onSearch, onOpen }: Props) {
  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <h3
        style={{
          fontSize: '13px',
          color: '#38bdf8',
          marginBottom: '10px',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}
      >
        <FolderSearch size={15} /> Local PC File Explorer
      </h3>

      <form onSubmit={onSearch} style={{ display: 'flex', gap: '6px', marginBottom: '10px' }}>
        <input
          type="text"
          placeholder="Search files on PC..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{ flex: 1, fontSize: '11px', padding: '6px 10px' }}
        />
        <button
          type="submit"
          style={{
            background: 'rgba(56, 189, 248, 0.2)',
            color: '#38bdf8',
            border: '1px solid #38bdf8',
            padding: '6px 10px',
            borderRadius: '8px',
            fontSize: '11px',
            fontWeight: 700,
          }}
        >
          {isSearching ? '...' : <Search size={12} />}
        </button>
      </form>

      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '160px', overflowY: 'auto' }}>
        {results.map((f, idx) => (
          <div
            key={idx}
            onClick={() => onOpen(f.path)}
            style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              background: 'rgba(15, 23, 42, 0.6)',
              padding: '6px 8px',
              borderRadius: '8px',
              border: '1px solid var(--bg-card-border)',
              cursor: 'pointer',
              fontSize: '11px',
            }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: '6px', overflow: 'hidden' }}>
              <FileText size={12} color="#38bdf8" />
              <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.fileName}</span>
            </div>
            <ExternalLink size={10} color="#94a3b8" />
          </div>
        ))}
      </div>
    </div>
  );
}
