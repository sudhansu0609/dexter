import { useCallback, useEffect, useState } from 'react';
import { getMission } from '../api';
import type { MissionStatus } from '../types';

// The mission card's data (roadmap v7): north star, this week's focus, today's
// win, blockers, wins and open loops. Refreshed after every chat turn and on
// every companion message, since both can change it.
export function useMission() {
  const [mission, setMission] = useState<MissionStatus | null>(null);

  const refresh = useCallback(async () => {
    try {
      setMission(await getMission());
    } catch {
      // Offline: keep whatever we had.
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { mission, refresh };
}
