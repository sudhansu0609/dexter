import { useCallback, useEffect, useState } from 'react';
import {
  approveBuzzcafStep,
  executeBuzzcafStep,
  getBuzzcafHealth,
  getBuzzcafProjects,
  getBuzzcafState,
  startBuzzcaf,
} from '../api';
import type { BuzzcafPending, BuzzcafProject } from '../types';

// BuzzcafAI Studio integration: health, project list, run-a-step, and launch.
// Status narration is surfaced in the chat feed, so a `pushMessage` sink is
// injected rather than the hook owning chat state.
export function useBuzzcaf(pushMessage: (text: string) => void) {
  const [projects, setProjects] = useState<BuzzcafProject[]>([]);
  const [online, setOnline] = useState(false);
  const [isExecutingStep, setIsExecutingStep] = useState(false);
  const [isStarting, setIsStarting] = useState(false);
  const [pending, setPending] = useState<BuzzcafPending[]>([]);
  const [isApproving, setIsApproving] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const status = await getBuzzcafHealth();
      setOnline(status.online || false);
      if (status.online) {
        const data = await getBuzzcafProjects();
        setProjects(data || []);
        try {
          const state = await getBuzzcafState();
          setPending(state.pending_approvals || []);
        } catch {
          setPending([]);
        }
      } else {
        setPending([]);
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

  const approve = useCallback(
    async (projectId: string) => {
      setIsApproving(true);
      try {
        const data = await approveBuzzcafStep(projectId);
        await refresh();
        pushMessage(`Approved; "${data.name || projectId}" is now at ${data.current_step || 'the next step'}.`);
      } catch (e) {
        console.warn('Studio approve failed', e);
        pushMessage('The Studio could not approve that step.');
      } finally {
        setIsApproving(false);
      }
    },
    [pushMessage, refresh],
  );

  return { projects, pending, online, isExecutingStep, isStarting, isApproving, executeStep, approve, start, refresh };
}
