import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';

// Every network call goes through src/api.ts by design (roadmap v3 item 11), so
// one mock of that module isolates the whole UI from the backend.
vi.mock('../api', () => ({
  API_BASE: 'http://localhost:8098',
  apiFetch: vi.fn(async () => new Response('{}')),
  eventsUrl: vi.fn(async () => 'http://localhost:8098/events'),
  getStatus: vi.fn(async () => ({ status: 'online', runtime: mockRuntime })),
  getHistory: vi.fn(async () => ({ session: 'default', turns: mockTurns })),
  getPlanner: vi.fn(async () => ({ tasks: [] })),
  addPlannerTask: vi.fn(async () => {}),
  togglePlannerTask: vi.fn(async () => {}),
  getSkills: vi.fn(async () => []),
  getBuzzcafHealth: vi.fn(async () => ({ online: false })),
  getBuzzcafProjects: vi.fn(async () => []),
  executeBuzzcafStep: vi.fn(async () => {}),
  startBuzzcaf: vi.fn(async () => {}),
  launchApp: vi.fn(async () => {}),
  searchFiles: vi.fn(async () => []),
  openFile: vi.fn(async () => {}),
  getTasks: vi.fn(async () => []),
  confirmTask: vi.fn(async () => {}),
  cancelTask: vi.fn(async () => {}),
  addReminder: vi.fn(async () => {}),
  getModels: vi.fn(async () => ({ providers: {} })),
  chatStream: vi.fn(async () => {}),
  getWorkbook: vi.fn(async () => mockWorkbook),
  addCommitment: vi.fn(async () => ({ ok: true, summary: 'ok' })),
  finishCommitment: vi.fn(async () => {}),
  rollCommitment: vi.fn(async () => {}),
  closeOutDay: vi.fn(async () => ({ done: 0, rolled: 0 })),
}));

let mockRuntime: any = null;
let mockTurns: { role: string; content: string }[] = [];

const emptyWorkbook = {
  today: { date: '2026-08-23', due: [], overdue: [], done: [], goals: [] },
  goals: [],
  slipping: [],
  stats: { goals: 0, projects: 0, openCommitments: 0, entries: 0, slipping: 0 },
  closedOut: false,
};
let mockWorkbook: any = emptyWorkbook;

import App from '../App';

describe('App', () => {
  beforeEach(() => {
    mockRuntime = null;
    mockTurns = [];
    mockWorkbook = emptyWorkbook;
  });

  it('mounts and renders the assistant shell', async () => {
    render(<App />);
    // "Dexter" appears in several places; anchor on the title bar heading and
    // the chat composer, which together prove the tree actually mounted.
    expect(await screen.findByRole('heading', { level: 1 })).toBeInTheDocument();
    expect(screen.getByPlaceholderText(/Ask Dexter anything/i)).toBeInTheDocument();
  });

  it('restores the previous conversation instead of a blank welcome', async () => {
    mockTurns = [
      { role: 'user', content: 'where did we leave the relaunch' },
      { role: 'assistant', content: 'You were three scripts in.' },
    ];

    render(<App />);

    // The regression this guards: the backend persisted these turns and fed them
    // to the model, but the UI opened on a hardcoded welcome every launch, so
    // Dexter looked like it had forgotten a conversation it actually remembered.
    expect(await screen.findByText('You were three scripts in.')).toBeInTheDocument();
    expect(screen.queryByText(/I am Dexter, your AI super assistant/i)).not.toBeInTheDocument();
  });

  it('warns when the model has fallen back to the CPU', async () => {
    mockRuntime = {
      ok: false,
      severity: 'warning',
      provider: 'ollama',
      model: 'qwen2.5:7b',
      device: 'cpu',
      gpuFraction: 0,
      gpu: { name: 'RTX 5060 Ti', totalMb: 16311, usedMb: 14512, freeMb: 1500 },
      warnings: [
        {
          code: 'model_on_cpu',
          message: 'qwen2.5:7b is running entirely on the CPU.',
          action: 'Close the other model server.',
        },
      ],
      checkedAt: 0,
    };

    render(<App />);

    await waitFor(() =>
      expect(screen.getByText(/running entirely on the CPU/i)).toBeInTheDocument(),
    );
    expect(screen.getByText(/Close the other model server/i)).toBeInTheDocument();
  });

  it('shows commitments and how many times each has been moved', async () => {
    mockWorkbook = {
      ...emptyWorkbook,
      goals: [{ id: 1, title: 'ship the relaunch', why: null, status: 'active', target_date: null }],
      today: {
        date: '2026-08-23',
        due: [
          {
            id: 10,
            text: 'script episode three',
            due_date: '2026-08-23',
            due_time: null,
            status: 'open',
            roll_count: 4,
            goal_id: 1,
            project_id: null,
          },
        ],
        overdue: [],
        done: [],
        goals: [],
      },
      slipping: [
        {
          id: 10,
          text: 'script episode three',
          due_date: '2026-08-23',
          due_time: null,
          status: 'open',
          roll_count: 4,
          goal_id: 1,
          project_id: null,
          overdue: false,
          stale: true,
          daysLate: 0,
        },
      ],
    };

    render(<App />);

    expect(await screen.findAllByText('script episode three')).not.toHaveLength(0);
    // The roll count is the coaching signal the flat planner could never show:
    // a task avoided for a week must not look like one written this morning.
    // Shown twice by design - on the row itself and in the slipping callout.
    expect(await screen.findAllByText(/moved 4/i)).not.toHaveLength(0);
    expect(screen.getByText(/1 slipping/i)).toBeInTheDocument();
    expect(screen.getByText(/ship the relaunch/i)).toBeInTheDocument();
  });
});
