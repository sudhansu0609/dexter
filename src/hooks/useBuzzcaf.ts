import { useCallback, useEffect, useState } from 'react';
import {
  executeBuzzcafStep,
  getBuzzcafHealth,
  getBuzzcafProjects,
  startBuzzcaf,
} from '../api';
import type { BuzzcafProject } from '../types';

// BuzzcafAI Studio integration: health, project list, run-a-step, and launch.
// Status narration is surfaced in the chat feed, so a `pushMessage` sink is
// injected rather than the hook owning chat state.
export function useBuzzcaf(pushMessage: (text: string) => void) {
  const [projects, setProjects] = useState<BuzzcafProject[]>([]);
  const [online, setOnline] = useState(false);
  const [isExecutingStep, setIsExecutingStep] = useState(false);
  const [isStarting, setIsStarting] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const status = await getBuzzcafHealth();
      setOnline(status.online || false);
      if (status.online) {
        const data = await getBuzzcafProjects();
        setProjects(data || []);
      }
    } catch (e) {
      console.warn('BuzzcafAI unreachable', e);
      setOnline(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const executeStep = useCallback(
    async (projectId: string) => {
      setIsExecutingStep(true);
      try {
        const data = await executeBuzzcafStep(projectId);
        await refresh();
        pushMessage(
          `Executed step on "${data.name || projectId}". Current step: ${
            data.current_step || 'Unknown'
          }`,
        );
      } catch (e) {
        console.warn('BuzzcafAI step failed', e);
        pushMessage('Failed to execute BuzzcafAI step.');
      } finally {
        setIsExecutingStep(false);
      }
    },
    [pushMessage, refresh],
  );

  const start = useCallback(async () => {
    setIsStarting(true);
    pushMessage('Starting BuzzcafAI Studio... This may take a moment.');
    try {
      const data = await startBuzzcaf();
      if (data.ready) {
        setOnline(true);
        await refresh();
        pushMessage('BuzzcafAI Studio is now online! You can create video projects.');
      } else {
        pushMessage(data.message || 'BuzzcafAI is starting up...');
        setTimeout(refresh, 5000);
      }
    } catch (e) {
      console.warn('BuzzcafAI start failed', e);
      pushMessage('Failed to start BuzzcafAI. Check if the application is installed correctly.');
    } finally {
      setIsStarting(false);
    }
  }, [pushMessage, refresh]);

  return { projects, online, isExecutingStep, isStarting, executeStep, start, refresh };
}
