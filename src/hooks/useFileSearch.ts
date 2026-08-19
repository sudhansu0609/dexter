import { useCallback, useState, type FormEvent } from 'react';
import { openFile as openFileApi, searchFiles } from '../api';
import type { FileResult } from '../types';

// Local PC file explorer: search + open.
export function useFileSearch() {
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<FileResult[]>([]);
  const [isSearching, setIsSearching] = useState(false);

  const search = useCallback(
    async (e: FormEvent) => {
      e.preventDefault();
      if (!query.trim()) return;
      setIsSearching(true);
      try {
        const data = await searchFiles(query);
        setResults(data.results || []);
      } catch (err) {
        console.warn('File search failed', err);
      } finally {
        setIsSearching(false);
      }
    },
    [query],
  );

  const openFile = useCallback(async (path: string) => {
    try {
      await openFileApi(path);
    } catch (e) {
      console.warn('Could not open file', e);
    }
  }, []);

  return { query, setQuery, results, isSearching, search, openFile };
}
