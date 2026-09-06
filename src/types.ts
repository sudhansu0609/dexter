// Shared API/UI types. Previously these interfaces were declared inline at the
// top of the App monolith; centralising them lets every component and hook share
// one definition of each backend shape (roadmap Phase 6, item 3).

export interface Task {
  id: string;
  time: string;
  title: string;
  completed: boolean;
}

export interface OverNightItem {
  id: string;
  timestamp: string;
  title: string;
  hook: string;
  scriptOutline: string[];
  targetAudience: string;
  ctrScore: string;
}

export interface SkillItem {
  name: string;
  description: string;
  triggers: string[];
  steps: string[];
  learnedAt: string;
}

export interface FileResult {
  fileName: string;
  path: string;
  sizeBytes: number;
}

export interface BuzzcafProject {
  id: string;
  name: string;
  brand: string;
  current_step: string;
  status: string;
  workflow_name: string;
}

export interface ReminderBanner {
  id: string;
  text: string;
  missed?: boolean;
}

export interface BgTask {
  id: string;
  title: string;
  status: string;
  question?: string | null;
  progress?: { ts: string; text: string }[];
  result?: string | null;
}

export type ChatSender = 'user' | 'dexter';

export interface ChatMessage {
  id: string;
  sender: ChatSender;
  text: string;
  action?: string;
}

// A single frame decoded from the /chat/stream SSE body.
export interface StreamEvent {
  type: 'tool_start' | 'token' | 'provider_switched' | 'reply' | 'audio' | 'error' | string;
  payload?: any;
}

// Runtime health: how well Dexter is actually running right now. Added because
// a 7B silently falling back to the CPU (another process holding the VRAM) made
// every reply take ~9s and nothing in the app ever said so.
export interface RuntimeWarning {
  code: string;
  message: string;
  action: string;
}

export interface RuntimeHealth {
  ok: boolean;
  severity: 'none' | 'warning' | string;
  provider: string;
  model: string;
  device: 'gpu' | 'cpu' | 'partial' | 'unknown' | string;
  gpuFraction: number | null;
  gpu: { name: string; totalMb: number; usedMb: number; freeMb: number } | null;
  warnings: RuntimeWarning[];
  checkedAt: number;
}

export interface DexterStatus {
  status: string;
  runtime?: RuntimeHealth;
  toolCount?: number;
  intentMode?: string;
}

// The workbook: goals, the commitments that serve them, and what actually
// happened. This is the model of the user's work that the app previously had
// nowhere to put - the day planner was a flat list of today's tasks.
export interface Goal {
  id: number;
  title: string;
  why: string | null;
  status: string;
  target_date: string | null;
}

export interface Commitment {
  id: number;
  text: string;
  due_date: string | null;
  due_time: string | null;
  status: string;
  roll_count: number;
  goal_id: number | null;
  project_id: number | null;
  // Present only on the slipping list.
  overdue?: boolean;
  stale?: boolean;
  daysLate?: number;
}

export interface WorkbookToday {
  date: string;
  due: Commitment[];
  overdue: Commitment[];
  done: Commitment[];
  goals: Goal[];
}

export interface WorkbookStats {
  goals: number;
  projects: number;
  openCommitments: number;
  entries: number;
  slipping: number;
}

export interface WorkbookSnapshot {
  today: WorkbookToday;
  goals: Goal[];
  slipping: Commitment[];
  stats: WorkbookStats;
  closedOut: boolean;
}

// A runtime-tunable setting. `liveReload` false means the value is saved but
// only takes effect on the next start, which the UI has to say out loud rather
// than letting the change look applied.
export interface SettingRow {
  key: string;
  label: string;
  help: string;
  kind: 'bool' | 'int' | 'float' | 'string' | 'choice';
  options: string[];
  group: string;
  value: unknown;
  default: unknown;
  liveReload: boolean;
}

export interface SettingsUpdateResult {
  ok: boolean;
  applied: string[];
  needsRestart: string[];
  rejected: string[];
  settings: SettingRow[];
}
