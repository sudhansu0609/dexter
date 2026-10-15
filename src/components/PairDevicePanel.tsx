import { useCallback, useEffect, useState } from 'react';
import { Smartphone, Trash2 } from 'lucide-react';
import { isLocalShell, listDevices, mintPairingCode, revokeDevice } from '../api';
import type { PairCodeInfo, PairedDevice } from '../types';

// The PC side of pairing: mint a code for a phone, and see/revoke the devices
// already paired. Only meaningful on the PC's own shell - a phone never sees it.
export function PairDevicePanel() {
  const [info, setInfo] = useState<PairCodeInfo | null>(null);
  const [left, setLeft] = useState(0);
  const [devices, setDevices] = useState<PairedDevice[]>([]);
  const [busy, setBusy] = useState(false);

  const refreshDevices = useCallback(async () => {
    try {
      const data = await listDevices();
      setDevices(data.devices || []);
    } catch (e) {
      console.warn('Could not list devices', e);
    }
  }, []);

  useEffect(() => { refreshDevices(); }, [refreshDevices]);

  useEffect(() => {
    if (left <= 0) return;
    const t = setInterval(() => setLeft((s) => Math.max(0, s - 1)), 1000);
    return () => clearInterval(t);
  }, [left]);

  const mint = async () => {
    setBusy(true);
    try {
      const data = await mintPairingCode();
      setInfo(data);
      setLeft(data.expiresIn || 120);
    } catch (e) {
      console.warn('Could not mint pairing code', e);
    } finally {
      setBusy(false);
    }
  };

  const revoke = async (id: string) => {
    await revokeDevice(id);
    await refreshDevices();
  };

  if (!isLocalShell()) return null;

  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <h3 style={{ fontSize: '13px', color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: '6px', marginBottom: '10px' }}>
        <Smartphone size={15} /> Pair a phone
      </h3>

      {info && left > 0 ? (
        <div style={{ textAlign: 'center', marginBottom: '12px' }}>
          <div style={{ fontSize: '30px', fontWeight: 800, letterSpacing: '6px', color: 'var(--text)' }}>{info.code}</div>
          {info.url ? (
            <div style={{ fontSize: '11px', color: 'var(--muted)', marginTop: '6px' }}>
              On the phone open <b>{info.url}</b> and enter this code.
            </div>
          ) : (
            <div style={{ fontSize: '11px', color: 'var(--warn, #d19a00)', marginTop: '6px' }}>
              Set DEXTER_TAILNET_HOST so the phone knows the address, then enter this code there.
            </div>
          )}
          <div style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '4px' }}>expires in {left}s</div>
        </div>
      ) : (
        <button
          type="button"
          onClick={mint}
          disabled={busy}
          style={{ width: '100%', background: 'var(--accent)', color: 'var(--bg)', padding: '10px', borderRadius: '10px', fontWeight: 800, fontSize: '12px', marginBottom: '12px', cursor: busy ? 'wait' : 'pointer' }}
        >
          {busy ? 'Generating…' : 'Generate pairing code'}
        </button>
      )}

      <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
        {devices.map((d) => (
          <div key={d.id} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', fontSize: '11px', padding: '6px 8px', background: 'var(--surface-2)', borderRadius: '8px', border: '1px solid var(--bg-card-border)' }}>
            <span style={{ color: 'var(--text)' }}>{d.name}</span>
            <button type="button" title="Revoke" onClick={() => revoke(d.id)} style={{ background: 'transparent', color: 'var(--muted)', padding: '2px' }}>
              <Trash2 size={14} />
            </button>
          </div>
        ))}
        {devices.length === 0 && <div style={{ fontSize: '10px', color: 'var(--muted)' }}>No phones paired yet.</div>}
      </div>
    </div>
  );
}
