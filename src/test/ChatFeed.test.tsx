import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';

import { ChatFeed } from '../components/ChatFeed';
import type { ChatMessage } from '../types';

// v12, P3.2: when Sentinel would not give Dexter the better model, the reply
// still arrives - from the rung below. The answer itself never apologises for
// that (nobody wants a paragraph about VRAM); one muted line under it is what
// turns "Dexter got dumber" into "BuzzEdit has the card".
const props = {
  isThinking: false,
  toolActivity: '',
  inputMsg: '',
  setInputMsg: () => {},
  onSend: () => {},
  onQuickReply: () => {},
  isExpanded: false,
};

describe('ChatFeed', () => {
  it('says which rung a stepped-down reply came from, and who has the card', () => {
    const chatLog: ChatMessage[] = [
      {
        id: 'd-1',
        sender: 'dexter',
        text: 'Renamed 14 files.',
        ladder: {
          model: 'qwen2.5:7b',
          provider: 'ollama',
          steppedDownFrom: 'gemma-4-12b',
          blockers: [{ process: 'BuzzEdit', vram_mib: 9216 }],
        },
      },
    ];
    render(<ChatFeed {...props} chatLog={chatLog} />);
    expect(screen.getByText('on qwen2.5:7b — BuzzEdit holds 9.0 GB')).toBeInTheDocument();
    expect(screen.getByText('Renamed 14 files.')).toBeInTheDocument();
  });

  it('says nothing at all on a turn that got the model it asked for', () => {
    const chatLog: ChatMessage[] = [
      { id: 'd-2', sender: 'dexter', text: 'Renamed 14 files.' },
      {
        id: 'd-3',
        sender: 'dexter',
        text: 'And moved them.',
        ladder: { model: 'gemma-4-12b', provider: 'ollama', steppedDownFrom: null },
      },
    ];
    render(<ChatFeed {...props} chatLog={chatLog} />);
    expect(screen.queryByText(/^on /)).toBeNull();
  });

  // v13, P3.2: the card was refused outright, so the rung ran in system memory.
  // The answer is right and about five times slower; a line that names the
  // place is the difference between "it is thinking" and "it is broken".
  it('says when a reply came from a model running in system memory', () => {
    const chatLog: ChatMessage[] = [
      {
        id: 'd-4',
        sender: 'dexter',
        text: 'Renamed 14 files.',
        ladder: {
          model: 'qwen2.5:7b',
          provider: 'ollama',
          steppedDownFrom: 'gemma-4-12b',
          place: 'cpu',
          blockers: [{ process: 'VrDeep', vram_mib: 10240 }],
        },
      },
    ];
    render(<ChatFeed {...props} chatLog={chatLog} />);
    expect(screen.getByText('on qwen2.5:7b (CPU) — VrDeep holds 10.0 GB')).toBeInTheDocument();
  });

  it('explains the CPU even when there was only ever one rung to step to', () => {
    const chatLog: ChatMessage[] = [
      {
        id: 'd-5',
        sender: 'dexter',
        text: 'Done.',
        ladder: { model: 'qwen2.5:7b', provider: 'ollama', steppedDownFrom: null, place: 'cpu' },
      },
    ];
    render(<ChatFeed {...props} chatLog={chatLog} />);
    expect(screen.getByText('on qwen2.5:7b (CPU) — the card was full')).toBeInTheDocument();
  });
});
