import { useEffect, useState } from 'react';
import { Settings as SettingsIcon, X } from 'lucide-react';
import { getProfile, getSettings, updateProfile, updateSettings } from '../api';
import type { SettingRow, UserProfile } from '../types';

const GROUP_LABELS: Record<string, string> = {
  model: 'Model',
  behaviour: 'Behaviour',
  knowledge: 'Knowledge',
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
      <div style={{ fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.08em', color: '#64748b', marginBottom: '4px', display: 'flex', justifyContent: 'space-between' }}>
        <span>About you</span>
        <span style={{ textTransform: 'none', letterSpacing: 0 }}>{note}</span>
      </div>
      {profile &&
        PROFILE_FIELDS.map((field) => (
          <div key={field.key} style={{ display: 'flex', alignItems: 'center', gap: '8px', padding: '5px 0', borderBottom: '1px solid rgba(148,163,184,0.1)' }}>
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontSize: '12px', color: '#e2e8f0' }}>{field.label}</div>
              <div style={{ fontSize: '10px', color: '#64748b', lineHeight: 1.35 }}>{field.hint}</div>
            </div>
            <input
              type="text"
              defaultValue={profileFieldText(profile, field.key)}
              onBlur={(e) => save(field.key, e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && (e.target as HTMLInputElement).blur()}
              style={{ width: '150px', background: 'rgba(15,23,42,0.7)', color: '#e2e8f0', border: '1px solid rgba(148,163,184,0.25)', borderRadius: '6px', fontSize: '11px', padding: '3px 5px' }}
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
export function SettingsPanel({ onClose }: { onClose: () => void }) {
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
        <SettingsIcon size={15} style={{ color: '#38bdf8' }} />
        <h2 style={{ fontSize: '13px', color: '#f1f5f9', margin: 0, flex: 1 }}>Settings</h2>
        <span style={{ fontSize: '11px', color: '#64748b' }}>{saving ? 'Saving…' : note}</span>
        <button
          onClick={onClose}
          style={{ background: 'transparent', border: 'none', color: '#94a3b8', cursor: 'pointer' }}
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
              color: '#64748b',
              marginBottom: '4px',
            }}
          >
            {GROUP_LABELS[group] ?? group}
          </div>

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
                  borderBottom: '1px solid rgba(148,163,184,0.1)',
                }}
              >
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: '12px', color: '#e2e8f0' }}>{row.label}</div>
                  {row.help && (
                    <div style={{ fontSize: '10px', color: '#64748b', lineHeight: 1.35 }}>
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

                {row.kind === 'choice' && (
                  <select
                    value={String(row.value)}
                    onChange={(e) => change(row.key, e.target.value)}
                    style={{
                      background: 'rgba(15,23,42,0.7)',
                      color: '#e2e8f0',
                      border: '1px solid rgba(148,163,184,0.25)',
                      borderRadius: '6px',
                      fontSize: '11px',
                      padding: '3px 5px',
                    }}
                  >
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
                      background: 'rgba(15,23,42,0.7)',
                      color: '#e2e8f0',
                      border: '1px solid rgba(148,163,184,0.25)',
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
