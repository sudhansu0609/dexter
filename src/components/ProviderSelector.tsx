import { type CSSProperties } from 'react';
import { useLlmChoice } from '../hooks/useLlmChoice';

// Compact LLM picker: choose the backend (Auto / LM Studio / Ollama) and, for a
// specific backend, the model. Selection persists in localStorage and is sent
// with every chat turn (typed and voice) via getLlmChoice() in useChat.
const PROVIDER_LABELS: Record<string, string> = {
  auto: 'Auto',
  lmstudio: 'LM Studio',
  ollama: 'Ollama',
};

const selectStyle: CSSProperties = {
  padding: '5px 8px',
  borderRadius: '8px',
  fontSize: '11px',
  maxWidth: '150px',
};

export default function ProviderSelector() {
  const { provider, model, data, loading, refresh, setProvider, setModel } = useLlmChoice();

  const info = provider !== 'auto' ? data?.providers?.[provider] : undefined;
  const models = info?.models ?? [];
  const online = provider === 'auto' ? true : Boolean(info?.online);
  const autoTarget = data?.preferred && data.preferred !== 'auto' ? PROVIDER_LABELS[data.preferred] ?? data.preferred : '';

  return (
    <div
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: '8px',
        margin: '10px 0 4px',
        flexWrap: 'wrap',
        appRegion: 'no-drag',
      } as CSSProperties}
    >
      <span
        style={{
          fontSize: '10px',
          color: 'var(--text-muted)',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
        }}
      >
        Model
      </span>

      <span
        title={online ? 'online' : 'offline'}
        style={{
          width: '8px',
          height: '8px',
          borderRadius: '50%',
          background: online ? 'var(--accent-emerald)' : 'var(--danger)',
          boxShadow: online ? '0 0 8px #bfe6cc' : 'none',
          flex: '0 0 auto',
        }}
      />

      <select
        value={provider}
        onChange={(e) => setProvider(e.target.value)}
        style={selectStyle}
        title="LLM provider"
      >
        <option value="auto">{autoTarget ? `Auto (${autoTarget})` : 'Auto'}</option>
        <option value="lmstudio">
          LM Studio{data && !data.providers?.lmstudio?.online ? ' — offline' : ''}
        </option>
        <option value="ollama">
          Ollama{data && !data.providers?.ollama?.online ? ' — offline' : ''}
        </option>
      </select>

      <select
        value={model ?? ''}
        onChange={(e) => setModel(e.target.value || null)}
        disabled={provider === 'auto' || models.length === 0}
        style={{ ...selectStyle, opacity: provider === 'auto' ? 0.5 : 1 }}
        title={provider === 'auto' ? 'Auto picks the model' : 'Model'}
      >
        {provider === 'auto' ? (
          <option value="">Auto</option>
        ) : (
          <>
            <option value="">Default</option>
            {models.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))}
          </>
        )}
      </select>

      <button
        type="button"
        onClick={() => refresh()}
        title="Refresh models"
        style={{
          background: 'var(--surface-2)',
          color: 'var(--accent)',
          border: '1px solid var(--bg-card-border)',
          padding: '4px 9px',
          borderRadius: '8px',
          fontSize: '11px',
        }}
      >
        {loading ? '…' : '↻'}
      </button>
    </div>
  );
}
