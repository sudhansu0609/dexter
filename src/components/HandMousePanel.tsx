import { useEffect, useState } from 'react';
import { Hand, Play, RotateCcw, Settings2, Square } from 'lucide-react';
import type { HandMouseStatus, HandPoseRow } from '../types';

interface Props {
  status: HandMouseStatus | null;
  busy: boolean;
  error?: string;
  onStart: (dryRun?: boolean) => void;
  onStop: () => void;
  onBind: (pose: string, action: string) => void;
  // v13.8: "can we control the speed of the hand mouse pointer?" - yes, and while
  // it is tracking, which is the only way to find the right number.
  onSpeed?: (speed: number) => void;
}

const btn = {
  background: 'var(--surface-2)',
  color: 'var(--text)',
  border: '1px solid var(--border)',
  borderRadius: '8px',
  fontSize: '11px',
  fontWeight: 600,
  padding: '4px 8px',
  display: 'flex',
  alignItems: 'center',
  gap: '4px',
} as const;

// What each action is called in one or two words. The backend sends its own
// labels; these are the short forms for the live line, where there is room for a
// phrase and not a sentence.
const SHORT: Record<string, string> = {
  point: 'moving the pointer',
  pinch: 'left button held',
  'right-pinch': 'right button held',
  middle: 'middle button held',
  'double-click': 'double clicked',
  scroll: 'scrolling',
  park: 'doing nothing, on purpose',
  none: 'nothing yet',
};

/** "Open palm → moving the pointer", or why not. */
export function liveLine(status: HandMouseStatus | null): string {
  if (!status) return 'Reading the camera…';
  if (!status.running) {
    if (!status.ready) return status.why || 'Not set up yet';
    return status.error ? `Off — ${status.error}` : 'Off';
  }
  if (!status.seeingHand) {
    // The number is the point: a palm score under the threshold is the room, the
    // distance or the pose — not the code. Without it, "it can't see my hand" has
    // no next step.
    const best = status.palmScore || status.bestScore;
    return best
      ? `Looking — best ${best.toFixed(2)} of ${status.palmNeeded.toFixed(2)} needed`
      : 'Looking for your hand';
  }
  const pose = status.poses.find((row) => row.pose === status.pose);
  const what = SHORT[status.action] || status.action;
  // Saying *why* the pointer is not moving matters: holding still through a click is
  // the v13.8 fix for "clicking is unstable", and without a word it looks like a
  // freeze.
  if (status.locked) return `${pose?.label || status.pose} → ${what}, holding still`;
  if (status.dragging) return `${pose?.label || status.pose} → ${what}, dragging`;
  return `${pose?.label || status.pose} → ${what}`;
}

function Dot({ on, warn }: { on: boolean; warn?: boolean }) {
  return (
    <span
      style={{
        width: '8px', height: '8px', borderRadius: '50%', display: 'inline-block',
        background: on ? (warn ? 'var(--warn)' : 'var(--success)') : 'var(--muted)',
      }}
    />
  );
}

/**
 * How far the pointer travels for a given hand movement (v13.8).
 *
 * Shown live and applied while tracking, because the only way to find the speed that
 * suits your desk is to move your hand and watch. The pointer is absolute, so this is
 * really the size of the rectangle of camera image that maps onto the screen - a
 * smaller one means less hand movement crosses the screen.
 */
function Speed({ status, onSpeed }: { status: HandMouseStatus; onSpeed: (n: number) => void }) {
  const [value, setValue] = useState(status.speed ?? 1);
  // Follow the backend when somebody changes it elsewhere (the settings panel, or
  // out loud), but never while this slider is being dragged.
  const [dragging, setDragging] = useState(false);
  useEffect(() => {
    if (!dragging && typeof status.speed === 'number') setValue(status.speed);
  }, [status.speed, dragging]);

  return (
    <div style={{ marginTop: '10px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', marginBottom: '2px' }}>
        <span style={{ color: 'var(--muted)', fontWeight: 600 }}>Pointer speed</span>
        <span style={{ color: 'var(--text)' }}>{value.toFixed(1)}×</span>
      </div>
      <input
        type="range"
        min={0.5}
        max={2.5}
        step={0.1}
        value={value}
        aria-label="Pointer speed"
        onChange={(e) => { setDragging(true); setValue(Number(e.target.value)); }}
        onPointerUp={() => { setDragging(false); onSpeed(value); }}
        onKeyUp={() => { setDragging(false); onSpeed(value); }}
        onBlur={() => setDragging(false)}
        style={{ width: '100%', accentColor: 'var(--accent)' }}
      />
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '9px', color: 'var(--muted)' }}>
        <span>slower, easier to aim</span>
        <span>faster</span>
      </div>
    </div>
  );
}

