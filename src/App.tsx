import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Mic, Radio, Volume2, VolumeX, Sparkles, Play, Plus, CheckCircle, Cpu, Shield, Zap, Layers, RefreshCw, ExternalLink, Activity, Tv, Calendar, Terminal, Globe } from 'lucide-react';

interface Task {
  id: string;
  time: string;
  title: string;
  completed: boolean;
}

interface OverNightItem {
  id: string;
  timestamp: string;
  title: string;
  hook: string;
  scriptOutline: string[];
  targetAudience: string;
  ctrScore: string;
}

interface SkillItem {
  name: string;
  description: string;
  triggers: string[];
  steps: string[];
  learnedAt: string;
}

const API_BASE = 'http://localhost:8098';

export default function App() {
  // Voice & Input State
  const [inputMsg, setInputMsg] = useState('');
  const [chatLog, setChatLog] = useState<{ id: string; sender: 'user' | 'dexter'; text: string; action?: string }[]>([
    { id: 'w1', sender: 'dexter', text: 'Hello! I am Dexter, your personal AI super assistant. How can I help you plan your day, curate video content, or launch apps?' }
  ]);
  const [isListening, setIsListening] = useState(false);
  const [isSpeaking, setIsSpeaking] = useState(false);
  const [isThinking, setIsThinking] = useState(false);
  
  // Model Provider Selection
  const [provider, setProvider] = useState<'auto' | 'lmstudio' | 'gemini' | 'openai' | 'ollama'>('auto');
  const [modelsStatus, setModelsStatus] = useState<any>(null);
  
  // Widgets Data
  const [tasks, setTasks] = useState<Task[]>([]);
  const [newTaskTitle, setNewTaskTitle] = useState('');
  const [overnightItems, setOvernightItems] = useState<OverNightItem[]>([]);
  const [skills, setSkills] = useState<SkillItem[]>([]);
  const [isCurating, setIsCurating] = useState(false);

  // Refs
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const recognitionRef = useRef<any>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);

  // Auto-scroll chat to bottom when messages change
  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatLog, isThinking]);

  useEffect(() => {
    fetchStatus();
    fetchPlanner();
    fetchOvernightQueue();
    fetchSkills();
    initSpeechRecognition();
  }, []);

  // Futuristic Canvas Particle Brain Animation Loop
  useEffect(() => {
    if (!canvasRef.current) return;
    const canvas = canvasRef.current;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animFrame: number;
    const particles: { x: number; y: number; vx: number; vy: number; radius: number; color: string }[] = [];
    const particleCount = 35;

    for (let i = 0; i < particleCount; i++) {
      particles.push({
        x: Math.random() * canvas.width,
        y: Math.random() * canvas.height,
        vx: (Math.random() - 0.5) * 1.2,
        vy: (Math.random() - 0.5) * 1.2,
        radius: Math.random() * 3 + 2,
        color: i % 2 === 0 ? '#38bdf8' : '#a855f7'
      });
    }

    const render = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      // Reset shadow state before drawing synapses
      ctx.shadowBlur = 0;
      ctx.shadowColor = 'transparent';

      // Draw Neural Connections (Synapses)
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
            ctx.lineWidth = isSpeaking ? 1.8 : 1.0;
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.stroke();
          }
        }
      }

      // Draw Particles (Brain Nodes)
      particles.forEach((p) => {
        p.x += p.vx * (isSpeaking ? 2.5 : isThinking ? 1.8 : 1.0);
        p.y += p.vy * (isSpeaking ? 2.5 : isThinking ? 1.8 : 1.0);

        if (p.x < 0 || p.x > canvas.width) p.vx *= -1;
        if (p.y < 0 || p.y > canvas.height) p.vy *= -1;

        ctx.beginPath();
        ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2);
        ctx.fillStyle = isSpeaking ? '#f472b6' : isThinking ? '#a855f7' : p.color;
        ctx.shadowBlur = 12;
        ctx.shadowColor = p.color;
        ctx.fill();

        // Reset shadow after each particle so it doesn't bleed
        ctx.shadowBlur = 0;
        ctx.shadowColor = 'transparent';
      });

      animFrame = requestAnimationFrame(render);
    };

    render();
    return () => cancelAnimationFrame(animFrame);
  }, [isSpeaking, isThinking]);

  const initSpeechRecognition = () => {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      const rec = new SpeechRecognition();
      rec.continuous = true;
      rec.interimResults = true;
      rec.lang = 'en-US';

      rec.onresult = (e: any) => {
        // Only take the LATEST result to avoid text duplication from interim results
        const lastResult = e.results[e.results.length - 1];
        const transcript = lastResult[0].transcript;
        if (transcript.trim()) {
          setInputMsg(transcript.trim());
        }
      };

      rec.onerror = (err: any) => console.log('Mic Error:', err);
      rec.onend = () => setIsListening(false);
      recognitionRef.current = rec;
    }
  };

  const toggleMic = () => {
    if (!recognitionRef.current) {
      alert('Speech Recognition is not supported in this browser. Please use Chrome/Edge.');
      return;
    }
    if (isListening) {
      recognitionRef.current.stop();
      setIsListening(false);
    } else {
      recognitionRef.current.start();
      setIsListening(true);
    }
  };

  const fetchStatus = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/dexter/status`);
      const data = await res.json();
      setModelsStatus(data);
    } catch (e) {}
  };

  const fetchPlanner = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/dexter/planner`);
      const data = await res.json();
      setTasks(data.tasks || []);
    } catch (e) {}
  };

  const fetchOvernightQueue = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/dexter/spilledcoffee/overnight-queue`);
      const data = await res.json();
      setOvernightItems(data);
    } catch (e) {}
  };

  const fetchSkills = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/dexter/skills`);
      const data = await res.json();
      setSkills(data);
    } catch (e) {}
  };

  const handleSendMessage = async (customText?: string) => {
    const textToSend = customText || inputMsg;
    if (!textToSend.trim()) return;

    const userMsg = { id: `u-${Date.now()}`, sender: 'user' as const, text: textToSend };
    setChatLog(prev => [...prev, userMsg]);
    setInputMsg('');
    setIsThinking(true);

    try {
      const res = await fetch(`${API_BASE}/api/dexter/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: textToSend, provider })
      });
      const data = await res.json();

      setIsThinking(false);
      const dexterMsg = { id: `d-${Date.now()}`, sender: 'dexter' as const, text: data.reply, action: data.action };
      setChatLog(prev => [...prev, dexterMsg]);

      // Refresh all widgets after action
      fetchPlanner();
      fetchOvernightQueue();
      fetchSkills();

      // Speak response
      speakText(data.reply);
    } catch (err: any) {
      setIsThinking(false);
      setChatLog(prev => [...prev, { id: `err-${Date.now()}`, sender: 'dexter', text: 'Error connecting to Dexter Backend Server.' }]);
    }
  };

  const speakText = (text: string) => {
    if ('speechSynthesis' in window) {
      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text.substring(0, 200));
      utterance.onstart = () => setIsSpeaking(true);
      utterance.onend = () => setIsSpeaking(false);
      utterance.onerror = () => setIsSpeaking(false);
      window.speechSynthesis.speak(utterance);
    }
  };

  const handleAddTask = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newTaskTitle.trim()) return;
    try {
      await fetch(`${API_BASE}/api/dexter/planner/add`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: newTaskTitle })
      });
      setNewTaskTitle('');
      fetchPlanner();
    } catch (e) {}
  };

  const handleToggleTask = async (taskId: string) => {
    try {
      await fetch(`${API_BASE}/api/dexter/planner/toggle/${taskId}`, { method: 'POST' });
      fetchPlanner();
    } catch (e) {}
  };

  const handleRunCurate = async () => {
    setIsCurating(true);
    try {
      await fetch(`${API_BASE}/api/dexter/spilledcoffee/curate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ niche: 'AI Tech & Viral Trends', count: 3 })
      });
      fetchOvernightQueue();
    } catch (e) {
    } finally {
      setIsCurating(false);
    }
  };

  const handleLaunchApp = async (appName: string) => {
    try {
      await fetch(`${API_BASE}/api/dexter/system/launch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ appName })
      });
      setChatLog(prev => [...prev, { id: `app-${Date.now()}`, sender: 'dexter', text: `Launched desktop application '${appName}'` }]);
    } catch (e) {}
  };

  return (
    <div style={{ padding: '20px', maxWidth: '1440px', margin: '0 auto' }}>
      
      {/* Top Header Navbar */}
      <header className="dexter-panel dexter-header" style={{ padding: '14px 24px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '20px', flexWrap: 'wrap', gap: '12px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '14px' }}>
          <div style={{ width: '42px', height: '42px', borderRadius: '12px', background: 'linear-gradient(135deg, #38bdf8 0%, #a855f7 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '22px', flexShrink: 0 }}>
            🧠
          </div>
          <div>
            <h1 style={{ fontSize: '20px', color: '#f8fafc', margin: 0 }}>DEXTER <span style={{ fontSize: '11px', background: 'rgba(56, 189, 248, 0.2)', color: '#38bdf8', padding: '2px 8px', borderRadius: '12px', border: '1px solid rgba(56, 189, 248, 0.4)' }}>SUPER ASSISTANT</span></h1>
            <p style={{ fontSize: '12px', color: 'var(--text-muted)', margin: 0 }}>Autonomous Day Planning • spilledCoffeeAi Automation • Self-Improving AI Skills</p>
          </div>
        </div>

        {/* LLM Provider Switcher & Status */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'rgba(15, 23, 42, 0.6)', padding: '6px 12px', borderRadius: '12px', border: '1px solid var(--bg-card-border)', fontSize: '12px' }}>
            <Cpu size={16} color="#38bdf8" />
            <span style={{ color: 'var(--text-muted)' }}>LLM Engine:</span>
            <select
              value={provider}
              onChange={(e: any) => setProvider(e.target.value)}
              style={{ background: '#0f172a', border: 'none', color: '#38bdf8', fontWeight: 700, cursor: 'pointer', padding: '2px 4px', borderRadius: '6px' }}
            >
              <option value="auto">Auto (Local / Gemini)</option>
              <option value="lmstudio">Local LM Studio</option>
              <option value="gemini">Google Gemini API</option>
              <option value="openai">OpenAI GPT-4o</option>
              <option value="ollama">Ollama GGUF</option>
            </select>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: '6px', background: 'rgba(16, 185, 129, 0.15)', color: '#10b981', border: '1px solid rgba(16, 185, 129, 0.3)', padding: '6px 12px', borderRadius: '12px', fontSize: '12px', fontWeight: 700 }}>
            <Activity size={14} /> System Active
          </div>
        </div>
      </header>

      {/* Main Grid Layout — responsive: 3-col on desktop, stacked on mobile */}
      <div className="dexter-main-grid">
        
        {/* LEFT COLUMN: Futuristic Animated Brain Visualizer & Day Planner */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          {/* Futuristic Animated Particle Brain Node Card */}
          <div className="dexter-panel dexter-glow-border" style={{ padding: '20px', textAlign: 'center' }}>
            <div style={{ fontSize: '12px', fontWeight: 800, color: '#a855f7', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '10px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}>
              <Sparkles size={14} /> Synaptic AI Node Visualizer
            </div>
            
            <div style={{ position: 'relative', width: '100%', maxWidth: '320px', height: '180px', margin: '0 auto', background: 'rgba(9, 13, 22, 0.8)', borderRadius: '16px', border: '1px solid rgba(56, 189, 248, 0.3)', overflow: 'hidden' }}>
              <canvas ref={canvasRef} width={320} height={180} style={{ width: '100%', height: '100%' }} />
              
              {/* Status Badge */}
              <div style={{ position: 'absolute', bottom: '10px', left: '50%', transform: 'translateX(-50%)', background: 'rgba(15, 23, 42, 0.85)', padding: '4px 12px', borderRadius: '12px', border: '1px solid rgba(168, 85, 247, 0.4)', fontSize: '11px', color: isSpeaking ? '#f472b6' : isThinking ? '#a855f7' : '#38bdf8', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '6px', whiteSpace: 'nowrap' }}>
                <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: isSpeaking ? '#f472b6' : isThinking ? '#a855f7' : '#38bdf8', flexShrink: 0 }} />
                {isSpeaking ? 'Dexter Speaking...' : isThinking ? 'Processing Intelligence...' : 'Listening & Ready'}
              </div>
            </div>
          </div>

          {/* Day Planner & Task Agenda Widget */}
          <div className="dexter-panel" style={{ padding: '20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px' }}>
              <h3 style={{ fontSize: '14px', color: '#38bdf8', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Calendar size={16} /> Day Planner Agenda
              </h3>
              <span style={{ fontSize: '11px', color: 'var(--text-muted)' }}>{tasks.length} Tasks</span>
            </div>

            <form onSubmit={handleAddTask} style={{ display: 'flex', gap: '8px', marginBottom: '14px' }}>
              <input
                type="text"
                placeholder="Add daily goal or task..."
                value={newTaskTitle}
                onChange={(e) => setNewTaskTitle(e.target.value)}
                style={{ flex: 1, fontSize: '12px', padding: '8px 12px' }}
              />
              <button type="submit" style={{ background: '#38bdf8', color: '#090d16', padding: '8px 12px', borderRadius: '10px', fontWeight: 800, fontSize: '12px' }}>
                <Plus size={14} />
              </button>
            </form>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '200px', overflowY: 'auto' }}>
              {tasks.map(t => (
                <div
                  key={t.id}
                  onClick={() => handleToggleTask(t.id)}
                  style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', background: 'rgba(15, 23, 42, 0.6)', borderRadius: '10px', border: '1px solid var(--bg-card-border)', fontSize: '12px', cursor: 'pointer', transition: 'all 0.15s ease' }}
                >
                  <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                    <CheckCircle size={14} color={t.completed ? '#10b981' : '#64748b'} />
                    <span style={{ textDecoration: t.completed ? 'line-through' : 'none', color: t.completed ? '#64748b' : '#f8fafc' }}>
                      {t.title}
                    </span>
                  </div>
                  <span style={{ fontSize: '10px', color: '#94a3b8' }}>{t.time}</span>
                </div>
              ))}
            </div>
          </div>

        </div>

        {/* MIDDLE COLUMN: Interactive Dexter Voice Workspace & Chat Feed */}
        <div className="dexter-panel dexter-chat-col" style={{ padding: '20px', display: 'flex', flexDirection: 'column' }}>
          
          {/* Chat Feed */}
          <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '14px', marginBottom: '16px', paddingRight: '6px' }}>
            {chatLog.map(m => (
              <div key={m.id} style={{ display: 'flex', justifyContent: m.sender === 'user' ? 'flex-end' : 'flex-start' }}>
                <div style={{
                  maxWidth: '82%',
                  padding: '12px 16px',
                  borderRadius: m.sender === 'user' ? '18px 18px 4px 18px' : '18px 18px 18px 4px',
                  background: m.sender === 'user' ? 'linear-gradient(135deg, #38bdf8 0%, #0284c7 100%)' : 'rgba(15, 23, 42, 0.8)',
                  color: '#ffffff',
                  border: m.sender === 'user' ? 'none' : '1px solid rgba(168, 85, 247, 0.3)',
                  boxShadow: '0 4px 14px rgba(0,0,0,0.2)',
                  fontSize: '13px',
                  lineHeight: 1.5
                }}>
                  <div style={{ fontSize: '11px', fontWeight: 800, opacity: 0.8, marginBottom: '4px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                    {m.sender === 'user' ? '👤 YOU' : '🧠 DEXTER AI'}
                  </div>
                  <div>{m.text}</div>
                </div>
              </div>
            ))}
            {isThinking && (
              <div style={{ display: 'flex', alignItems: 'center', gap: '8px', color: '#a855f7', fontSize: '12px', fontWeight: 600, background: 'rgba(168, 85, 247, 0.1)', padding: '10px 14px', borderRadius: '12px', width: 'fit-content' }}>
                <RefreshCw className="brain-active-pulse" size={14} />
                <span>Dexter is reasoning & executing tools...</span>
              </div>
            )}
            {/* Scroll anchor */}
            <div ref={chatEndRef} />
          </div>

          {/* Voice Input Controls Bar */}
          <div style={{ display: 'flex', gap: '10px', alignItems: 'center', background: 'rgba(15, 23, 42, 0.8)', padding: '12px', borderRadius: '16px', border: '1px solid var(--bg-card-border)', flexWrap: 'wrap' }}>
            <button
              type="button"
              onClick={toggleMic}
              style={{
                background: isListening ? 'rgba(244, 114, 182, 0.25)' : 'rgba(56, 189, 248, 0.2)',
                color: isListening ? '#f472b6' : '#38bdf8',
                border: isListening ? '1px solid #f472b6' : '1px solid #38bdf8',
                padding: '12px',
                borderRadius: '12px',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0
              }}
              title="Continuous Voice Mic Command"
            >
              {isListening ? <Radio className="brain-active-pulse" size={20} /> : <Mic size={20} />}
            </button>

            <input
              type="text"
              placeholder={isListening ? 'Listening... Speak your command to Dexter' : 'Type prompt (e.g., "Plan my day", "Play lo-fi on YouTube", "Open Chrome")'}
              value={inputMsg}
              onChange={(e) => setInputMsg(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && handleSendMessage()}
              style={{ flex: 1, borderRadius: '12px', minWidth: '180px' }}
            />

            <button
              type="button"
              onClick={() => handleSendMessage()}
              style={{
                background: 'linear-gradient(135deg, #a855f7 0%, #38bdf8 100%)',
                color: '#ffffff',
                border: 'none',
                padding: '12px 20px',
                borderRadius: '12px',
                fontWeight: 800,
                fontSize: '13px',
                flexShrink: 0
              }}
            >
              Ask Dexter
            </button>
          </div>

        </div>

        {/* RIGHT COLUMN: spilledCoffeeAI Curation & Self-Learned Skills */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '20px' }}>
          
          {/* spilledCoffeeAI Overnight Content Curation Card */}
          <div className="dexter-panel" style={{ padding: '20px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
              <h3 style={{ fontSize: '14px', color: '#f472b6', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Tv size={16} /> spilledCoffeeAI Queue
              </h3>
              <button
                type="button"
                onClick={handleRunCurate}
                disabled={isCurating}
                style={{ background: 'rgba(244, 114, 182, 0.2)', color: '#f472b6', border: '1px solid rgba(244, 114, 182, 0.4)', padding: '4px 10px', borderRadius: '8px', fontSize: '11px', fontWeight: 700, opacity: isCurating ? 0.6 : 1 }}
              >
                {isCurating ? 'Curating...' : 'Curate Titles'}
              </button>
            </div>

            <p style={{ fontSize: '11px', color: 'var(--text-muted)', marginBottom: '12px' }}>
              Overnight viral video titles and script outlines ready for review:
            </p>

            <div style={{ display: 'flex', flexDirection: 'column', gap: '10px', maxHeight: '200px', overflowY: 'auto' }}>
              {overnightItems.length === 0 ? (
                <div style={{ padding: '12px', background: 'rgba(15, 23, 42, 0.4)', borderRadius: '10px', border: '1px dashed rgba(244, 114, 182, 0.3)', textAlign: 'center', color: '#64748b', fontSize: '11px' }}>
                  No curated content yet. Click <strong>Curate Titles</strong> to generate.
                </div>
              ) : overnightItems.map(item => (
                <div key={item.id} style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '10px', borderRadius: '10px', border: '1px solid rgba(244, 114, 182, 0.3)' }}>
                  <div style={{ fontSize: '12px', fontWeight: 800, color: '#f472b6', marginBottom: '4px' }}>
                    {item.title}
                  </div>
                  <div style={{ fontSize: '11px', color: '#e2e8f0', marginBottom: '4px', fontStyle: 'italic' }}>
                    "{item.hook}"
                  </div>
                  <div style={{ fontSize: '10px', color: '#94a3b8' }}>
                    Target: {item.targetAudience} • CTR Potential: <strong style={{ color: '#10b981' }}>{item.ctrScore}</strong>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Quick App Launcher & Tools */}
          <div className="dexter-panel" style={{ padding: '20px' }}>
            <h3 style={{ fontSize: '14px', color: '#38bdf8', marginBottom: '12px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Zap size={16} /> Quick OS App Launcher
            </h3>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '8px' }}>
              {['Chrome', 'VSCode', 'Notepad', 'Calc', 'Spotify'].map(app => (
                <button
                  key={app}
                  type="button"
                  onClick={() => handleLaunchApp(app)}
                  style={{ background: 'rgba(15, 23, 42, 0.6)', color: '#f8fafc', border: '1px solid var(--bg-card-border)', padding: '8px 10px', borderRadius: '10px', fontSize: '12px', fontWeight: 600, textAlign: 'left', display: 'flex', alignItems: 'center', gap: '6px' }}
                >
                  <ExternalLink size={12} color="#38bdf8" /> {app}
                </button>
              ))}
            </div>
          </div>

          {/* Self-Learning Skills Registry */}
          <div className="dexter-panel" style={{ padding: '20px' }}>
            <h3 style={{ fontSize: '14px', color: '#a855f7', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
              <Layers size={16} /> Self-Learned Skills ({skills.length})
            </h3>
            <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '160px', overflowY: 'auto' }}>
              {skills.length === 0 ? (
                <div style={{ padding: '12px', background: 'rgba(15, 23, 42, 0.4)', borderRadius: '10px', border: '1px dashed rgba(168, 85, 247, 0.3)', textAlign: 'center', color: '#64748b', fontSize: '11px' }}>
                  No skills learned yet. Dexter learns automatically from complex tasks.
                </div>
              ) : skills.map((s, idx) => (
                <div key={idx} style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '8px 10px', borderRadius: '10px', border: '1px solid rgba(168, 85, 247, 0.25)', fontSize: '11px' }}>
                  <div style={{ fontWeight: 700, color: '#a855f7' }}>{s.name}</div>
                  <div style={{ color: '#94a3b8', fontSize: '10px' }}>{s.description}</div>
                </div>
              ))}
            </div>
          </div>

        </div>

      </div>
    </div>
  );
}
