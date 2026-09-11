import { useEffect, useState } from 'react';
import { Cpu, HardDrive, Gauge, Snowflake, RefreshCw } from 'lucide-react';
import { getResources, runPhrase } from '../api';
import type { ResourceSnapshot } from '../types';

const POLL_MS = 15_000;

interface Props {
  // The panel refetches when the steward publishes; App passes the SSE tick.
  tick?: number;
}

const gb = (mib?: number | null) => (mib == null ? null : mib / 1024);

function zoneColour(zone?: string): string {
  const z = (zone || '').toLowerCase();
  if (z === 'red') return 'var(--danger)';
  if (z === 'yellow' || z === 'orange') return 'var(--warn)';
  return 'var(--success)';
}

/**
 * One gauge. Deliberately a bar and two numbers, not a dial: the question is
 * always "is there room for the thing I am about to start", and a bar answers
 * that at a glance.
 */
function Gauge3({
  label, usedMib, totalMib, zone, note,
}: { label: string; usedMib: number | null; totalMib: number | null; zone?: string; note?: string }) {
  const pct = usedMib != null && totalMib ? Math.min(100, Math.round((usedMib / totalMib) * 100)) : null;
  return (
    <div style={{ marginBottom: '10px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', marginBottom: '4px' }}>
        <span style={{ color: 'var(--muted)', fontWeight: 600 }}>{label}</span>
        <span style={{ color: 'var(--text)' }}>
          {pct == null
            ? 'not reported'
            : `${gb(usedMib)!.toFixed(1)} / ${gb(totalMib)!.toFixed(1)} GB`}
        </span>
      </div>
      <div style={{ height: '6px', background: 'var(--surface-2)', borderRadius: '4px', overflow: 'hidden' }}>
        <div
          data-testid={`gauge-${label.toLowerCase()}`}
          style={{ width: `${pct ?? 0}%`, height: '100%', background: zoneColour(zone), transition: 'width .3s ease' }}
        />
      </div>
      {note && <div style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '3px' }}>{note}</div>}
    </div>
  );
}

function Action({ label, onClick, busy }: { label: string; onClick: () => void; busy: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={busy}
      style={{
        background: 'var(--surface-2)',
        color: 'var(--text)',
        border: '1px solid var(--border)',
        padding: '5px 9px',
        borderRadius: '8px',
        fontSize: '11px',
        fontWeight: 600,
        opacity: busy ? 0.5 : 1,
      }}
    >
      {label}
    </button>
  );
}

/**
 * What the machine's memory is doing, and who has it (roadmap v9, D5).
 *
 * The three numbers that decide whether anything on this PC works — VRAM, RAM
 * and free disk — used to be invisible: a render would take the card, Dexter
 * would fall back to the CPU, and the only symptom was that everything felt
 * slow. Sentinel knows all three; this is the window onto it.
 *
 * The buttons are the moves Sentinel actually allows. There is deliberately no
 * "kill it" button: stopping something is the apps panel's job, with its own
 * confirmation, and the guardian's ladder is not something to click past.
 */