/** The palm score against the threshold it has to clear. */
function Confidence({ status }: { status: HandMouseStatus }) {
  const needed = status.palmNeeded || 0.5;
  const score = status.palmScore || 0;
  const pct = Math.min(100, Math.round((score / Math.max(needed * 2, 0.01)) * 100));
  return (
    <div style={{ marginTop: '8px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: '10px', marginBottom: '3px' }}>
        <span style={{ color: 'var(--muted)', fontWeight: 600 }}>Hand confidence</span>
        <span style={{ color: score >= needed ? 'var(--success)' : 'var(--muted)' }}>
          {score.toFixed(2)} / {needed.toFixed(2)}
        </span>
      </div>
      <div style={{ height: '5px', background: 'var(--surface-2)', borderRadius: '3px', position: 'relative' }}>
        <div
          style={{
            width: `${pct}%`, height: '100%', borderRadius: '3px',
            background: score >= needed ? 'var(--success)' : 'var(--warn)',
          }}
        />
        {/* Where the threshold sits, so the bar answers "close, or nowhere near?" */}
        <div
          style={{
            position: 'absolute', left: '50%', top: '-2px', width: '1px', height: '9px',
            background: 'var(--border)',
          }}
        />
      </div>
    </div>
  );
}

function GestureEditor({ status, onBind }: { status: HandMouseStatus; onBind: Props['onBind'] }) {
  return (
    <div style={{ marginTop: '10px', display: 'flex', flexDirection: 'column', gap: '5px' }}>
      {status.poses.map((row: HandPoseRow) => (
        <div
          key={row.pose}
          style={{
            display: 'flex', alignItems: 'center', gap: '8px',
            // The shape being held right now is highlighted: that is how you find
            // out which row you are editing without taking your hand down.
            background: row.pose === status.pose && status.running ? 'var(--surface-2)' : 'transparent',
            borderRadius: '7px', padding: '3px 4px',
          }}
        >
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ fontSize: '11px', fontWeight: 600, color: 'var(--text)' }}>
              {row.label}
              {row.action !== row.default && (
                <span style={{ color: 'var(--accent)', fontWeight: 500 }}> · changed</span>
              )}
            </div>
            <div style={{ fontSize: '10px', color: 'var(--muted)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {row.hint}
            </div>
          </div>
          <select
            value={row.action}
            aria-label={`What ${row.label} does`}
            onChange={(e) => onBind(row.pose, e.target.value)}
            style={{
              background: 'var(--surface)', color: 'var(--text)', border: '1px solid var(--border)',
              borderRadius: '7px', fontSize: '11px', padding: '3px 4px', maxWidth: '46%',
            }}
          >
            {status.actions.map((action) => (
              <option key={action.action} value={action.action}>
                {action.label}
              </option>
            ))}
          </select>
        </div>
      ))}
      <div style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '2px', lineHeight: 1.4 }}>
        Changes apply at once, even while tracking. You can also say “make a fist the
        right click”.
      </div>
    </div>
  );
}

/**
 * The hand mouse, on the dashboard (v13.6).
 *
 * Asked for directly: "where do I see the hand tracking on the dashboard" — and
 * until now, nowhere. It lived in a floating OpenCV window and a tool you had to
 * ask by name. This is the part a gesture mouse cannot do without: your hand is
 * busy, the pointer is the symptom rather than the evidence, and the difference
 * between "the camera can't see you" and "that shape is bound to do nothing" is
 * the whole diagnosis.
 */
export function HandMousePanel({ status, busy, error, onStart, onStop, onBind, onSpeed }: Props) {
  const [editing, setEditing] = useState(false);
  const running = !!status?.running;
  const blocked = !!status && !status.ready;

  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <h3
        style={{
          fontSize: '13px', color: 'var(--accent)', marginBottom: '10px',
          display: 'flex', alignItems: 'center', gap: '6px',
        }}
      >
        <Hand size={15} /> Hand mouse
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '6px' }}>
          {running && (
            <span style={{ fontSize: '10px', color: 'var(--muted)', fontWeight: 500 }}>
              {status?.fps?.toFixed(0)} fps
              {status?.camera != null ? ` · cam ${status.camera}` : ''}
            </span>
          )}
          <Dot on={running} warn={running && !status?.seeingHand} />
        </span>
      </h3>

      <div style={{ fontSize: '12px', color: 'var(--text)', fontWeight: 600, lineHeight: 1.4 }}>
        {liveLine(status)}
      </div>
      {status?.dryRun && running && (
        <div style={{ fontSize: '10px', color: 'var(--warn)', marginTop: '2px' }}>
          Rehearsal — the real pointer is not being moved.
        </div>
      )}
      {running && (
        <div style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '3px' }}>
          {status?.preview.open ? 'Tracking window open' : 'Tracking window closed'}
          {' · '}
          {status?.hotkey} stops it
          {status && status.frames > 0 && (
            <> · {Math.round((100 * status.handFrames) / Math.max(1, status.frames))}% of frames had a hand</>
          )}
        </div>
      )}
      {blocked && (
        <div style={{ fontSize: '10px', color: 'var(--warn)', marginTop: '4px', lineHeight: 1.4 }}>
          Say “set up hand tracking” and Dexter will fetch the two models (8 MB).
        </div>
      )}
      {!!error && (
        <div style={{ fontSize: '10px', color: 'var(--danger)', marginTop: '4px' }}>{error}</div>
      )}

      {running && status && <Confidence status={status} />}
      {status && onSpeed && <Speed status={status} onSpeed={onSpeed} />}

      <div style={{ display: 'flex', gap: '6px', marginTop: '10px', flexWrap: 'wrap' }}>
        {running ? (
          <button type="button" style={{ ...btn, borderColor: 'var(--danger)', color: 'var(--danger)' }}
                  disabled={busy} onClick={onStop}>
            <Square size={11} /> Stop
          </button>
        ) : (
          <>
            <button type="button" style={btn} disabled={busy || blocked} onClick={() => onStart(false)}>
              <Play size={11} /> Start
            </button>
            {/* Rehearsal first is the safe way to try a changed gesture: the poses
                are recognised and the real pointer is left alone. */}
            <button type="button" style={btn} disabled={busy || blocked} onClick={() => onStart(true)}>
              <RotateCcw size={11} /> Rehearse
            </button>
          </>
        )}
        <button
          type="button"
          style={{ ...btn, marginLeft: 'auto', color: editing ? 'var(--accent)' : 'var(--text)' }}
          onClick={() => setEditing((v) => !v)}
          aria-expanded={editing}
        >
          <Settings2 size={11} /> Gestures
        </button>
      </div>

      {editing && status && <GestureEditor status={status} onBind={onBind} />}
    </div>
  );
}
