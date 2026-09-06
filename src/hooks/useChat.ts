import { useEffect, useRef, useState } from 'react';
import { chatStream, getHistory } from '../api';
import { getLlmChoice } from './useLlmChoice';
import type { ChatMessage, StreamEvent } from '../types';

interface ChatDeps {
  // Called once a turn produces its authoritative reply, to refresh the widgets
  // whose data a tool call may have changed.
  onTurnComplete: () => void;
}

// Enough to re-establish where the conversation was, without the feed opening
// on a wall of yesterday.
const HISTORY_TURNS = 12;

const WELCOME: ChatMessage = {
  id: 'w1',
  sender: 'dexter',
  text: 'Hello! I am Dexter. Ask me anything, or tell me what to do.',
};

// Owns the chat feed and the streaming /chat/stream turn loop: live token
// rendering, tool-activity labels and provider-switch handling (roadmap Phase
// 6, items 9 + token-stream rendering). Voice was removed in v5; the chat is
// text-only.
export function useChat({ onTurnComplete }: ChatDeps) {
  const [chatLog, setChatLog] = useState<ChatMessage[]>([WELCOME]);
  const [inputMsg, setInputMsg] = useState('');
  const [isThinking, setIsThinking] = useState(false);
  const [toolActivity, setToolActivity] = useState('');
  // Chat message currently receiving streamed LLM tokens, if any.
  const streamMsgIdRef = useRef<string | null>(null);

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

  const pushDexterMessage = (text: string, action?: string) => {
    setChatLog((prev) => [...prev, { id: `d-${Date.now()}`, sender: 'dexter', text, action }]);
  };

  const handleSendMessage = async (customText?: string) => {
    const textToSend = customText || inputMsg;
    if (!textToSend.trim()) return;

    const userMsg: ChatMessage = { id: `u-${Date.now()}`, sender: 'user', text: textToSend };
    setChatLog((prev) => [...prev, userMsg]);
    setInputMsg('');
    setIsThinking(true);
    setToolActivity('');
    streamMsgIdRef.current = null;

    try {
      // Streaming endpoint: tokens land as they are generated. The user's picked
      // provider/model (or 'auto') rides along on every turn.
      const choice = getLlmChoice();
      const res = await chatStream(textToSend, choice.provider, choice.model);

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
          } else if (evt.type === 'provider_switched') {
            // The reply restarts on a fallback provider; clear the partial text.
            const sid = streamMsgIdRef.current;
            if (sid) setChatLog((prev) => prev.map((m) => (m.id === sid ? { ...m, text: '' } : m)));
            setToolActivity(`retrying on ${evt.payload?.to || 'fallback'}`);
          } else if (evt.type === 'reply') {
            setIsThinking(false);
            setToolActivity('');
            const sid = streamMsgIdRef.current;
            streamMsgIdRef.current = null;
            if (sid) {
              // Finalize the streamed message with the authoritative reply.
              setChatLog((prev) =>
                prev.map((m) =>
                  m.id === sid ? { ...m, text: evt.payload.reply, action: evt.payload.action } : m,
                ),
              );
            } else {
              setChatLog((prev) => [
                ...prev,
                { id: `d-${Date.now()}`, sender: 'dexter', text: evt.payload.reply, action: evt.payload.action },
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
