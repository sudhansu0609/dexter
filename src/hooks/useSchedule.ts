import { useCallback, useEffect, useState } from 'react';
import {
  createSchedule,
  deleteSchedule,
  getSchedule,
  runScheduleNow,
  updateSchedule,
} from '../api';
import type { ScheduleAction, ScheduleItem } from '../types';

// The scheduler screen's state + actions. `enabled` lets a caller hold the fetch
// until the tab/panel is actually shown, so the compact desktop layout does not
// poll for something it never renders.
export function useSchedule(enabled = true) {
  const [items, setItems] = useState<ScheduleItem[]>([]);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      const data = await getSchedule();
      setItems(data.items || []);
    } catch (e) {
      console.warn('Could not load schedule', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (enabled) refresh();
  }, [enabled, refresh]);

  const add = useCallback(
    async (action: ScheduleAction, when: string) => {
      const res = await createSchedule(action, when);
      if (!res.ok) throw new Error(res.error || 'Could not schedule that.');
      await refresh();
      return res;
    },
    [refresh],
  );

  const edit = useCallback(
    async (id: string, body: { action?: ScheduleAction; when?: string }) => {
      await updateSchedule(id, body);
      await refresh();
    },
    [refresh],
  );

  const remove = useCallback(
    async (id: string) => {
      await deleteSchedule(id);
      await refresh();
    },
    [refresh],
  );

  const runNow = useCallback(async (id: string) => {
    await runScheduleNow(id);
  }, []);

  return { items, loading, refresh, add, edit, remove, runNow };
}
