import { ExternalLink, Zap } from 'lucide-react';

interface Props {
  onLaunch: (appName: string) => void;
}

const APPS = ['Chrome', 'VSCode', 'Notepad', 'Calc', 'Spotify'];

// Quick OS app launcher.
export function AppLauncher({ onLaunch }: Props) {
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
        <Zap size={15} /> Quick OS App Launcher
      </h3>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
        {APPS.map((app) => (
          <button
            key={app}
            type="button"
            onClick={() => onLaunch(app)}
            style={{
              background: 'rgba(15, 23, 42, 0.6)',
              color: '#f8fafc',
              border: '1px solid var(--bg-card-border)',
              padding: '6px 8px',
              borderRadius: '8px',
              fontSize: '11px',
              fontWeight: 600,
              textAlign: 'left',
              display: 'flex',
              alignItems: 'center',
              gap: '4px',
            }}
          >
            <ExternalLink size={10} color="#38bdf8" /> {app}
          </button>
        ))}
      </div>
    </div>
  );
}
