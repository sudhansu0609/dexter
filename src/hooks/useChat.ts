import { useEffect, useRef, useState, type MutableRefObject } from 'react';
import { chatStream, getHistory } from '../api';
import { getLlmChoice } from './useLlmChoice';
import type { VoiceApi } from './useVoice';
import type { ChatMessage, StreamEvent } from '../types';

interface ChatDeps {
  // Called once a turn produces its authoritative reply, to refresh the widgets
  // whose data a tool call may have changed.
  onTurnComplete: () => void;
  // The voice layer (v10). Optional: a text-only shell works without it.
  voice?: VoiceApi;
  // Shared with the voice loop: whether a turn is in flight, and the send
  // handler the mic dispatches recognised commands through.
  isThinkingRef?: MutableRefObject<boolean>;
  commandHandlerRef?: MutableRefObject<(text: string) => void>;
}

export interface SendOptions {
  via?: 'voice' | 'text';
}

// Enough to re-establish where the conversation was, without the feed opening
// on a wall of yesterday.
const HISTORY_TURNS = 12;

const WELCOME: ChatMessage = {
  id: 'w1',
  sender: 'dexter',
  text: "Hey. I'm Dexter, and we're on this together. Tell me what we're working towards, or just say what's next.",
};

// Owns the chat feed and the streaming /chat/stream turn loop: live token
// rendering, tool-activity labels, provider-switch handling (roadmap Phase 6,
// items 9 + token-stream rendering), and - with a voice layer attached -
// feeding spoken chunks to it as they stream in (v10).
export function useChat({ onTurnComplete, voice, isThinkingRef, commandHandlerRef }: ChatDeps) {
  const [chatLog, setChatLog] = useState<ChatMessage[]>([WELCOME]);
  const [inputMsg, setInputMsg] = useState('');
  const [isThinking, setIsThinking] = useState(false);
  const [toolActivity, setToolActivity] = useState('');
  // Chat message currently receiving streamed LLM tokens, if any.
  const streamMsgIdRef = useRef<string | null>(null);

  // Keep the thinking flag mirrored where the voice loop reads it.
  useEffect(() => {
    if (isThinkingRef) isThinkingRef.current = isThinking;
    voice?.setThinking(isThinking);
  }, [isThinking, isThinkingRef, voice]);

  // Resume the thread on launch. The backend has always persisted these turns
  // and fed them to the model; the UI just could not read them back, so every
  // relaunch opened on a blank welcome while Dexter itself remembered the
  // conversation. Only replace the welcome when there is genuinely something to
  // restore - a first run should still be greeted.
  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        const { turns } = await getHistory(HISTORY_TURNS);
        if (cancelled || turns.length === 0) return;

        const restored: ChatMessage[] = turns
          .filter((turn) => turn.role === 'user' || turn.role === 'assistant')
          .map((turn, index) => ({
            id: `h-${index}`,
            sender: turn.role === 'user' ? 'user' : 'dexter',
            text: turn.content,
          }));

        if (restored.length > 0) setChatLog(restored);
      } catch {
        // No history is not an error - keep the welcome and carry on.
      }
    })();

    return () => {
      cancelled = true;
    };
  }, []);

  const pushDexterMessage = (text: string, action?: string, extra?: { kind?: string; quickReplies?: string[] }) => {
    setChatLog((prev) => [...prev, { id: `d-${Date.now()}`, sender: 'dexter', text, action, ...(extra || {}) }]);
  };

  const handleSendMessage = async (customText?: string, options: SendOptions = {}) => {
    const textToSend = customText || inputMsg;
    if (!textToSend.trim()) return;
    const via = options.via || 'text';

    const userMsg: ChatMessage = { id: `u-${Date.now()}`, sender: 'user', text: textToSend, via };
    setChatLog((prev) => [...prev, userMsg]);
    setInputMsg('');
    setIsThinking(true);
    setToolActivity('');
    streamMsgIdRef.current = null;

    // Voice on means Dexter talks back; a reply to something spoken is shaped
    // to be heard (short, no lists) whether or not it is voiced.
    const speak = Boolean(voice?.canSpeak());
    let gotAudio = false;
    let replyText = '';

    try {
      // Streaming endpoint: tokens land as they are generated. The user's picked
      // provider/model (or 'auto') rides along on every turn.
      const choice = getLlmChoice();
      const res = await chatStream(textToSend, choice.provider, choice.model, { speak, spoken: via === 'voice' });

      if (!res.ok || !res.body) throw new Error(`Chat failed: ${res.status}`);

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        // SSE frames are separated by a blank line.
        const frames = buffer.split('\n\n');
        buffer = frames.pop() || '';

        for (const frame of frames) {
          const dataLine = frame.split('\n').find((l) => l.startsWith('data:'));
          if (!dataLine) continue;

          let evt: StreamEvent;
          try {
            evt = JSON.parse(dataLine.slice(5).trim());
          } catch {
            continue;
          }

          if (evt.type === 'tool_start') {
            setToolActivity(String(evt.payload?.name || '').replace(/_/g, ' '));
          } else if (evt.type === 'token') {
            // Live LLM output - render it as it is generated.
            setIsThinking(false);
            const piece = String(evt.payload?.text || '');
            if (!streamMsgIdRef.current) {
              const newId = `d-${Date.now()}`;
              streamMsgIdRef.current = newId;
              setChatLog((prev) => [...prev, { id: newId, sender: 'dexter', text: piece }]);
            } else {
              const sid = streamMsgIdRef.current;
              setChatLog((prev) => prev.map((m) => (m.id === sid ? { ...m, text: m.text + piece } : m)));
            }
          } else if (evt.type === 'escalated') {
            // The small model handed this turn to the bigger, tool-enabled one (v8).
            setToolActivity('bringing in the bigger model');
          } else if (evt.type === 'provider_switched') {
            // The reply restarts on a fallback provider; clear the partial text.
            const sid = streamMsgIdRef.current;
            if (sid) setChatLog((prev) => prev.map((m) => (m.id === sid ? { ...m, text: '' } : m)));
            setToolActivity(`retrying on ${evt.payload?.to || 'fallback'}`);
          } else if (evt.type === 'audio' && speak) {
            // A spoken sentence, ready while the rest is still being written.
            gotAudio = true;
            voice?.enqueueAudio(String(evt.payload?.url || ''));
          } else if (evt.type === 'interrupted') {
            // The user cut in. Keep whatever was rendered; drop the spinner.
            setIsThinking(false);
            setToolActivity('');
            streamMsgIdRef.current = null;
          } else if (evt.type === 'reply') {
            setIsThinking(false);
            setToolActivity('');
            replyText = String(evt.payload?.reply || '');
            const sid = streamMsgIdRef.current;
            streamMsgIdRef.current = null;
            if (sid) {
              // Finalize the streamed message with the authoritative reply.
              setChatLog((prev) =>
                prev.map((m) =>
                  m.id === sid ? { ...m, text: evt.payload.reply, action: evt.payload.action, quickReplies: evt.payload.details?.quickReplies } : m,
                ),
              );
            } else {
              setChatLog((prev) => [
                ...prev,
                { id: `d-${Date.now()}`, sender: 'dexter', text: evt.payload.reply, action: evt.payload.action, quickReplies: evt.payload.details?.quickReplies },
              ]);
            }
            onTurnComplete();
          } else if (evt.type === 'error') {
            setChatLog((prev) => [
              ...prev,
              {
                id: `err-${Date.now()}`,
                sender: 'dexter',
                text: `Something went wrong: ${evt.payload?.message || 'unknown error'}`,
              },
            ]);
          }
        }
      }

      setIsThinking(false);
      setToolActivity('');
      // Nothing was voiced (voice off, or a reply with no speakable text):
      // still open the follow-up window so the next sentence needs no wake word.
      if (via === 'voice' || speak) voice?.replyLanded(gotAudio, replyText);
    } catch (err) {
      console.warn('Chat request failed', err);
      setIsThinking(false);
      setToolActivity('');
      setChatLog((prev) => [
        ...prev,
        { id: `err-${Date.now()}`, sender: 'dexter', text: 'Error connecting to Dexter Backend Server.' },
      ]);
    }
  };

  // The mic is registered once on mount but must always dispatch to the current
  // render's send closure; keep the shared handler ref pointed at it.
  useEffect(() => {
    if (commandHandlerRef) commandHandlerRef.current = (text: string) => handleSendMessage(text, { via: 'voice' });
  });

  return {
    chatLog,
    inputMsg,
    setInputMsg,
    isThinking,
    toolActivity,
    handleSendMessage,
    pushDexterMessage,
  };
}
