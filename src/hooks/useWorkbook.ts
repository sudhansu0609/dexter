import { useCallback, useEffect, useState } from 'react';
import { addCommitment, closeOutDay, finishCommitment, getWorkbook, rollCommitment } from '../api';
import type { WorkbookSnapshot } from '../types';

/**
 * The workbook: what the user is working towards, what they committed to, and
 * what is slipping.
 *
 * Refreshed after every turn rather than polled, because a voice command is the
 * main way this data changes ("I'm committing to X", "I finished Y").
 */
export function useWorkbook() {
  const [snapshot, setSnapshot] = useState<WorkbookSnapshot | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setSnapshot(await getWorkbook());
    } catch {
      // The offline badge already reports an unreachable backend; a failed
      // refresh should leave the last known state on screen, not blank it.
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const withRefresh = async (action: () => Promise<unknown>) => {
    setBusy(true);
    try {
      await action();
      await refresh();
    } finally {
      setBusy(false);
    }
  };

  return {
    snapshot,
    busy,
    refresh,
    commit: (text: string, when?: string) => withRefresh(() => addCommitment(text, when)),
    keep: (id: number) => withRefresh(() => finishCommitment(id)),
    roll: (id: number) => withRefresh(() => rollCommitment(id)),
    closeOut: () => withRefresh(closeOutDay),
  };
}
