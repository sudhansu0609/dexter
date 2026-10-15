import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

// Everything reaches the backend through src/api.ts, so one mock isolates both
// panels (roadmap v3 item 11). These tests are about the two things that are
// only visible in the UI: that an absent or old Sentinel reads as "the guardian
// is old" rather than "Dexter is broken", and that stopping a busy batch app
// asks before the second click carries the confirmation.
vi.mock('../api', () => ({
  getResources: vi.fn(async () => mockResources),
  getApps: vi.fn(async () => ({ apps: mockApps, root: 'B:\\youtubeProjects\\Buzzcaf_Media' })),
  startAppById: vi.fn(async (id: string) => {
    started.push(id);
    return { ok: true, summary: 'Batchy is up.' };
  }),
  stopAppById: vi.fn(async (id: string, confirm: boolean) => {
    stopCalls.push({ id, confirm });
    if (!confirm) {
      return {
        ok: false,
        summary: 'VrDeep looks busy — 3 job(s) in its queue. Stopping it loses that work. Stop it anyway?',
        data: { needsConfirmation: true },
      };
    }
    return { ok: true, summary: 'VrDeep is stopped.' };
  }),
  runPhrase: vi.fn(async (message: string) => {
    phrases.push(message);
    return { reply: 'ok', action: 'fast_path' };
  }),
}));

let started: string[] = [];
let stopCalls: { id: string; confirm: boolean }[] = [];
let phrases: string[] = [];

const connectedResources = {
  sentinel: { connected: true, version: '2.0.0', features: ['ram_reserve', 'query', 'disk'] },
  vram: { name: 'RTX 5060 Ti', totalMib: 16311, usedMib: 11000, freeMib: 5311, zone: 'Yellow' },
  ram: { zone: 'Green', totalMib: 32768, availMib: 12800, commitMib: null, commitLimitMib: null, top: [] },
  disk: { drives: [{ letter: 'C:', total: 500 * 1024 ** 3, free: 97 * 1024 ** 3, zone: 'Green' }] },
  holders: [{ name: 'VrDeep', pid: 33228, vramMib: 10240, frozen: false }],
  frozen: [],
  reservations: [],
  trayAlive: true,
  models: { loaded: [{ name: 'qwen2.5:3b', sizeMb: 2500, vramMb: 2500, onGpu: true }], ours: [] },
  lastShed: {},
  budgets: { vramMib: 5120, ramMib: 3072, engineVramMib: 12500, engineRamMib: 6500 },
};

let mockResources: any = connectedResources;

const mockApps = [
  {
    id: 'vrdeep', label: 'VrDeep', class: 'batch', running: true, evidence: 'process python.exe',
    detail: '', pids: [33228], port: null, vramMib: 10240, ramMib: 15000,
    exclusiveWith: ['buzzcode'], canLaunch: true, canStop: true, batchJob: true,
    rssMib: 14000, uptimeSeconds: 7200,
  },
  {
    id: 'buzzedit', label: 'BuzzEdit', class: 'batch', running: false, evidence: '', detail: '',
    pids: [], port: 8099, vramMib: 9216, ramMib: 8192, exclusiveWith: ['vrdeep'],
    canLaunch: true, canStop: true, batchJob: true,
  },
];

import { ResourcePanel } from '../components/ResourcePanel';
import { AppsPanel } from '../components/AppsPanel';

