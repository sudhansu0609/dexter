import { useEffect, useState } from 'react';
import { Settings as SettingsIcon, X } from 'lucide-react';
import { forgetVoiceProfile, getProfile, getSettings, getVoiceProfile, previewVoice, updateProfile, updateSettings, type VoiceProfile } from '../api';
import type { SettingRow, UserProfile } from '../types';
import type { VoiceApi } from '../hooks/useVoice';

// Voice (v10.2): the microphone, and whose voice Dexter answers to. These need
// the live pipeline (device list, level, enrolment), so they are not plain
// settings rows.
function VoiceSection({ voice }: { voice: VoiceApi }) {
  const [profile, setProfile] = useState<VoiceProfile | null>(null);
  const [note, setNote] = useState('');

  const load = () => getVoiceProfile().then(setProfile).catch(() => setProfile(null));
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    if (voice.enroll.done) {
      setNote(voice.enroll.message || '');
      load();
    }
  }, [voice.enroll.done, voice.enroll.message]);
  useEffect(() => {
    voice.refreshMics();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [voice.state === 'idle']);

  const live = voice.state !== 'off' && voice.state !== 'unavailable';
  const brioMic = voice.mics.find((m) => /brio/i.test(m.label));

  return (
    <div className="dexter-voice-settings">
      <div className="row">
        <label>
          Microphone
          <small>
            {voice.micSilent
              ? 'This one is delivering silence. Pick another.'
              : voice.micLabel
                ? `Using: ${voice.micLabel}`
                : brioMic
                  ? `Defaulting to Brio (${brioMic.label})`
                  : 'The system default, unless it looks virtual.'}
          </small>
        </label>
        <select
          value={voice.micId || ''}
          onChange={(e) => voice.selectMic(e.target.value)}
          disabled={!live}
          aria-label="Microphone"
        >
          <option value="">{brioMic ? `Automatic (${brioMic.label})` : 'Automatic'}</option>
          {voice.mics.map((m) => (
            <option key={m.deviceId} value={m.deviceId}>
              {m.label}
            </option>
          ))}
        </select>
        <span className="dexter-voice-meter" style={{ flexBasis: 60 }}>
          <span style={{ width: `${Math.round(voice.level * 100)}%` }} />
        </span>
      </div>

      <div className="row">
        <label>
          Learn my voice
          <small>
            {profile?.enrolled
              ? `Enrolled ${profile.enrolledAt || ''} (${profile.enrolledSeconds || 0} s). ${profile.strict ? 'Only you are answered.' : 'Anyone is answered.'}`
              : profile && !profile.available
                ? 'Speaker recognition is not installed on the server.'
                : 'Ten seconds of you talking, so Dexter answers you and ignores everyone else - including its own echo.'}
            {note && <> {note}</>}
          </small>
        </label>
        {voice.enroll.active ? (
          <>
            <span style={{ fontSize: 11, color: 'var(--warn)', fontWeight: 600 }}>
              {voice.enroll.seconds.toFixed(0)} / {voice.enroll.target.toFixed(0)} s
            </span>
            <button type="button" className="dexter-chip" onClick={voice.cancelEnroll}>
              Cancel
            </button>
          </>
        ) : (
          <>
            <button type="button" className="dexter-chip" onClick={voice.startEnroll} disabled={voice.state === 'unavailable' || profile?.available === false}>
              {profile?.enrolled ? 'Learn again' : 'Learn my voice'}
            </button>
            {profile?.enrolled && (
              <button
                type="button"
                className="dexter-chip"
                onClick={() =>
                  forgetVoiceProfile()
                    .then((p) => {
                      setProfile(p);
                      setNote('Forgotten.');
                    })
                    .catch(() => setNote('Could not forget.'))
                }
              >
                Forget
              </button>
            )}
          </>
        )}
      </div>
      {voice.enroll.active && (
        <small>Read anything aloud in your normal voice: a paragraph from a page, or just tell Dexter about your day.</small>
      )}
    </div>
  );
}

const GROUP_LABELS: Record<string, string> = {
  model: 'Model',
  voice: 'Voice',
  behaviour: 'Behaviour',
  knowledge: 'Knowledge',
  // v9: what Dexter asks Sentinel for, and what it is allowed to unload.
  machine: 'Machine',
  // v13.6: the webcam hand mouse, including which pose does what.
  hand: 'Hand mouse',
  general: 'General',
};