export function ResourcePanel({ tick = 0 }: Props) {
  const [snap, setSnap] = useState<ResourceSnapshot | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = async () => {
    try {
      setSnap(await getResources());
    } catch {
      // The offline badge already covers "the backend is unreachable".
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

  const say = async (phrase: string) => {
    setBusy(true);
    try {
      await runPhrase(phrase);
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  if (!snap) {
    return (
      <div className="dexter-panel" style={{ padding: '16px' }}>
        <h3 style={{ fontSize: '13px', color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Gauge size={15} /> Machine
        </h3>
        <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '8px' }}>Reading the machine…</div>
      </div>
    );
  }

  const { vram, ram, disk, sentinel, holders, frozen } = snap;
  const drive = disk?.drives?.[0];

  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <h3
        style={{
          fontSize: '13px', color: 'var(--accent)', marginBottom: '10px',
          display: 'flex', alignItems: 'center', gap: '6px', justifyContent: 'space-between',
        }}
      >
        <span style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
          <Gauge size={15} /> Machine
        </span>
        <button
          type="button"
          onClick={refresh}
          title="Refresh"
          style={{ background: 'transparent', border: 'none', color: 'var(--muted)', padding: 0 }}
        >
          <RefreshCw size={12} />
        </button>
      </h3>

      <Gauge3
        label="VRAM"
        usedMib={vram?.usedMib ?? null}
        totalMib={vram?.totalMib ?? null}
        zone={vram?.zone}
        note={vram ? vram.name : 'Sentinel is not reporting the GPU.'}
      />
      <Gauge3
        label="RAM"
        usedMib={ram?.totalMib != null && ram?.availMib != null ? ram.totalMib - ram.availMib : null}
        totalMib={ram?.totalMib ?? null}
        zone={ram?.zone}
        note={ram?.source === 'local' ? 'read locally — this Sentinel has no RAM zones' : undefined}
      />
      <Gauge3
        label="Disk"
        usedMib={drive ? Math.round((drive.total - drive.free) / (1024 * 1024)) : null}
        totalMib={drive ? Math.round(drive.total / (1024 * 1024)) : null}
        zone={drive?.zone}
        note={drive ? `${drive.letter} · ${(drive.free / 1024 ** 3).toFixed(0)} GB free` : undefined}
      />

      {holders.length > 0 && (
        <div style={{ marginTop: '10px' }}>
          <div style={{ fontSize: '11px', color: 'var(--muted)', fontWeight: 600, marginBottom: '4px' }}>
            Holding the card
          </div>
          {holders.slice(0, 4).map((h) => (
            <div
              key={`${h.name}-${h.pid}`}
              style={{ display: 'flex', justifyContent: 'space-between', fontSize: '11px', padding: '2px 0' }}
            >
              <span style={{ display: 'flex', alignItems: 'center', gap: '4px' }}>
                <Cpu size={10} color="var(--muted)" /> {h.name}
                {h.frozen && <Snowflake size={10} color="var(--accent)" />}
              </span>
              <span style={{ color: 'var(--muted)' }}>{(h.vramMib / 1024).toFixed(1)} GB</span>
            </div>
          ))}
        </div>
      )}

      {frozen.length > 0 && (
        <div style={{ fontSize: '11px', color: 'var(--warn)', marginTop: '8px' }}>
          Paused by Sentinel: {frozen.map((f) => f.name).join(', ')}
        </div>
      )}

      {snap.lastShed?.steps && snap.lastShed.steps.length > 0 && (
        <div style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '8px', lineHeight: 1.4 }}>
          Last gave back {((snap.lastShed.freedMib ?? 0) / 1024).toFixed(1)} GB:{' '}
          {snap.lastShed.steps.join('; ')}
        </div>
      )}

      <div style={{ display: 'flex', flexWrap: 'wrap', gap: '6px', marginTop: '12px' }}>
        <Action label="Pause batch" onClick={() => say('pause batch jobs')} busy={busy} />
        <Action label="Resume batch" onClick={() => say('resume batch jobs')} busy={busy} />
        <Action label="Safe clean" onClick={() => say('clean up space')} busy={busy} />
      </div>

      <div style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '10px', lineHeight: 1.4 }}>
        {sentinel.connected ? (
          <>
            Sentinel {sentinel.version ? `v${sentinel.version}` : 'v1 (no Hello)'}
            {sentinel.features.length === 0 && ' — older build: no RAM admission or disk watch'}
            {snap.trayAlive === false && ' · its tray is not running'}
          </>
        ) : (
          <span style={{ color: 'var(--warn)' }}>
            Sentinel is not answering — nothing is arbitrating memory right now.
          </span>
        )}
      </div>
      {snap.models.loaded.length > 0 && (
        <div style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '4px' }}>
          <HardDrive size={9} style={{ verticalAlign: 'middle' }} /> Loaded:{' '}
          {snap.models.loaded.map((m) => `${m.name} ${(m.vramMb / 1024).toFixed(1)} GB`).join(', ')}
        </div>
      )}
    </div>
  );
}
