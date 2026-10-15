import { useState, type CSSProperties } from 'react';
import { useLlmChoice } from '../hooks/useLlmChoice';
import { formatTokens, useTokenUsage } from '../hooks/useTokenUsage';

// Compact LLM picker: choose the backend (Auto / LM Studio / Ollama / Claude)
// and, for a specific backend, the model. Selection persists in localStorage
// and is sent with every chat turn (typed and voice) via getLlmChoice() in
// useChat. Next to it, a running token counter for every model call made.
const PROVIDER_LABELS: Record<string, string> = {
  auto: 'Auto',
  lmstudio: 'LM Studio',
  ollama: 'Ollama',
  claude: 'Claude',
};

const selectStyle: CSSProperties = {
  padding: '5px 8px',
  borderRadius: '8px',
  fontSize: '11px',
  maxWidth: '150px',
};

const chipStyle: CSSProperties = {
  background: 'var(--surface-2)',
  color: 'var(--text-muted)',
  border: '1px solid var(--bg-card-border)',
  padding: '4px 9px',
  borderRadius: '8px',
  fontSize: '11px',
  cursor: 'pointer',
  whiteSpace: 'nowrap',
};

function TokenCounter() {
  const { data, reset, busy } = useTokenUsage();
  const [open, setOpen] = useState(false);
  if (!data) return null;

  const all = data.allTime;
  const ses = data.session;
  const title =
    `All-time: ${all.total_tokens.toLocaleString()} tokens over ${all.calls} calls ` +
    `(${all.prompt_tokens.toLocaleString()} in / ${all.completion_tokens.toLocaleString()} out)\n` +
    `This session: ${ses.total_tokens.toLocaleString()} tokens over ${ses.calls} calls\n` +
    'Click for the breakdown by provider and model.';

  return (
    <span style={{ position: 'relative', display: 'inline-flex' }}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title={title}
        aria-label="Token usage"
        aria-expanded={open}
        style={chipStyle}
      >
        {formatTokens(all.total_tokens)} tok · {all.calls} calls
        {ses.calls > 0 ? ` · ${formatTokens(ses.total_tokens)} today` : ''}
      </button>
      {open && (
        <div
          role="dialog"
          aria-label="Token usage breakdown"
          style={{
            position: 'absolute',
            top: '110%',
            left: 0,
            zIndex: 20,
            minWidth: '280px',
            maxWidth: '360px',
            padding: '10px 12px',
            borderRadius: '10px',
            background: 'var(--surface-2)',
            border: '1px solid var(--bg-card-border)',
            boxShadow: '0 8px 24px rgba(0,0,0,0.25)',
            fontSize: '11px',
            color: 'var(--text)',
          }}
        >
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
            <strong>Tokens used</strong>
            <span style={{ color: 'var(--text-muted)' }}>since {data.since.slice(0, 10)}</span>
          </div>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ color: 'var(--text-muted)', textAlign: 'right' }}>
                <th style={{ textAlign: 'left', fontWeight: 400 }}>model</th>
                <th style={{ fontWeight: 400 }}>calls</th>
                <th style={{ fontWeight: 400 }}>in</th>
                <th style={{ fontWeight: 400 }}>out</th>
                <th style={{ fontWeight: 400 }}>total</th>
              </tr>
            </thead>
            <tbody>
              <tr style={{ textAlign: 'right', fontWeight: 600 }}>
                <td style={{ textAlign: 'left' }}>all</td>
                <td>{all.calls}</td>
                <td>{formatTokens(all.prompt_tokens)}</td>
                <td>{formatTokens(all.completion_tokens)}</td>
                <td>{formatTokens(all.total_tokens)}</td>
              </tr>
              {data.models.slice(0, 12).map((row) => (
                <tr key={row.name} style={{ textAlign: 'right' }}>
                  <td style={{ textAlign: 'left', maxWidth: '170px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }} title={row.name}>
                    {row.name}
                  </td>
                  <td>{row.calls}</td>
                  <td>{formatTokens(row.prompt_tokens)}</td>
                  <td>{formatTokens(row.completion_tokens)}</td>
                  <td>{formatTokens(row.total_tokens)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginTop: '8px', color: 'var(--text-muted)' }}>
            <span>
              This session: {formatTokens(ses.total_tokens)} tok · {ses.calls} calls
            </span>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                if (window.confirm('Reset the all-time token counters?')) reset();
              }}
              style={{ ...chipStyle, color: 'var(--danger)' }}
            >
              Reset
            </button>
          </div>
        </div>
      )}
    </span>
  );
}

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
        <option value="claude">
          Claude{data && !data.providers?.claude?.online ? ' — offline' : ''}
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

      <TokenCounter />
    </div>
  );
}
