import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { KeyRound } from 'lucide-react';
import { needsPairing, redeemPairing } from '../api';

// Shown to a remote device (the phone) that has not paired yet. It redeems a
// one-time code - shown on the PC - for a long-lived device token, then lets the
// real app mount. A `?pair=CODE` in the URL (from the PC's QR) auto-fills and
// submits, so scanning is one tap.
function PairingScreen({ onPaired }: { onPaired: () => void }) {
  const [code, setCode] = useState('');
  const [name, setName] = useState('phone');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const tried = useRef(false);

  const redeem = useCallback(
    async (value: string, deviceName: string) => {
      setBusy(true);
      setError(null);
      try {
        await redeemPairing(value, deviceName);
        onPaired();
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Pairing failed.');
      } finally {
        setBusy(false);
      }
    },
    [onPaired],
  );

  useEffect(() => {
    if (tried.current) return;
    let fromUrl = '';
    try {
      fromUrl = new URLSearchParams(window.location.search).get('pair') || '';
    } catch {
      fromUrl = '';
    }
    if (fromUrl) {
      tried.current = true;
      setCode(fromUrl);
      redeem(fromUrl, 'phone');
    }
  }, [redeem]);

  return (
    <div style={{ minHeight: '100vh', display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '24px' }}>
      <div className="dexter-panel" style={{ padding: '24px', width: '100%', maxWidth: '360px' }}>
        <h2 style={{ fontSize: '16px', color: 'var(--accent)', display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '6px' }}>
          <KeyRound size={18} /> Pair this device
        </h2>
        <p style={{ fontSize: '12px', color: 'var(--muted)', marginBottom: '16px' }}>
          On the PC, open Dexter and choose “Pair a phone” to get a code, then enter it here.
        </p>
        <form
          onSubmit={(e) => { e.preventDefault(); if (code.trim()) redeem(code.trim(), name); }}
          style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}
        >
          <input
            type="text"
            inputMode="text"
            autoCapitalize="characters"
            placeholder="Pairing code"
            value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase())}
            style={{ fontSize: '18px', letterSpacing: '3px', textAlign: 'center', padding: '12px' }}
          />
          <input
            type="text"
            placeholder="Device name (optional)"
            value={name}
            onChange={(e) => setName(e.target.value)}
            style={{ fontSize: '13px', padding: '10px' }}
          />
          {error && <div style={{ fontSize: '12px', color: 'var(--danger, #e5484d)' }}>{error}</div>}
          <button
            type="submit"
            disabled={busy || !code.trim()}
            style={{ background: 'var(--accent)', color: 'var(--bg)', padding: '12px', borderRadius: '10px', fontWeight: 800, fontSize: '14px', cursor: busy ? 'wait' : 'pointer' }}
          >
            {busy ? 'Pairing…' : 'Pair'}
          </button>
        </form>
      </div>
    </div>
  );
}

// Wraps the app: on a remote device with no token, show pairing first.
export function PairingGate({ children }: { children: ReactNode }) {
  const [paired, setPaired] = useState<boolean>(() => !needsPairing());
  if (!paired) return <PairingScreen onPaired={() => setPaired(true)} />;
  return <>{children}</>;
}
