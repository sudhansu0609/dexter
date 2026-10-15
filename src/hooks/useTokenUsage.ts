import { useCallback, useEffect, useState } from 'react';
import { getTokenUsage, resetTokenUsage, type TokenUsageResponse } from '../api';

// Token counter for the model picker: how many tokens every model call has
// used, all-time and this session, by provider and model. Refreshed after each
// chat turn (useChat fires TURN_EVENT), on a slow poll, and when the window
// regains focus.
export const TURN_EVENT = 'dexter:turn-complete';
const POLL_MS = 20_000;

export function useTokenUsage() {
  const [data, setData] = useState<TokenUsageResponse | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setData(await getTokenUsage());
    } catch {
      /* backend not up yet, or an older backend without the endpoint */
    }
  }, []);

  const reset = useCallback(async () => {
    setBusy(true);
    try {
      setData(await resetTokenUsage());
    } catch {
      /* ignore */
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, POLL_MS);
    const onTurn = () => {
      // The ledger is written right after the reply; give it a beat.
      window.setTimeout(refresh, 400);
    };
    window.addEventListener(TURN_EVENT, onTurn);
    window.addEventListener('focus', refresh);
    return () => {
      window.clearInterval(timer);
      window.removeEventListener(TURN_EVENT, onTurn);
      window.removeEventListener('focus', refresh);
    };
  }, [refresh]);

  return { data, refresh, reset, busy };
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(n >= 10_000_000 ? 0 : 1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(n >= 10_000 ? 0 : 1)}K`;
  return String(n);
}
