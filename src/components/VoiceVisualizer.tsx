import { useEffect, useRef } from 'react';
import { Sparkles } from 'lucide-react';

interface Props {
  isSpeaking: boolean;
  isThinking: boolean;
  isVoiceMuted: boolean;
  isListening: boolean;
}

// Synaptic AI node visualizer: a particle "brain" whose speed and colour track
// Dexter's state, plus the status badge. Owns its own canvas animation loop.
export function VoiceVisualizer({ isSpeaking, isThinking, isVoiceMuted, isListening }: Props) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (!canvasRef.current) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Match the backing store to the CSS size at device resolution - a fixed
    // buffer stretched by CSS renders blurry on any HiDPI screen.
    const dpr = window.devicePixelRatio || 1;
    const width = canvas.clientWidth || 380;
    const height = canvas.clientHeight || 180;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    let animFrame: number;
    const particles: { x: number; y: number; vx: number; vy: number; radius: number; color: string }[] = [];
    const particleCount = 40;

    for (let i = 0; i < particleCount; i++) {
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * 1.2,
        vy: (Math.random() - 0.5) * 1.2,
        radius: Math.random() * 3 + 2,
        color: i % 2 === 0 ? '#38bdf8' : '#a855f7',
      });
    }

    const render = () => {
      ctx.clearRect(0, 0, width, height);
      ctx.shadowBlur = 0;
      ctx.shadowColor = 'transparent';

      // Draw Synapses
      for (let i = 0; i < particleCount; i++) {
        for (let j = i + 1; j < particleCount; j++) {
          const dx = particles[i].x - particles[j].x;
          const dy = particles[i].y - particles[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);

          if (dist < 75) {
            ctx.beginPath();
            ctx.strokeStyle = isSpeaking
              ? `rgba(244, 114, 182, ${1 - dist / 75})`
              : isThinking
              ? `rgba(168, 85, 247, ${1 - dist / 75})`
              : `rgba(56, 189, 248, ${0.4 - dist / 180})`;
            ctx.lineWidth = isSpeaking ? 2.0 : 1.0;
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.stroke();
          }
        }
      }

      // Draw Particles
      particles.forEach((p) => {
        p.x += p.vx * (isSpeaking ? 2.5 : isThinking ? 1.8 : 1.0);
        p.y += p.vy * (isSpeaking ? 2.5 : isThinking ? 1.8 : 1.0);

        if (p.x < 0 || p.x > width) p.vx *= -1;
        if (p.y < 0 || p.y > height) p.vy *= -1;

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = isSpeaking ? '#f472b6' : isThinking ? '#a855f7' : p.color;
        ctx.shadowBlur = 12;
        ctx.shadowColor = p.color;
        ctx.fill();
        ctx.shadowBlur = 0;
        ctx.shadowColor = 'transparent';
      });

      animFrame = requestAnimationFrame(render);
    };

    render();
    return () => cancelAnimationFrame(animFrame);
  }, [isSpeaking, isThinking]);

  const badgeColor = isVoiceMuted
    ? '#f87171'
    : isSpeaking
    ? '#f472b6'
    : isThinking
    ? '#a855f7'
    : '#38bdf8';

  const badgeLabel = isVoiceMuted
    ? 'Voice Muted (Text Mode)'
    : isSpeaking
    ? 'Dexter Speaking...'
    : isThinking
    ? 'Processing Intelligence...'
    : isListening
    ? 'Continuous Listening...'
    : 'Ready';

  return (
    <div className="dexter-panel dexter-glow-border" style={{ padding: '16px', textAlign: 'center' }}>
      <div
        style={{
          fontSize: '11px',
          fontWeight: 800,
          color: '#a855f7',
          textTransform: 'uppercase',
          letterSpacing: '0.05em',
          marginBottom: '8px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '6px',
        }}
      >
        <Sparkles size={14} /> Synaptic AI Node Visualizer
      </div>

      <div
        style={{
          position: 'relative',
          width: '100%',
          height: '180px',
          margin: '0 auto',
          background: 'rgba(9, 13, 22, 0.8)',
          borderRadius: '16px',
          border: '1px solid rgba(56, 189, 248, 0.3)',
          overflow: 'hidden',
        }}
      >
        <canvas ref={canvasRef} style={{ width: '100%', height: '100%' }} />

        {/* Status Badge */}
        <div
          style={{
            position: 'absolute',
            bottom: '10px',
            left: '50%',
            transform: 'translateX(-50%)',
            background: 'rgba(15, 23, 42, 0.85)',
            padding: '4px 12px',
            borderRadius: '12px',
            border: '1px solid rgba(168, 85, 247, 0.4)',
            fontSize: '11px',
            color: badgeColor,
            fontWeight: 700,
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            whiteSpace: 'nowrap',
          }}
        >
          <span
            style={{
              width: '6px',
              height: '6px',
              borderRadius: '50%',
              background: badgeColor,
              flexShrink: 0,
            }}
          />
          {badgeLabel}
        </div>
      </div>
    </div>
  );
}
