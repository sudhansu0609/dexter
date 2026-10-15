import { useEffect } from 'react';
import { ExternalLink, Mic, MicOff, Volume2, VolumeX, X } from 'lucide-react';
import type { VoiceApi } from '../hooks/useVoice';
import { useLlmChoice } from '../hooks/useLlmChoice';

// The pop-up's own settings (v11.1). The pop-up is the window that is usually
// up, and it listens; but the microphone it uses and the model it talks to
// could only be changed in the full window. That is the wrong place for "it
// picked the wrong mic" - you notice that here, mid-sentence. So the two
// choices that matter minute to minute live here too. Everything else is one
// click away in the full window.
//
// Both choices are shared with the full window: the mic id and the model live
// in localStorage, which the two windows share.

const PROVIDER_LABELS: Record<string, string> = {
  auto: 'Auto',
  lmstudio: 'LM Studio',
  ollama: 'Ollama',
  claude: 'Claude',
};

interface Props {
  voice: VoiceApi;
  onClose: () => void;
  onOpenFull: () => void;
}

export default function PopupSettings({ voice, onClose, onOpenFull }: Props) {
  const llm = useLlmChoice();

  // The device list needs a permission grant to carry labels; ask again when
  // the panel opens so a mic plugged in since is listed.
  useEffect(() => {
    voice.refreshMics();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const info = llm.provider !== 'auto' ? llm.data?.providers?.[llm.provider] : undefined;
  const models = info?.models ?? [];
  const autoTarget =
    llm.data?.preferred && llm.data.preferred !== 'auto'
      ? PROVIDER_LABELS[llm.data.preferred] ?? llm.data.preferred
      : '';
  const voiceOff = voice.state === 'off' || voice.state === 'unavailable';
  const brioMic = voice.mics.find((m) => /brio/i.test(m.label));

  return (
    <div className="dexter-popup-settings" role="dialog" aria-label="Quick settings">
      <div className="head">
        <span>Quick settings</span>
        <button type="button" title="Close" aria-label="Close settings" onClick={onClose}>
          <X size={14} />
        </button>
      </div>

      <label className="field">
        <span className="name">Microphone</span>
        <select
          value={voice.micId || ''}
          onChange={(e) => voice.selectMic(e.target.value)}
          aria-label="Microphone"
          title={voice.state === 'unavailable' ? voice.unavailableReason || 'Voice unavailable' : 'Which microphone Dexter listens on'}
        >
          <option value="">{brioMic ? `Automatic (${brioMic.label})` : 'Automatic'}</option>
          {voice.mics.map((m) => (
            <option key={m.deviceId} value={m.deviceId}>
              {m.label}
            </option>
          ))}
        </select>
        <span className="dexter-voice-meter" title="Microphone level">
          <span style={{ width: `${Math.round(Math.min(1, voice.level) * 100)}%` }} />
        </span>
      </label>
      <div className="note">
        {voice.state === 'unavailable'
          ? `Voice unavailable: ${voice.unavailableReason || 'no microphone'}`
          : voiceOff
            ? 'Voice is off - the mic button turns it on.'
            : voice.micSilent
              ? 'This one is delivering silence. Pick another.'
              : voice.micLabel
                ? `Using: ${voice.micLabel}`
                : voice.mics.length === 0
                  ? 'No microphones listed yet. Turn voice on once to grant access.'
                  : 'The system default, unless it looks virtual.'}
      </div>

      {/* v13: the two ways to quieten Dexter, said plainly, because "mute" means
          both of them to different people on different days. */}
      <label className="field">
        <span className="name">Dexter's voice</span>
        <button
          type="button"
          className="mini"
          aria-pressed={voice.silent}
          onClick={() => voice.toggleSilent()}
          title={voice.silent ? 'Let Dexter speak again' : 'Stop Dexter speaking; it keeps listening'}
        >
          {voice.silent ? <VolumeX size={12} /> : <Volume2 size={12} />}
          {voice.silent ? ' Muted' : ' Speaking'}
        </button>
        <button
          type="button"
          className="mini"
          aria-pressed={voice.muted}
          onClick={() => voice.toggleMuted()}
          title={voice.muted ? 'Turn the microphone back on' : 'Turn the microphone off entirely'}
        >
          {voice.muted ? <MicOff size={12} /> : <Mic size={12} />}
          {voice.muted ? ' Mic off' : ' Mic on'}
        </button>
      </label>
      <div className="note">
        {voice.silent
          ? 'Muted: Dexter answers in writing and still hears you.'
          : 'Dexter speaks its replies. Mute silences the voice without closing the mic.'}
      </div>

      <label className="field">
        <span className="name">Model</span>
        <select value={llm.provider} onChange={(e) => llm.setProvider(e.target.value)} aria-label="LLM provider" title="Which backend answers">
          <option value="auto">{autoTarget ? `Auto (${autoTarget})` : 'Auto'}</option>
          <option value="lmstudio">LM Studio{llm.data && !llm.data.providers?.lmstudio?.online ? ' - offline' : ''}</option>
          <option value="ollama">Ollama{llm.data && !llm.data.providers?.ollama?.online ? ' - offline' : ''}</option>
          <option value="claude">Claude{llm.data && !llm.data.providers?.claude?.online ? ' - offline' : ''}</option>
        </select>
        <select
          value={llm.model ?? ''}
          onChange={(e) => llm.setModel(e.target.value || null)}
          disabled={llm.provider === 'auto' || models.length === 0}
          aria-label="Model"
          title={llm.provider === 'auto' ? 'Auto picks the model' : 'Model'}
        >
          {llm.provider === 'auto' ? (
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
        <button type="button" className="mini" onClick={() => llm.refresh()} title="Refresh models" aria-label="Refresh models">
          {llm.loading ? '…' : '↻'}
        </button>
      </label>
      <div className="note">
        {llm.provider === 'auto'
          ? 'Auto: the fast model answers chat, the smart one handles actions.'
          : info?.online
            ? `${PROVIDER_LABELS[llm.provider]} is online${llm.model ? `: ${llm.model}` : ''}.`
            : `${PROVIDER_LABELS[llm.provider]} is not running.`}
      </div>

      <button type="button" className="dexter-chip more" onClick={onOpenFull}>
        <ExternalLink size={12} /> All settings, voice and wake word
      </button>
    </div>
  );
}