const PROFILE_FIELDS: Array<{ key: keyof UserProfile; label: string; hint: string }> = [
  { key: 'name', label: 'Your name', hint: 'How Dexter should address you' },
  { key: 'timezone', label: 'Timezone', hint: 'e.g. Asia/Kolkata' },
  { key: 'location', label: 'Location', hint: 'City or region' },
  { key: 'working_hours', label: 'Working hours', hint: 'e.g. 10:00-19:00, weekdays' },
  { key: 'roles', label: 'Roles', hint: 'comma-separated, e.g. YouTube creator, editor' },
  { key: 'preferences', label: 'Preferences', hint: 'comma-separated, e.g. short replies' },
  { key: 'channels', label: 'YouTube channels', hint: 'comma-separated Name=UCchannelId' },
  { key: 'notes', label: 'Anything else', hint: 'Free text Dexter should keep in mind' },
];

function profileFieldText(profile: UserProfile, key: keyof UserProfile): string {
  const value = profile[key];
  if (Array.isArray(value)) {
    return value
      .map((v) => (typeof v === 'string' ? v : v.channel_id ? `${v.name}=${v.channel_id}` : v.name))
      .join(', ');
  }
  return String(value ?? '');
}

// Who Dexter works for (roadmap v5, 4.2). Saved on blur, field by field.
function ProfileSection() {
  const [profile, setProfile] = useState<UserProfile | null>(null);
  const [note, setNote] = useState('');

  useEffect(() => {
    getProfile()
      .then((r) => setProfile(r.profile))
      .catch(() => setNote('Could not load your profile.'));
  }, []);

  const save = async (key: keyof UserProfile, raw: string) => {
    if (!profile) return;
    const value: unknown =
      key === 'roles' || key === 'preferences' || key === 'channels'
        ? raw.split(',').map((s) => s.trim()).filter(Boolean)
        : raw;
    try {
      const result = await updateProfile({ [key]: value } as Partial<UserProfile>);
      setProfile(result.profile);
      setNote('Saved.');
    } catch {
      setNote('Could not save your profile.');
    }
  };

  return (
    <div style={{ marginBottom: '10px' }}>
      <div style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.08em', color: 'var(--muted)', marginBottom: '4px', display: 'flex', justifyContent: 'space-between' }}>
        <span>About you</span>
        <span style={{ textTransform: 'none', letterSpacing: 0 }}>{note}</span>
      </div>
      {profile &&
        PROFILE_FIELDS.map((field) => (
          <div key={field.key} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '5px 0', borderBottom: '1px solid var(--surface-2)' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: '12px', color: 'var(--text)' }}>{field.label}</div>
              <div style={{ fontSize: '10px', color: 'var(--muted)', lineHeight: 1.35 }}>{field.hint}</div>
            </div>
            <input
              type="text"
              defaultValue={profileFieldText(profile, field.key)}
              onBlur={(e) => save(field.key, e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
              style={{ width: '150px', background: 'var(--surface-2)', color: 'var(--text)', border: '1px solid var(--border)', borderRadius: '6px', fontSize: '11px', padding: '3px 5px' }}
            />
          </div>
        ))}
    </div>
  );
}

/**
 * Change how Dexter behaves without editing .env and restarting.
 *
 * Every knob used to be a module constant read once at import, so tuning
 * anything meant a file edit and a restart - which meant in practice that
 * nothing was ever tuned and the defaults were the product.
 */
