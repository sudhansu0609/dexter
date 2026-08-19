import { useCallback, useEffect, useState } from 'react';
import { curateOvernight, getOvernightQueue } from '../api';
import type { OverNightItem } from '../types';

// spilledCoffeeAI overnight content queue + the "curate now" action.
export function useOvernight() {
  const [items, setItems] = useState<OverNightItem[]>([]);
  const [isCurating, setIsCurating] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const data = await getOvernightQueue();
      setItems(Array.isArray(data) ? data : []);
    } catch (e) {
      console.warn('Could not load overnight queue', e);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const curate = useCallback(async () => {
    setIsCurating(true);
    try {
      await curateOvernight('AI Tech & Viral Trends', 3);
      await refresh();
    } catch (e) {
      console.warn('Could not curate ideas', e);
    } finally {
      setIsCurating(false);
    }
  }, [refresh]);

  return { items, isCurating, curate, refresh };
}
