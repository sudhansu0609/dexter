import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Mic, MicOff, Radio, Volume2, VolumeX, Sparkles, Play, Plus, CheckCircle, Cpu, Shield, Zap, Layers, RefreshCw, ExternalLink, Activity, Tv, Calendar, Terminal, Maximize2, Minimize2, X, FolderSearch, Search, FileText } from 'lucide-react';

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

interface FileResult {
  fileName: string;
  path: string;
  sizeBytes: number;
}

const API_BASE = 'http://localhost:8098';

export default function App() {
  // Desktop Window State
  const [isExpanded, setIsExpanded] = useState(false);

  // Voice & Input State
  const [inputMsg, setInputMsg] = useState('');
  const [chatLog, setChatLog] = useState<{ id: string; sender: 'user' | 'dexter'; text: string; action?: string }[]>([
    { id: 'w1', sender: 'dexter', text: 'Hello! I am Dexter, your AI super assistant. Speak your command hands-free or type a request.' }
  ]);
  
  // Hands-Free & Mute State
  const [isVoiceMuted, setIsVoiceMuted] = useState(false);
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

  // Desktop File Search State
  const [fileQuery, setFileQuery] = useState('');
  const [fileResults, setFileResults] = useState<FileResult[]>([]);
  const [isSearchingFiles, setIsSearchingFiles] = useState(false);

  // Refs
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const recognitionRef = useRef<any>(null);
  const chatEndRef = useRef<HTMLDivElement | null>(null);
  const isSpeakingRef = useRef(false);
  const isVoiceMutedRef = useRef(false);
  const isThinkingRef = useRef(false);
  const lastSpeechEndTimeRef = useRef<number>(0);
  const lastSpokenTextRef = useRef<string>('');

  useEffect(() => {
    isSpeakingRef.current = isSpeaking;
  }, [isSpeaking]);

  useEffect(() => {
    isThinkingRef.current = isThinking;
  }, [isThinking]);

  useEffect(() => {
    isVoiceMutedRef.current = isVoiceMuted;
    if (isVoiceMuted) {
      stopListening();
      if ('speechSynthesis' in window) window.speechSynthesis.cancel();
    } else {
      startListening();
    }
  }, [isVoiceMuted]);

  // Auto-scroll chat to bottom
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
    const particleCount = 40;

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

        if (p.x < 0 || p.x > canvas.width) p.vx *= -1;
        if (p.y < 0 || p.y > canvas.height) p.vy *= -1;

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

  const stopSpeaking = () => {
    if (audioRef.current) {
      try {
        audioRef.current.pause();
        audioRef.current.currentTime = 0;
      } catch (e) {}
    }
    if ('speechSynthesis' in window) {
      try { window.speechSynthesis.cancel(); } catch (e) {}
    }
    setIsSpeaking(false);
    isSpeakingRef.current = false;
    lastSpeechEndTimeRef.current = Date.now();
    setTimeout(() => {
      if (!isVoiceMutedRef.current && !isThinkingRef.current) {
        startListening();
      }
    }, 800);
  };

  // Continuous Hands-Free Speech Recognition Loop
  const initSpeechRecognition = () => {
    if ('webkitSpeechRecognition' in window || 'SpeechRecognition' in window) {
      const SpeechRecognition = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
      const rec = new SpeechRecognition();
      rec.continuous = true;
      rec.interimResults = true; // Enable interim results to detect 'stop' instantly
      rec.lang = 'en-US';

      rec.onresult = (e: any) => {
        const lastResult = e.results[e.results.length - 1];
        if (!lastResult) return;

        const rawTranscript = (lastResult[0]?.transcript || '').trim().toLowerCase();

        // ── Voice Interrupt Detection while Dexter is speaking ──
        if (isSpeakingRef.current) {
          const INTERRUPT_KEYWORDS = ["stop", "shut up", "quiet", "pause", "be quiet", "stop speaking", "stop talking", "cancel"];
          if (INTERRUPT_KEYWORDS.some(k => rawTranscript.includes(k))) {
            console.log('[Voice Interrupt] Stop keyword detected while speaking:', rawTranscript);
            stopSpeaking();
            return;
          }
          return;
        }

        if (isVoiceMutedRef.current || isThinkingRef.current) return;
        if (Date.now() - lastSpeechEndTimeRef.current < 1500) return;

        if (lastResult.isFinal) {
          const transcript = lastResult[0].transcript.trim();
          if (transcript.length < 3) return;

          // Self-Echo / Deduplication Filter
          const cleanTranscript = transcript.toLowerCase();
          const cleanLastSpoken = lastSpokenTextRef.current.toLowerCase();
          if (cleanLastSpoken && cleanLastSpoken.includes(cleanTranscript)) {
            console.log('[Mic Filter] Ignored self-echo transcript:', transcript);
            return;
          }

          handleSendMessage(transcript);
        }
      };

      rec.onerror = (err: any) => {
        // Silently handle error
      };

      rec.onend = () => {
        setIsListening(false);
        // Only restart if idle, not muted, not speaking, not thinking, and past cooldown
        if (!isVoiceMutedRef.current && !isSpeakingRef.current && !isThinkingRef.current && Date.now() - lastSpeechEndTimeRef.current >= 1500) {
          setTimeout(() => {
            startListening();
          }, 400);
        }
      };

      recognitionRef.current = rec;
      startListening();
    }
  };

  const startListening = () => {
    if (recognitionRef.current && !isVoiceMutedRef.current) {
      try {
        recognitionRef.current.start();
        setIsListening(true);
      } catch (e) {}
    }
  };

  const stopListening = () => {
    if (recognitionRef.current) {
      try {
        recognitionRef.current.stop();
        setIsListening(false);
      } catch (e) {}
    }
  };

  const toggleVoiceMute = () => {
    if (isSpeaking) {
      stopSpeaking();
    }
    setIsVoiceMuted(prev => !prev);
  };

  const toggleExpandWindow = () => {
    const nextState = !isExpanded;
    setIsExpanded(nextState);
    if ((window as any).electronAPI) {
      if (nextState) {
        (window as any).electronAPI.setExpanded();
      } else {
        (window as any).electronAPI.setCompact();
      }
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

    // Temporarily pause mic while processing & speaking
    stopListening();

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

      // Refresh all widgets
      fetchPlanner();
      fetchOvernightQueue();
      fetchSkills();

      // Speak response if voice is active
      if (!isVoiceMutedRef.current) {
        speakText(data.reply);
      } else {
        startListening();
      }
    } catch (err: any) {
      setIsThinking(false);
      setChatLog(prev => [...prev, { id: `err-${Date.now()}`, sender: 'dexter', text: 'Error connecting to Dexter Backend Server.' }]);
      startListening();
    }
  };

  const audioRef = useRef<HTMLAudioElement | null>(null);

  const speakText = async (text: string) => {
    if (isVoiceMutedRef.current) {
      startListening();
      return;
    }

    // Save spoken text ref for deduplication filtering
    lastSpokenTextRef.current = text;

    // 1. Immediately abort mic recognition & set speaking flags
    stopListening();
    if (recognitionRef.current) {
      try { recognitionRef.current.abort(); } catch (e) {}
    }

    setIsSpeaking(true);
    isSpeakingRef.current = true;

    try {
      // 2. Fetch Kokoro ONNX audio from backend endpoint
      const res = await fetch(`${API_BASE}/api/dexter/tts/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text, voiceKey: 'michael' })
      });

      if (!res.ok) throw new Error('TTS fetch failed');

      const blob = await res.blob();
      const audioUrl = URL.createObjectURL(blob);

      if (audioRef.current) {
        audioRef.current.pause();
      }

      const audio = new Audio(audioUrl);
      audioRef.current = audio;

      audio.onended = () => {
        setIsSpeaking(false);
        isSpeakingRef.current = false;
        lastSpeechEndTimeRef.current = Date.now();
        // Resume listening after 1.5s cooldown so mic doesn't catch lingering room echo
        setTimeout(() => {
          if (!isVoiceMutedRef.current && !isSpeakingRef.current && !isThinkingRef.current) {
            startListening();
          }
        }, 1500);
      };

      audio.onerror = () => {
        setIsSpeaking(false);
        isSpeakingRef.current = false;
        lastSpeechEndTimeRef.current = Date.now();
        if (!isVoiceMutedRef.current) startListening();
      };

      await audio.play();
    } catch (e) {
      console.warn('Backend Kokoro TTS playback error, falling back to browser synthesis', e);
      // Fallback
      if ('speechSynthesis' in window && !isVoiceMutedRef.current) {
        window.speechSynthesis.cancel();
        const utterance = new SpeechSynthesisUtterance(text.substring(0, 200));
        utterance.onend = () => {
          setIsSpeaking(false);
          isSpeakingRef.current = false;
          lastSpeechEndTimeRef.current = Date.now();
          setTimeout(() => { if (!isVoiceMutedRef.current) startListening(); }, 1500);
        };
        utterance.onerror = () => {
          setIsSpeaking(false);
          isSpeakingRef.current = false;
          lastSpeechEndTimeRef.current = Date.now();
          if (!isVoiceMutedRef.current) startListening();
        };
        window.speechSynthesis.speak(utterance);
      } else {
        setIsSpeaking(false);
        isSpeakingRef.current = false;
        lastSpeechEndTimeRef.current = Date.now();
        startListening();
      }
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

  const handleSearchFiles = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!fileQuery.trim()) return;
    setIsSearchingFiles(true);
    try {
      const res = await fetch(`${API_BASE}/api/dexter/filesystem/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ query: fileQuery })
      });
      const data = await res.json();
      setFileResults(data.results || []);
    } catch (e) {}
    setIsSearchingFiles(false);
  };

  const handleOpenFile = async (path: string) => {
    try {
      await fetch(`${API_BASE}/api/dexter/filesystem/open`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path })
      });
    } catch (e) {}
  };

  return (
    <div style={{ padding: '14px', maxWidth: isExpanded ? '1440px' : '460px', margin: '0 auto', transition: 'max-width 0.3s ease' }}>
      
      {/* Frameless Desktop Window Title Bar */}
      <header className="dexter-panel dexter-header" style={{ padding: '10px 16px', display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '14px', appRegion: 'drag' } as any}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '10px' }}>
          <div style={{ width: '32px', height: '32px', borderRadius: '10px', background: 'linear-gradient(135deg, #38bdf8 0%, #a855f7 100%)', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '18px' }}>
            🧠
          </div>
          <div>
            <h1 style={{ fontSize: '15px', color: '#f8fafc', margin: 0, display: 'flex', alignItems: 'center', gap: '6px' }}>
              DEXTER <span style={{ fontSize: '9px', background: 'rgba(56, 189, 248, 0.2)', color: '#38bdf8', padding: '1px 6px', borderRadius: '8px', border: '1px solid rgba(56, 189, 248, 0.4)' }}>DESKTOP AI</span>
            </h1>
          </div>
        </div>

        {/* Window Action Controls & Voice Mute */}
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', appRegion: 'no-drag' } as any}>
          
          {/* Dedicated Voice Mute Button */}
          <button
            type="button"
            onClick={toggleVoiceMute}
            style={{
              background: isVoiceMuted ? 'rgba(239, 68, 68, 0.2)' : 'rgba(16, 185, 129, 0.2)',
              color: isVoiceMuted ? '#f87171' : '#10b981',
              border: isVoiceMuted ? '1px solid rgba(239, 68, 68, 0.4)' : '1px solid rgba(16, 185, 129, 0.4)',
              padding: '6px 10px',
              borderRadius: '10px',
              fontSize: '11px',
              fontWeight: 700,
              display: 'flex',
              alignItems: 'center',
              gap: '4px'
            }}
            title={isVoiceMuted ? 'Unmute Voice' : 'Disable/Mute Voice'}
          >
            {isVoiceMuted ? <MicOff size={14} /> : <Radio className="brain-active-pulse" size={14} />}
            {isVoiceMuted ? 'Muted' : 'Hands-Free On'}
          </button>

          {/* Window Expand / Compact Toggle */}
          <button
            type="button"
            onClick={toggleExpandWindow}
            style={{ background: 'rgba(15, 23, 42, 0.6)', color: '#38bdf8', border: '1px solid var(--bg-card-border)', padding: '6px 8px', borderRadius: '8px' }}
            title={isExpanded ? 'Compact View' : 'Expand Full Dashboard'}
          >
            {isExpanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>

          {/* Electron Close Window */}
          {(window as any).electronAPI && (
            <button
              type="button"
              onClick={() => (window as any).electronAPI.close()}
              style={{ background: 'rgba(239, 68, 68, 0.2)', color: '#f87171', border: '1px solid rgba(239, 68, 68, 0.4)', padding: '6px 8px', borderRadius: '8px' }}
            >
              <X size={14} />
            </button>
          )}
        </div>
      </header>

      {/* Main Grid Layout (Compact by default, expanded when toggled) */}
      <div style={{ display: 'grid', gridTemplateColumns: isExpanded ? '340px 1fr 340px' : '1fr', gap: '16px' }}>
        
        {/* CENTER COLUMN: Synaptic Visualizer & Continuous Voice Workspace */}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '14px' }}>
          
          {/* Synaptic AI Node Visualizer Card (Primary Default UI) */}
          <div className="dexter-panel dexter-glow-border" style={{ padding: '16px', textAlign: 'center' }}>
            <div style={{ fontSize: '11px', fontWeight: 800, color: '#a855f7', textTransform: 'uppercase', letterSpacing: '0.05em', marginBottom: '8px', display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}>
              <Sparkles size={14} /> Synaptic AI Node Visualizer
            </div>
            
            <div style={{ position: 'relative', width: '100%', height: '180px', margin: '0 auto', background: 'rgba(9, 13, 22, 0.8)', borderRadius: '16px', border: '1px solid rgba(56, 189, 248, 0.3)', overflow: 'hidden' }}>
              <canvas ref={canvasRef} width={380} height={180} style={{ width: '100%', height: '100%' }} />
              
              {/* Status Badge */}
              <div style={{ position: 'absolute', bottom: '10px', left: '50%', transform: 'translateX(-50%)', background: 'rgba(15, 23, 42, 0.85)', padding: '4px 12px', borderRadius: '12px', border: '1px solid rgba(168, 85, 247, 0.4)', fontSize: '11px', color: isVoiceMuted ? '#f87171' : isSpeaking ? '#f472b6' : isThinking ? '#a855f7' : '#38bdf8', fontWeight: 700, display: 'flex', alignItems: 'center', gap: '6px', whiteSpace: 'nowrap' }}>
                <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: isVoiceMuted ? '#f87171' : isSpeaking ? '#f472b6' : isThinking ? '#a855f7' : '#38bdf8', flexShrink: 0 }} />
                {isVoiceMuted ? 'Voice Muted (Text Mode)' : isSpeaking ? 'Dexter Speaking...' : isThinking ? 'Processing Intelligence...' : isListening ? 'Continuous Listening...' : 'Ready'}
              </div>
            </div>
          </div>

          {/* Chat Feed */}
          <div className="dexter-panel" style={{ padding: '14px', height: isExpanded ? '460px' : '260px', display: 'flex', flexDirection: 'column' }}>
            <div style={{ flex: 1, overflowY: 'auto', display: 'flex', flexDirection: 'column', gap: '10px', paddingRight: '4px' }}>
              {chatLog.map(m => (
                <div key={m.id} style={{ display: 'flex', justifyContent: m.sender === 'user' ? 'flex-end' : 'flex-start' }}>
                  <div style={{
                    maxWidth: '85%',
                    padding: '10px 14px',
                    borderRadius: m.sender === 'user' ? '16px 16px 4px 16px' : '16px 16px 16px 4px',
                    background: m.sender === 'user' ? 'linear-gradient(135deg, #38bdf8 0%, #0284c7 100%)' : 'rgba(15, 23, 42, 0.8)',
                    color: '#ffffff',
                    border: m.sender === 'user' ? 'none' : '1px solid rgba(168, 85, 247, 0.3)',
                    fontSize: '12px',
                    lineHeight: 1.4
                  }}>
                    <div style={{ fontSize: '10px', fontWeight: 800, opacity: 0.8, marginBottom: '2px', display: 'flex', alignItems: 'center', gap: '4px' }}>
                      {m.sender === 'user' ? '👤 YOU' : '🧠 DEXTER AI'}
                    </div>
                    <div>{m.text}</div>
                  </div>
                </div>
              ))}
              {isThinking && (
                <div style={{ display: 'flex', alignItems: 'center', gap: '6px', color: '#a855f7', fontSize: '11px', fontWeight: 600, background: 'rgba(168, 85, 247, 0.1)', padding: '8px 12px', borderRadius: '10px', width: 'fit-content' }}>
                  <RefreshCw className="brain-active-pulse" size={12} />
                  <span>Dexter is reasoning & executing tools...</span>
                </div>
              )}
              <div ref={chatEndRef} />
            </div>

            {/* Prompt Input Control Bar */}
            <div style={{ display: 'flex', gap: '8px', alignItems: 'center', marginTop: '10px' }}>
              <input
                type="text"
                placeholder={isVoiceMuted ? 'Type command (Voice is muted)' : 'Speak hands-free or type here...'}
                value={inputMsg}
                onChange={(e) => setInputMsg(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && handleSendMessage()}
                style={{ flex: 1, borderRadius: '10px', fontSize: '12px', padding: '8px 12px' }}
              />
              <button
                type="button"
                onClick={() => handleSendMessage()}
                style={{
                  background: 'linear-gradient(135deg, #a855f7 0%, #38bdf8 100%)',
                  color: '#ffffff',
                  padding: '8px 14px',
                  borderRadius: '10px',
                  fontWeight: 800,
                  fontSize: '12px',
                  flexShrink: 0
                }}
              >
                Send
              </button>
            </div>
          </div>

          {/* Quick Desktop Controls Bar */}
          {!isExpanded && (
            <div style={{ display: 'flex', justifyContent: 'between', gap: '8px' }}>
              <button
                type="button"
                onClick={toggleExpandWindow}
                style={{ flex: 1, background: 'rgba(15, 23, 42, 0.6)', color: '#38bdf8', border: '1px solid var(--bg-card-border)', padding: '8px', borderRadius: '10px', fontSize: '11px', fontWeight: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: '6px' }}
              >
                <Maximize2 size={12} /> Open Full Day Planner & Curation Dashboard
              </button>
            </div>
          )}

        </div>

        {/* EXPANDED LEFT COLUMN: Day Planner & Local Desktop File Explorer (Visible when expanded) */}
        {isExpanded && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            
            {/* Day Planner Agenda Widget */}
            <div className="dexter-panel" style={{ padding: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '12px' }}>
                <h3 style={{ fontSize: '13px', color: '#38bdf8', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Calendar size={15} /> Day Planner Agenda
                </h3>
                <span style={{ fontSize: '10px', color: 'var(--text-muted)' }}>{tasks.length} Tasks</span>
              </div>

              <form onSubmit={handleAddTask} style={{ display: 'flex', gap: '6px', marginBottom: '12px' }}>
                <input
                  type="text"
                  placeholder="Add goal or task..."
                  value={newTaskTitle}
                  onChange={(e) => setNewTaskTitle(e.target.value)}
                  style={{ flex: 1, fontSize: '11px', padding: '6px 10px' }}
                />
                <button type="submit" style={{ background: '#38bdf8', color: '#090d16', padding: '6px 10px', borderRadius: '8px', fontWeight: 800, fontSize: '11px' }}>
                  <Plus size={12} />
                </button>
              </form>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '180px', overflowY: 'auto' }}>
                {tasks.map(t => (
                  <div
                    key={t.id}
                    onClick={() => handleToggleTask(t.id)}
                    style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '6px 10px', background: 'rgba(15, 23, 42, 0.6)', borderRadius: '8px', border: '1px solid var(--bg-card-border)', fontSize: '11px', cursor: 'pointer' }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px' }}>
                      <CheckCircle size={12} color={t.completed ? '#10b981' : '#64748b'} />
                      <span style={{ textDecoration: t.completed ? 'line-through' : 'none', color: t.completed ? '#64748b' : '#f8fafc' }}>
                        {t.title}
                      </span>
                    </div>
                    <span style={{ fontSize: '9px', color: '#94a3b8' }}>{t.time}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Desktop Local File Explorer Tool */}
            <div className="dexter-panel" style={{ padding: '16px' }}>
              <h3 style={{ fontSize: '13px', color: '#38bdf8', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <FolderSearch size={15} /> Local PC File Explorer
              </h3>

              <form onSubmit={handleSearchFiles} style={{ display: 'flex', gap: '6px', marginBottom: '10px' }}>
                <input
                  type="text"
                  placeholder="Search files on PC..."
                  value={fileQuery}
                  onChange={(e) => setFileQuery(e.target.value)}
                  style={{ flex: 1, fontSize: '11px', padding: '6px 10px' }}
                />
                <button type="submit" style={{ background: 'rgba(56, 189, 248, 0.2)', color: '#38bdf8', border: '1px solid #38bdf8', padding: '6px 10px', borderRadius: '8px', fontSize: '11px', fontWeight: 700 }}>
                  {isSearchingFiles ? '...' : <Search size={12} />}
                </button>
              </form>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '160px', overflowY: 'auto' }}>
                {fileResults.map((f, idx) => (
                  <div key={idx} onClick={() => handleOpenFile(f.path)} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: 'rgba(15, 23, 42, 0.6)', padding: '6px 8px', borderRadius: '8px', border: '1px solid var(--bg-card-border)', cursor: 'pointer', fontSize: '11px' }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: '6px', overflow: 'hidden' }}>
                      <FileText size={12} color="#38bdf8" />
                      <span style={{ whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{f.fileName}</span>
                    </div>
                    <ExternalLink size={10} color="#94a3b8" />
                  </div>
                ))}
              </div>
            </div>

          </div>
        )}

        {/* EXPANDED RIGHT COLUMN: spilledCoffeeAI Queue, App Launcher & Skills (Visible when expanded) */}
        {isExpanded && (
          <div style={{ display: 'flex', flexDirection: 'column', gap: '16px' }}>
            
            {/* spilledCoffeeAI Queue */}
            <div className="dexter-panel" style={{ padding: '16px' }}>
              <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '10px' }}>
                <h3 style={{ fontSize: '13px', color: '#f472b6', display: 'flex', alignItems: 'center', gap: '6px' }}>
                  <Tv size={15} /> spilledCoffeeAI Queue
                </h3>
                <button
                  type="button"
                  onClick={handleRunCurate}
                  disabled={isCurating}
                  style={{ background: 'rgba(244, 114, 182, 0.2)', color: '#f472b6', border: '1px solid rgba(244, 114, 182, 0.4)', padding: '4px 8px', borderRadius: '6px', fontSize: '10px', fontWeight: 700 }}
                >
                  {isCurating ? '...' : '⚡ Curate'}
                </button>
              </div>

              <div style={{ display: 'flex', flexDirection: 'column', gap: '8px', maxHeight: '180px', overflowY: 'auto' }}>
                {overnightItems.map(item => (
                  <div key={item.id} style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '8px', borderRadius: '8px', border: '1px solid rgba(244, 114, 182, 0.3)' }}>
                    <div style={{ fontSize: '11px', fontWeight: 800, color: '#f472b6' }}>{item.title}</div>
                    <div style={{ fontSize: '10px', color: '#e2e8f0', fontStyle: 'italic' }}>"{item.hook}"</div>
                  </div>
                ))}
              </div>
            </div>

            {/* Quick App Launcher */}
            <div className="dexter-panel" style={{ padding: '16px' }}>
              <h3 style={{ fontSize: '13px', color: '#38bdf8', marginBottom: '10px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Zap size={15} /> Quick OS App Launcher
              </h3>
              <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px' }}>
                {['Chrome', 'VSCode', 'Notepad', 'Calc', 'Spotify'].map(app => (
                  <button
                    key={app}
                    type="button"
                    onClick={() => handleLaunchApp(app)}
                    style={{ background: 'rgba(15, 23, 42, 0.6)', color: '#f8fafc', border: '1px solid var(--bg-card-border)', padding: '6px 8px', borderRadius: '8px', fontSize: '11px', fontWeight: 600, textAlign: 'left', display: 'flex', alignItems: 'center', gap: '4px' }}
                  >
                    <ExternalLink size={10} color="#38bdf8" /> {app}
                  </button>
                ))}
              </div>
            </div>

            {/* Self-Learning Skills */}
            <div className="dexter-panel" style={{ padding: '16px' }}>
              <h3 style={{ fontSize: '13px', color: '#a855f7', marginBottom: '8px', display: 'flex', alignItems: 'center', gap: '6px' }}>
                <Layers size={15} /> Self-Learned Skills ({skills.length})
              </h3>
              <div style={{ display: 'flex', flexDirection: 'column', gap: '6px', maxHeight: '140px', overflowY: 'auto' }}>
                {skills.map((s, idx) => (
                  <div key={idx} style={{ background: 'rgba(15, 23, 42, 0.6)', padding: '6px 8px', borderRadius: '8px', border: '1px solid rgba(168, 85, 247, 0.25)', fontSize: '10px' }}>
                    <div style={{ fontWeight: 700, color: '#a855f7' }}>⚡ {s.name}</div>
                  </div>
                ))}
              </div>
            </div>

          </div>
        )}

      </div>
    </div>
  );
}