export function SettingsPanel({ onClose, voice }: { onClose: () => void; voice?: VoiceApi }) {
  const [trying, setTrying] = useState('');

  const tryVoice = async (key: string) => {
    setTrying(key);
    try {
      const blob = await previewVoice(key);
      if (voice) voice.playBlob(blob);
      else new Audio(URL.createObjectURL(blob)).play().catch(() => undefined);
    } catch {
      setNote('Could not play a preview.');
    } finally {
      setTrying('');
    }
  };
  const [rows, setRows] = useState<SettingRow[]>([]);
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState('');

  useEffect(() => {
    getSettings()
      .then((r) => setRows(r.settings))
      .catch(() => setNote('Could not load settings.'));
  }, []);

  const change = async (key: string, value: unknown) => {
    setRows((prev) => prev.map((r) => (r.key === key ? { ...r, value } : r)));
    setSaving(true);
    try {
      const result = await updateSettings({ [key]: value });
      setRows(result.settings);
      // Say plainly when something will not take effect until next launch,
      // rather than letting it look applied.
      setNote(
        result.needsRestart.includes(key)
          ? 'Saved — takes effect next time Dexter starts.'
          : 'Saved.',
      );
    } catch {
      setNote('Could not save that.');
    } finally {
      setSaving(false);
    }
  };

  const groups = Array.from(new Set(rows.map((r) => r.group)));

  return (
    <div className="dexter-panel" style={{ padding: '12px', marginBottom: '14px' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '10px' }}>
        <SettingsIcon size={15} style={{ color: 'var(--accent)' }} />
        <h2 style={{ fontSize: '13px', color: 'var(--text)', margin: 0, flex: 1 }}>Settings</h2>
        <span style={{ fontSize: '11px', color: 'var(--muted)' }}>{saving ? 'Saving…' : note}</span>
        <button
          onClick={onClose}
          style={{ background: 'transparent', border: 'none', color: 'var(--muted)', cursor: 'pointer' }}
        >
          <X size={14} />
        </button>
      </div>

      <ProfileSection />

      {groups.map((group) => (
        <div key={group} style={{ marginBottom: '10px' }}>
          <div
            style={{
              fontSize: '10px',
              textTransform: 'uppercase',
              letterSpacing: '0.08em',
              color: 'var(--muted)',
              marginBottom: '4px',
            }}
          >
            {GROUP_LABELS[group] ?? group}
          </div>

          {group === 'voice' && voice && <VoiceSection voice={voice} />}

          {rows
            .filter((r) => r.group === group)
            .map((row) => (
              <div
                key={row.key}
                style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  padding: '5px 0',
                  borderBottom: '1px solid var(--surface-2)',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '12px', color: 'var(--text)' }}>{row.label}</div>
                  {row.help && (
                    <div style={{ fontSize: '10px', color: 'var(--muted)', lineHeight: 1.35 }}>
                      {row.help}
                      {!row.liveReload && ' (needs restart)'}
                    </div>
                  )}
                </div>

                {row.kind === 'bool' && (
                  <input
                    type="checkbox"
                    checked={Boolean(row.value)}
                    onChange={(e) => change(row.key, e.target.checked)}
                    style={{ cursor: 'pointer', flexShrink: 0 }}
                  />
                )}

                {row.key === 'voiceKey' && (
                  <button
                    type="button"
                    className="dexter-chip"
                    onClick={() => tryVoice(String(row.value))}
                    disabled={Boolean(trying)}
                    title="Hear this voice"
                  >
                    {trying ? 'Playing…' : 'Try'}
                  </button>
                )}

                {(row.kind === 'choice' || row.kind === 'model') && (
                  <select
                    value={String(row.value)}
                    onChange={(e) => change(row.key, e.target.value)}
                    style={{
                      background: 'var(--surface-2)',
                      color: 'var(--text)',
                      border: '1px solid var(--border)',
                      borderRadius: '6px',
                      fontSize: '11px',
                      padding: '3px 5px',
                    }}
                  >
                    {row.kind === 'model' && (
                      <option value="">
                        {row.key === 'fastModel'
                          ? 'Off'
                          : row.key === 'smartModelFallback'
                            ? 'No second rung'
                            : 'Backend default'}
                      </option>
                    )}
                    {row.options.map((option) => (
                      <option key={option} value={option}>
                        {option}
                      </option>
                    ))}
                  </select>
                )}

                {(row.kind === 'int' || row.kind === 'float' || row.kind === 'string') && (
                  <input
                    type={row.kind === 'string' ? 'text' : 'number'}
                    step={row.kind === 'float' ? '0.1' : '1'}
                    defaultValue={String(row.value)}
                    onBlur={(e) => change(row.key, e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
                    style={{
                      width: row.kind === 'string' ? '110px' : '64px',
                      background: 'var(--surface-2)',
                      color: 'var(--text)',
                      border: '1px solid var(--border)',
                      borderRadius: '6px',
                      fontSize: '11px',
                      padding: '3px 5px',
                    }}
                  />
                )}
              </div>
            ))}
        </div>
      ))}
    </div>
  );
}
