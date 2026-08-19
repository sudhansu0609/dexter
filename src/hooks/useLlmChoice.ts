import { useCallback, useEffect, useState } from 'react';
import { getModels, type LlmModelsResponse } from '../api';

// User's picked LLM backend + model, persisted like the dexterVoiceEngine
// preference (localStorage-only, read at startup). 'auto' provider means "let
// the server's configured preference decide"; a null model means "provider's
// default model".
const PROVIDER_KEY = 'dexterProvider';
const MODEL_KEY = 'dexterModel';

export interface LlmChoice {
  provider: string; // 'auto' | 'lmstudio' | 'ollama'
  model: string | null;
}

// Non-React accessor so the chat sender can read the current choice at send
// time. Both typed and voice commands go through the same handler, so reading
// localStorage here means one selection covers every message without threading
// state through the voice loop.
export function getLlmChoice(): LlmChoice {
  return {
    provider: localStorage.getItem(PROVIDER_KEY) || 'auto',
    model: localStorage.getItem(MODEL_KEY) || null,
  };
}

export function useLlmChoice() {
  const [provider, setProviderState] = useState<string>(
    () => localStorage.getItem(PROVIDER_KEY) || 'auto',
  );
  const [model, setModelState] = useState<string | null>(
    () => localStorage.getItem(MODEL_KEY) || null,
  );
  const [data, setData] = useState<LlmModelsResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true);
    try {
      setData(await getModels());
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const setProvider = useCallback((p: string) => {
    setProviderState(p);
    localStorage.setItem(PROVIDER_KEY, p);
    // Clear the model on a provider switch: the previous model id won't exist on
    // the new backend, and 'auto'/default is a safe fallback until re-picked.
    setModelState(null);
    localStorage.removeItem(MODEL_KEY);
  }, []);

  const setModel = useCallback((m: string | null) => {
    setModelState(m);
    if (m) localStorage.setItem(MODEL_KEY, m);
    else localStorage.removeItem(MODEL_KEY);
  }, []);

  return { provider, model, data, loading, refresh, setProvider, setModel };
}
