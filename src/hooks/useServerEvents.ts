import { useEffect, useRef, useState } from 'react';
import { addReminder, cancelTask, confirmTask, eventsUrl, getTasks } from '../api';
import type { BgTask, ReminderBanner } from '../types';
import type { VoiceApi } from './useVoice';

interface EventDeps {
  voice: VoiceApi;
  // Reminders can change the planner (a due item is checked off server-side).
  refreshPlanner: () => void;
}

// Owns the SSE push channel - the backend's only way to reach the user unprompted
// - plus the reminder banners and background-task tray it feeds. Reconnects on
// error (roadmap Phase 6, item 17). The listeners are registered once, so they
// dispatch through a ref that always holds the current-render callbacks.
export function useServerEvents({ voice, refreshPlanner }: EventDeps) {
  const [reminders, setReminders] = useState<ReminderBanner[]>([]);
  const [bgTasks, setBgTasks] = useState<BgTask[]>([]);
  const [connected, setConnected] = useState(false);

  const depsRef = useRef({ voice, refreshPlanner });
  depsRef.current = { voice, refreshPlanner };

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
          if (payload?.speak && !depsRef.current.voice.isVoiceMutedRef.current) {
            // Flagged as a reminder so it is delivered in the urgent register.
            depsRef.current.voice.speakText(payload.spoken || payload.text, 'urgent', true);
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
    dismissReminder,
    snoozeReminder,
    answerTask,
    cancelBgTask,
    dismissBgTask,
  };
}
