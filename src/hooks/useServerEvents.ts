import { useEffect, useRef, useState } from 'react';
import { addReminder, cancelTask, confirmTask, eventsUrl, getTasks } from '../api';
import type { BgTask, ReminderBanner } from '../types';

interface EventDeps {
  // Reminders can change the planner (a due item is checked off server-side).
  refreshPlanner: () => void;
  // Studio events (a step finished, approval needed) change the Studio panel.
  refreshBuzzcaf?: () => void;
  // A message Dexter starts on its own (check-in, code/Studio event) → the chat feed.
  onCompanion?: (payload: { kind: string; text: string; quickReplies: string[] }) => void;
  // v9: the steward acted, or Sentinel pushed something about memory or disk.
  onResources?: () => void;
  // v10: something worth saying out loud arrived (a reminder, a check-in).
  onSpeak?: (text: string, emotion: string | undefined, isReminder: boolean) => void;
}

// A notice about the machine that persists until it is read: the GPU being too
// busy to load a model, a drive going red, Sentinel stopping something. These
// are not transient - the condition is still true four seconds later - so they
// are banners, not toasts.
export interface MachineNotice {
  id: string;
  kind: 'gpu_busy' | 'disk_tight' | 'evicted';
  text: string;
}

// Owns the SSE push channel - the backend's only way to reach the user unprompted
// - plus the reminder banners and background-task tray it feeds. Reconnects on
// error (roadmap Phase 6, item 17). The listeners are registered once, so they
// dispatch through a ref that always holds the current-render callbacks.
export function useServerEvents({ refreshPlanner, refreshBuzzcaf, onCompanion, onResources, onSpeak }: EventDeps) {
  const [reminders, setReminders] = useState<ReminderBanner[]>([]);
  const [bgTasks, setBgTasks] = useState<BgTask[]>([]);
  const [connected, setConnected] = useState(false);
  const [notices, setNotices] = useState<MachineNotice[]>([]);
  // Bumped on every `resources` frame so the panels refetch instead of polling harder.
  const [resourceTick, setResourceTick] = useState(0);

  const depsRef = useRef({ refreshPlanner, refreshBuzzcaf, onCompanion, onResources, onSpeak });
  depsRef.current = { refreshPlanner, refreshBuzzcaf, onCompanion, onResources, onSpeak };

  const dismissNotice = (id: string) => setNotices((prev) => prev.filter((n) => n.id !== id));

  const pushNotice = (kind: MachineNotice['kind'], text: string) => {
    if (!text) return;
    setNotices((prev) => {
      // One notice per kind: the GPU being busy twice is still one fact.
      const rest = prev.filter((n) => n.kind !== kind);
      return [...rest, { id: `${kind}-${Date.now()}`, kind, text }];
    });
  };

  const dismissReminder = (id: string) => {
    setReminders((prev) => prev.filter((r) => r.id !== id));
  };

  const snoozeReminder = async (reminder: ReminderBanner) => {
    dismissReminder(reminder.id);
    try {
      await addReminder(reminder.text, 'in 10 minutes');
    } catch (e) {
      console.warn('Could not snooze reminder', e);
    }
  };

  const answerTask = async (taskId: string, approve: boolean) => {
    try {
      await confirmTask(taskId, approve);
    } catch (e) {
      console.warn('Could not answer task checkpoint', e);
    }
  };

  const cancelBgTask = async (taskId: string) => {
    try {
      await cancelTask(taskId);
    } catch (e) {
      console.warn('Could not cancel task', e);
    }
  };

  const dismissBgTask = (id: string) => {
    setBgTasks((prev) => prev.filter((t) => t.id !== id));
  };

  // Initial background-task snapshot.
  useEffect(() => {
    (async () => {
      try {
        const data = await getTasks();
        if (Array.isArray(data)) setBgTasks(data.slice(0, 5));
      } catch (e) {
        console.warn('Could not load background tasks', e);
      }
    })();
  }, []);

  // Push channel. Until this existed the backend had no way to reach the user,
  // which is why reminders were impossible - nothing could arrive unprompted.
  useEffect(() => {
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;
    let closed = false;

    const connect = async () => {
      if (closed) return;
      // The stream URL carries the auth token (EventSource cannot send headers).
      const url = await eventsUrl();
      if (closed) return;
      source = new EventSource(url);

      source.onopen = () => setConnected(true);

      source.addEventListener('reminder', (evt: MessageEvent) => {
        try {
          const { payload } = JSON.parse(evt.data);
          if (payload?.banner !== false) {
            setReminders((prev) => [
              ...prev,
              { id: payload.id || `rem-${Date.now()}`, text: payload.text, missed: payload.missed },
            ]);
          }
          if (payload?.speak) {
            // Flagged as a reminder so it is delivered in the urgent register.
            depsRef.current.onSpeak?.(payload.spoken || payload.text, 'urgent', true);
          }
          depsRef.current.refreshPlanner();
        } catch (e) {
          console.warn('Malformed reminder event', e);
        }
      });

      source.addEventListener('task', (evt: MessageEvent) => {
        try {
          const { payload } = JSON.parse(evt.data);
          if (!payload?.id) return;
          setBgTasks((prev) => [payload, ...prev.filter((t) => t.id !== payload.id)].slice(0, 5));
        } catch (e) {
          console.warn('Malformed task event', e);
        }
      });

      source.addEventListener('studio', () => {
        depsRef.current.refreshBuzzcaf?.();
      });

      // v9: the machine. `resources` is a nudge to refetch; the other three are
      // things the user has to be told, so they land as banners.
      source.addEventListener('resources', () => {
        setResourceTick((n) => n + 1);
        depsRef.current.onResources?.();
      });

      source.addEventListener('gpu_busy', (evt: MessageEvent) => {
        try {
          const { payload } = JSON.parse(evt.data);
          pushNotice('gpu_busy', payload?.message || 'The GPU is too busy for my larger model right now.');
          setResourceTick((n) => n + 1);
        } catch (e) {
          console.warn('Malformed gpu_busy event', e);
        }
      });

      source.addEventListener('disk_tight', (evt: MessageEvent) => {
        try {
          const { payload } = JSON.parse(evt.data);
          const freeGb = payload?.freeBytes ? (payload.freeBytes / 1024 ** 3).toFixed(0) : '?';
          pushNotice(
            'disk_tight',
            `${payload?.drive || 'A drive'} is down to ${freeGb} GB free (${payload?.level || 'low'}). Say "clean up space" and I'll ask Sentinel to run a safe clean.`,
          );
        } catch (e) {
          console.warn('Malformed disk_tight event', e);
        }
      });

      source.addEventListener('evicted', (evt: MessageEvent) => {
        try {
          const { payload } = JSON.parse(evt.data);
          pushNotice('evicted', payload?.text || 'Sentinel stopped a background process to free memory.');
          setResourceTick((n) => n + 1);
        } catch (e) {
          console.warn('Malformed evicted event', e);
        }
      });

      source.addEventListener('companion', (evt: MessageEvent) => {
        try {
          const { payload } = JSON.parse(evt.data);
          if (payload?.text) {
            depsRef.current.onCompanion?.(payload);
            // Dexter speaking first should sound like it: warm, not urgent.
            depsRef.current.onSpeak?.(payload.text, 'warm', false);
          }
          depsRef.current.refreshPlanner();
        } catch (e) {
          console.warn('Malformed companion event', e);
        }
      });

      source.onerror = () => {
        setConnected(false);
        source?.close();
        if (!closed) retry = setTimeout(connect, 4000);
      };
    };

    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      source?.close();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return {
    reminders,
    bgTasks,
    connected,
    notices,
    dismissNotice,
    resourceTick,
    dismissReminder,
    snoozeReminder,
    answerTask,
    cancelBgTask,
    dismissBgTask,
  };
}
