import { useEffect, useState } from 'react';
import { getStatus } from '../api';
import type { RuntimeHealth } from '../types';

// How often to re-check. The facts behind it (which model is resident, how much
// VRAM is free) change on the scale of minutes, and the backend caches its own
// probe for 45s, so polling harder would only burn requests.
const POLL_MS = 60_000;

/**
 * Whether Dexter is running well, and if not, why.
 *
 * This exists because the app had no way to tell the user it was degraded: a 7B
 * model silently fell back to the CPU when another process held the GPU, every
 * reply took ~9 seconds, and the only symptom was that Dexter "felt broken".
 */
export function useRuntimeHealth() {
  const [health, setHealth] = useState<RuntimeHealth | null>(null);
  const [dismissed, setDismissed] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;

    const check = async () => {
      try {
        const status = await getStatus();
        if (!cancelled) setHealth(status.runtime ?? null);
      } catch {
        // A failed status poll is not itself a runtime warning - the offline
        // badge already covers "the backend is unreachable".
      }
    };

    check();
    const timer = setInterval(check, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, []);

  const warnings = (health?.warnings ?? []).filter((w) => !dismissed.includes(w.code));

  return {
    health,
    warnings,
    // Dismissal is per warning code and lasts for the session: the user has
    // read it, and repeating it every minute would be nagging. It returns on
    // the next launch if the cause is still there.
    dismiss: (code: string) => setDismissed((prev) => [...prev, code]),
  };
}
