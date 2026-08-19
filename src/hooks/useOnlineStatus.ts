import { useEffect, useState } from 'react';

// Browser-level connectivity, used to show an offline indicator (roadmap Phase 6,
// item 17). The SSE hook handles its own reconnect; this just reflects whether
// the machine has a network at all.
export function useOnlineStatus(): boolean {
  const [online, setOnline] = useState<boolean>(
    typeof navigator !== 'undefined' ? navigator.onLine : true,
  );

  useEffect(() => {
    const up = () => setOnline(true);
    const down = () => setOnline(false);
    window.addEventListener('online', up);
    window.addEventListener('offline', down);
    return () => {
      window.removeEventListener('online', up);
      window.removeEventListener('offline', down);
    };
  }, []);

  return online;
}
