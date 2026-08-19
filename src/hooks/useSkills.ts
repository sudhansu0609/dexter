import { useCallback, useEffect, useState } from 'react';
import { getSkills } from '../api';
import type { SkillItem } from '../types';

// Self-learned skills list (display-only).
export function useSkills() {
  const [skills, setSkills] = useState<SkillItem[]>([]);

  const refresh = useCallback(async () => {
    try {
      const data = await getSkills();
      setSkills(Array.isArray(data) ? data : []);
    } catch (e) {
      console.warn('Could not load skills', e);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { skills, refresh };
}
