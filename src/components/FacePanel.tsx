import { Cpu } from 'lucide-react';

// The ai-visualizer "face" (jaredrhod, AGPL) runs as a separate sidecar process
// on its own port and reads the .voice_state / .voice_waveform bus files that the
// backend writes from POST /api/dexter/voice-bus. We only embed it here, so its
// AGPL code never mixes into Dexter's own bundle. The sidecar's port lives in its
// own config (jaredRepo/ai-visualizer/ai-visualizer.json, default 8790); override
// the whole URL with VITE_VISUALIZER_URL if you move it.
const FACE_URL = import.meta.env?.VITE_VISUALIZER_URL || 'http://127.0.0.1:8790/';

// Display-only: the face reacts to the real voice state on its own. If the
// sidecar is not running the iframe just shows the browser's "can't connect"
// page, which is a fine, honest signal that the face is off.
export function FacePanel() {
  return (
    <div className="dexter-panel" style={{ padding: '16px' }}>
      <h3
        style={{
          fontSize: '13px',
          color: 'var(--accent)',
          marginBottom: '8px',
          display: 'flex',
          alignItems: 'center',
          gap: '6px',
        }}
      >
        <Cpu size={15} /> Face
      </h3>
      <div
        style={{
          position: 'relative',
          width: '100%',
          aspectRatio: '16 / 9',
          borderRadius: '8px',
          overflow: 'hidden',
          border: '1px solid var(--accent-border)',
          background: 'var(--surface-2)',
        }}
      >
        <iframe
          src={FACE_URL}
          title="Dexter face"
          style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', border: 'none' }}
          allow="microphone; autoplay"
        />
      </div>
      <div style={{ fontSize: '10px', color: 'var(--muted)', marginTop: '6px' }}>
        Runs the ai-visualizer sidecar. Start it with scripts/run_visualizer.bat.
      </div>
    </div>
  );
}