describe('ResourcePanel', () => {
  beforeEach(() => {
    mockResources = connectedResources;
    phrases = [];
  });

  it('shows the three gauges with real figures', async () => {
    render(<ResourcePanel />);
    await waitFor(() => expect(screen.getByText('VRAM')).toBeInTheDocument());
    // 11000 MiB of 16311 → 10.7 of 15.9 GB
    expect(screen.getByText('10.7 / 15.9 GB')).toBeInTheDocument();
    expect(screen.getByText('RAM')).toBeInTheDocument();
    expect(screen.getByText('Disk')).toBeInTheDocument();
  });

  it('names who is holding the card', async () => {
    render(<ResourcePanel />);
    await waitFor(() => expect(screen.getByText(/VrDeep/)).toBeInTheDocument());
    expect(screen.getByText('10.0 GB')).toBeInTheDocument();
  });

  it('says the guardian is down rather than showing an empty gauge', async () => {
    mockResources = {
      ...connectedResources,
      sentinel: { connected: false, version: null, features: [] },
      vram: null,
      ram: null,
      disk: null,
      holders: [],
    };
    render(<ResourcePanel />);
    await waitFor(() =>
      expect(screen.getByText(/Sentinel is not answering/)).toBeInTheDocument(),
    );
    expect(screen.getAllByText('not reported').length).toBe(3);
  });

  it('calls an old Sentinel old, instead of looking broken', async () => {
    mockResources = {
      ...connectedResources,
      sentinel: { connected: true, version: null, features: [] },
      ram: { ...connectedResources.ram, source: 'local' },
    };
    render(<ResourcePanel />);
    await waitFor(() => expect(screen.getByText(/older build/)).toBeInTheDocument());
    expect(screen.getByText(/read locally/)).toBeInTheDocument();
  });

  it('warns when the tray - the hand that actually freezes and cleans - is gone', async () => {
    mockResources = { ...connectedResources, trayAlive: false };
    render(<ResourcePanel />);
    await waitFor(() => expect(screen.getByText(/tray is not running/)).toBeInTheDocument());
  });

  it('names the rung Dexter is thinking on and why it is not the better one', async () => {
    mockResources = {
      ...connectedResources,
      ladder: {
        model: 'qwen2.5:7b',
        provider: 'ollama',
        steppedDownFrom: 'gemma-4-12b',
        blockers: [{ process: 'BuzzEdit', vram_mib: 9216 }],
      },
    };
    render(<ResourcePanel />);
    await waitFor(() => expect(screen.getByText(/Thinking on/)).toBeInTheDocument());
    expect(screen.getByText('qwen2.5:7b')).toBeInTheDocument();
    expect(screen.getByText(/stepped down from gemma-4-12b/)).toBeInTheDocument();
    expect(screen.getByText(/BuzzEdit has the card/)).toBeInTheDocument();
  });

  it('shows no rung line at all before the first escalated turn', async () => {
    render(<ResourcePanel />);
    await waitFor(() => expect(screen.getByText('VRAM')).toBeInTheDocument());
    expect(screen.queryByText(/Thinking on/)).toBeNull();
  });

  it('offers only the moves Sentinel allows, as the phrases Dexter understands', async () => {
    render(<ResourcePanel />);
    await waitFor(() => expect(screen.getByText('Pause batch')).toBeInTheDocument());
    expect(screen.queryByText(/kill/i)).toBeNull();
    fireEvent.click(screen.getByText('Safe clean'));
    await waitFor(() => expect(phrases).toEqual(['clean up space']));
  });

  // v13, P3.5: a model can be on the card, in system memory, or split across
  // both, and the difference is about five times the speed. A list of names and
  // gigabytes could not tell the owner why an answer was crawling.
  it('says where each resident model actually is', async () => {
    mockResources = {
      ...connectedResources,
      models: {
        loaded: [
          { name: 'qwen3.5:4b', sizeMb: 3000, vramMb: 0, onGpu: false, place: 'cpu' },
          { name: 'qwen2.5:7b', sizeMb: 5000, vramMb: 5000, onGpu: true, place: 'gpu' },
          { name: 'gemma-4-12b', sizeMb: 8000, vramMb: 2000, onGpu: false, place: 'split' },
        ],
        ours: [],
      },
    };
    render(<ResourcePanel />);
    await waitFor(() => expect(screen.getByText(/qwen3.5:4b 2.9 GB \(CPU\)/)).toBeInTheDocument());
    expect(screen.getByText(/qwen2.5:7b 4.9 GB \(GPU\)/)).toBeInTheDocument();
    expect(screen.getByText(/gemma-4-12b 2.0 GB \(split\)/)).toBeInTheDocument();
  });

  it('names system memory as the reason an escalated turn is slow', async () => {
    mockResources = {
      ...connectedResources,
      ladder: {
        model: 'qwen2.5:7b',
        provider: 'ollama',
        steppedDownFrom: null,
        place: 'cpu',
        blockers: [],
      },
    };
    render(<ResourcePanel />);
    await waitFor(() => expect(screen.getByText(/in system memory/)).toBeInTheDocument());
  });
});

describe('AppsPanel', () => {
  beforeEach(() => {
    started = [];
    stopCalls = [];
  });

  it('lists the projects, running ones first, with what a stopped one would cost', async () => {
    render(<AppsPanel onLaunch={() => {}} />);
    await waitFor(() => expect(screen.getByText('VrDeep')).toBeInTheDocument());
    expect(screen.getByText('BuzzEdit')).toBeInTheDocument();
    expect(screen.getByText('1 running')).toBeInTheDocument();
    expect(screen.getByText('needs ~9.0 GB VRAM')).toBeInTheDocument();
  });

  it('starts a stopped project through the same tool the chat uses', async () => {
    const said: string[] = [];
    render(<AppsPanel onLaunch={() => {}} onMessage={(t) => said.push(t)} />);
    await waitFor(() => expect(screen.getByText('Start')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Start'));
    await waitFor(() => expect(started).toEqual(['buzzedit']));
    expect(said).toContain('Batchy is up.');
  });

  it('asks before stopping a batch app that is mid-job, and only then confirms', async () => {
    render(<AppsPanel onLaunch={() => {}} />);
    await waitFor(() => expect(screen.getByText('Stop')).toBeInTheDocument());

    fireEvent.click(screen.getByText('Stop'));
    await waitFor(() => expect(screen.getByText(/3 job\(s\) in its queue/)).toBeInTheDocument());
    expect(stopCalls).toEqual([{ id: 'vrdeep', confirm: false }]);

    fireEvent.click(screen.getByText('Stop anyway'));
    await waitFor(() => expect(stopCalls.length).toBe(2));
    expect(stopCalls[1]).toEqual({ id: 'vrdeep', confirm: true });
  });

  it('lets the owner back out of a stop', async () => {
    render(<AppsPanel onLaunch={() => {}} />);
    await waitFor(() => expect(screen.getByText('Stop')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Stop'));
    await waitFor(() => expect(screen.getByText('Leave it')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Leave it'));
    await waitFor(() => expect(screen.queryByText('Leave it')).toBeNull());
    expect(stopCalls.length).toBe(1);
  });

  it('keeps the quick launcher for everything that is not a project', async () => {
    const launched: string[] = [];
    render(<AppsPanel onLaunch={(n) => launched.push(n)} />);
    await waitFor(() => expect(screen.getByText('Chrome')).toBeInTheDocument());
    fireEvent.click(screen.getByText('Chrome'));
    expect(launched).toEqual(['Chrome']);
  });
});
