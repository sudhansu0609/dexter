import { useEffect, useState } from 'react';
import { ExternalLink, Play, Square, Zap } from 'lucide-react';
import { getApps, startAppById, stopAppById } from '../api';
import type { AppRow } from '../types';

const POLL_MS = 20_000;

interface Props {
  // Quick OS launcher (Chrome, VS Code…) — kept from the v8 AppLauncher, because
  // "open chrome" and "open buzzedit" are different questions with the same verb.
  onLaunch: (appName: string) => void;
  onMessage?: (text: string) => void;
  tick?: number;
}

const QUICK = ['Chrome', 'VSCode', 'Notepad', 'Calc', 'Spotify'];

const btn = {
  background: 'var(--surface-2)',
  color: 'var(--text)',
  border: '1px solid var(--border)',
  borderRadius: '8px',
  fontSize: '11px',
  fontWeight: 600,
  padding: '4px 8px',
  display: 'flex',
  alignItems: 'center',
  gap: '4px',
} as const;

/**
 * The owner's own projects: which are up, and one click to change that
 * (roadmap v9, D5 — this is the v8 AppLauncher grown up).
 *
 * A start goes through the same tool the chat uses, so the same Sentinel
 * check applies: no launching 9 GB of ComfyUI beside a render just because the
 * button was easier to reach than the sentence. A stop on a busy batch app
 * comes back asking, and only the second click carries `confirm`.
 */
export function AppsPanel({ onLaunch, onMessage, tick = 0 }: Props) {
  const [apps, setApps] = useState<AppRow[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [asking, setAsking] = useState<{ id: string; question: string } | null>(null);

  const refresh = async () => {
    try {
      const data = await getApps();
      setApps(data.apps || []);
    } catch {
      // Offline is already reported by the header badge.
    }
  };

  useEffect(() => {
    refresh();
    const timer = setInterval(refresh, POLL_MS);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    if (tick) refresh();
  }, [tick]);

  const start = async (row: AppRow) => {
    setBusy(row.id);
    try {
      const res = await startAppById(row.id);
      onMessage?.(res.summary);
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const stop = async (row: AppRow, confirm: boolean) => {
    setBusy(row.id);
    try {
      const res = await stopAppById(row.id, confirm);
      if (!res.ok && (res.data as any)?.needsConfirmation) {
        setAsking({ id: row.id, question: res.summary });
      } else {
        setAsking(null);
        onMessage?.(res.summary);
      }
    } finally {
      setBusy(null);
      refresh();
    }
  };

  const running = apps.filter((a) => a.running);
  const stopped = apps.filter((a) => !a.running && (a.canLaunch || a.canStop));

  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <h3
        style={{
          fontSize: '13px', color: 'var(--accent)', marginBottom: '10px',
          display: 'flex', alignItems: 'center', gap: '6px',
        }}
      >
        <Zap size={15} /> Projects
        <span style={{ color: 'var(--muted)', fontWeight: 500, fontSize: '11px' }}>
          {running.length} running
        </span>
      </h3>

      {asking && (
        <div
          style={{
            border: '1px solid #f3dcb0', background: 'var(--warn-soft)', borderRadius: '10px',
            padding: '8px 10px', marginBottom: '10px', fontSize: '11px', color: 'var(--warn)',
          }}
        >
          <div style={{ lineHeight: 1.4 }}>{asking.question}</div>
          <div style={{ display: 'flex', gap: '6px', marginTop: '6px' }}>
            <button
              type="button"
              style={{ ...btn, borderColor: 'var(--danger)', color: 'var(--danger)' }}
              onClick={() => {
                const row = apps.find((a) => a.id === asking.id);
                if (row) stop(row, true);
              }}
            >
              Stop anyway
            </button>
            <button type="button" style={btn} onClick={() => setAsking(null)}>
              Leave it
            </button>
          </div>
        </div>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
        {apps.length === 0 && (
          <div style={{ fontSize: '11px', color: 'var(--muted)' }}>Reading the registry…</div>
        )}
        {[...running, ...stopped].map((row) => (
          <div
            key={row.id}
            style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              gap: '8px', padding: '5px 0', borderBottom: '1px solid var(--border)',
            }}
          >
            <div style={{ minWidth: 0, flex: 1 }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: '6px', fontSize: '12px' }}>
                <span
                  aria-hidden
                  style={{
                    width: '6px', height: '6px', borderRadius: '50%', flexShrink: 0,
                    background: row.running ? 'var(--success)' : 'var(--border)',
                  }}
                />
                <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {row.label}
                </span>
                {row.class === 'batch' && (
                  <span style={{ fontSize: '9px', color: 'var(--muted)', border: '1px solid var(--border)', borderRadius: '4px', padding: '0 3px' }}>
                    batch
                  </span>
                )}
              </div>
              <div style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '1px' }}>
                {row.running
                  ? [row.port ? `:${row.port}` : '', row.evidence, row.detail].filter(Boolean).join(' · ')
                  : row.vramMib > 0
                    ? `needs ~${(row.vramMib / 1024).toFixed(1)} GB VRAM`
                    : 'stopped'}
              </div>
            </div>
            {row.running ? (
              row.canStop && (
                <button type="button" style={btn} disabled={busy === row.id} onClick={() => stop(row, false)}>
                  <Square size={9} /> Stop
                </button>
              )
            ) : (
              row.canLaunch && (
                <button type="button" style={btn} disabled={busy === row.id} onClick={() => start(row)}>
                  <Play size={9} color="var(--accent)" /> Start
                </button>
              )
            )}
          </div>
        ))}
      </div>

      <div style={{ marginTop: '12px' }}>
        <div style={{ fontSize: '10px', color: 'var(--muted)', fontWeight: 600, marginBottom: '5px' }}>
          Everything else
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
          {QUICK.map((app) => (
            <button key={app} type="button" style={{ ...btn, textAlign: 'left' }} onClick={() => onLaunch(app)}>
              <ExternalLink size={10} color="var(--accent)" /> {app}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
