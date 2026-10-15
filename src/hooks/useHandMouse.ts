import { useCallback, useEffect, useRef, useState } from 'react';
import { bindHandGesture, getHandMouse, startHandMouse, stopHandMouse, updateSettings } from '../api';
import type { HandMouseStatus } from '../types';

// Two rates, because the two states are different questions. While tracking, the
// panel is a live instrument — the pose changes several times a second and the
// palm score is what tells you whether to move your hand or the lamp. While it is
// off, the only thing that can change is whether the models are present.
const LIVE_MS = 1_000;
const IDLE_MS = 20_000;

/**
 * The webcam hand mouse, for the dashboard (v13.6).
 *
 * Asked for as "where do I see the hand tracking on the dashboard" — and the
 * honest answer was nowhere: it existed only as a floating OpenCV window and a
 * tool you had to ask. A gesture mouse is the one feature you cannot debug by
 * looking at the thing it controls, because your hand is busy and the pointer is
 * the symptom, so it needs a readout of its own.
 */
export function useHandMouse(enabled = true) {
  const [status, setStatus] = useState<HandMouseStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const timer = useRef<number | null>(null);
  const cancelled = useRef(false);

  const refresh = useCallback(async () => {
    try {
      const next = await getHandMouse();
      if (!cancelled.current) setStatus(next);
      return next;
    } catch {
      // A failed poll is not a hand-mouse failure: the offline badge covers "the
      // backend is unreachable", and blanking the panel would lose the last
      // thing it knew.
      return null;
    }
  }, []);

  useEffect(() => {
    if (!enabled) return undefined;
    cancelled.current = false;
    let stopped = false;

    const tick = async () => {
      const next = await refresh();
      if (stopped) return;
      const delay = next?.running ? LIVE_MS : IDLE_MS;
      timer.current = window.setTimeout(tick, delay);
    };
    tick();

    return () => {
      stopped = true;
      cancelled.current = true;
      if (timer.current != null) window.clearTimeout(timer.current);
    };
  }, [enabled, refresh]);

  const act = useCallback(
    async (fn: () => Promise<{ ok?: boolean; summary?: string }>) => {
      setBusy(true);
      setError('');
      try {
        const reply = await fn();
        if (reply && reply.ok === false) setError(reply.summary || 'That did not work.');
        await refresh();
        return reply;
      } catch (err) {
        setError(err instanceof Error ? err.message : 'That did not work.');
        return null;
      } finally {
        setBusy(false);
      }
    },
    [refresh],
  );

  return {
    status,
    busy,
    error,
    refresh,
    start: (dryRun = false) => act(() => startHandMouse({ dryRun })),
    stop: () => act(() => stopHandMouse()),
    // v13.8: the pointer's speed is the size of the control rectangle, and it
    // applies while tracking - so the slider is usable *while* you aim with it.
    setSpeed: (speed: number) => act(() => updateSettings({ handSpeed: speed })),
    // Optimistic, because a gesture map you have just changed should redraw at
    // once; the refresh inside `act` is what makes it true.
    bind: (pose: string, action: string) => {
      setStatus((prev) =>
        prev ? { ...prev, bindings: { ...prev.bindings, [pose]: action },
                 poses: prev.poses.map((row) => (row.pose === pose ? { ...row, action } : row)) }
             : prev);
      return act(() => bindHandGesture(pose, action));
    },
  };
}
