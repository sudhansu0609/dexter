// Shared API/UI types. Previously these interfaces were declared inline at the
// top of the App monolith; centralising them lets every component and hook share
// one definition of each backend shape (roadmap Phase 6, item 3).

export interface Task {
  id: string;
  time: string;
  title: string;
  completed: boolean;
  kind?: 'commitment' | 'reminder' | 'event';
  overdue?: boolean;
  rollCount?: number;
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

export interface BuzzcafPending {
  project_id: string;
  name: string;
  brand?: string;
  step?: string;
  agent?: string;
  since?: string;
}

export interface BuzzcafState {
  online: boolean;
  version?: string;
  llm_provider?: string;
  projects?: { total: number; in_progress: number; paused_for_approval: number; done: number };
  pending_approvals?: BuzzcafPending[];
  active_project?: { id: string; name: string; brand: string; status: string; current_step: string } | null;
  saved_topics_count?: number;
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

// Which smart rung a turn ran on, and what pushed it down one (v12, D4).
export interface LadderInfo {
  model: string;
  provider: string;
  /** The better model Sentinel refused, or null when we got our first choice. */
  steppedDownFrom?: string | null;
  /** Where the rung ran (v13). "cpu" is slow but right, and worth saying so. */
  place?: 'gpu' | 'cpu' | 'split';
  blockers?: { client?: string; process?: string; label?: string; name?: string; vramMib?: number; vram_mib?: number }[];
  at?: number;
}

export interface ChatMessage {
  id: string;
  sender: ChatSender;
  text: string;
  action?: string;
  /** Set only when the reply came from the fallback rung; the UI says so once, quietly. */
  ladder?: LadderInfo;
  // A message Dexter started (check-in, event) and the one-tap answers it offers.
  kind?: string;
  quickReplies?: string[];
  // How the message came in: spoken through the mic, or typed.
  via?: 'voice' | 'text';
}

// What the backend reports about the voice layer (v10).
export interface VoiceStatus {
  enabled: boolean;
  sttReady: boolean;
  sttLoaded?: boolean;
  wakeReady: boolean;
  vadReady: boolean;
  ttsReady?: boolean;
  model?: string;
  device?: string | null;
  wakeWord?: string;
  wakeMode?: string;
  bargeIn?: boolean;
  followUpWindow?: number;
  speaker?: { available: boolean; enrolled: boolean; strict: boolean };
}

export interface MissionStatus {
  northStar: string;
  why: string;
  focus: string;
  focusSetOn: string;
  todayWin: string;
  blockers: string[];
  wins: { date: string; text: string }[];
  snoozedUntil: number;
  initiative: 'dexter' | 'me';
  // 'VS Code on dexter · 42 min · last saved router.py (dexter) 3 min ago'
  now?: string;
  openLoops: string[];
  sentToday: string[];
}

// A single frame decoded from the /chat/stream SSE body.
export interface StreamEvent {
  type:
    | 'tool_start'
    | 'token'
    | 'provider_switched'
    | 'escalated'
    | 'reply'
    | 'delivery'
    | 'audio'
    | 'interrupted'
    | 'error'
    | 'done'
    | string;
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

// The webcam hand mouse (v13, on the dashboard in v13.6).
//
// Two names for what is happening, and both matter: `pose` is the shape of the
// hand the tracker read, `action` is what that shape is bound to. "It saw a fist
// and deliberately did nothing" and "it never saw my hand" are different problems
// with the same symptom, and showing only one of them is how the first version of
// this was reported as broken.
export interface HandPoseRow {
  pose: string;
  label: string;
  hint: string;
  action: string;
  default: string;
}

export interface HandMouseStatus {
  running: boolean;
  pose: string;
  action: string;
  gesture: string;
  bindings: Record<string, string>;
  poses: HandPoseRow[];
  actions: { action: string; label: string }[];
  buttons: { left: boolean; right: boolean; middle: boolean };
  // v13.8: the pointer is held still through a click so the press lands where it was
  // aimed; past the drag distance it follows the hand again.
  locked: boolean;
  dragging: boolean;
  speed: number;
  region: number[];
  dragThresholdPx: number;
  confirmFrames: number;
  fps: number;
  camera: number | null;
  engine: string;
  dryRun: boolean;
  frames: number;
  handFrames: number;
  seeingHand: boolean;
  minutes: number;
  hotkey: string;
  ready: boolean;
  why: string;
  error: string;
  // How close the camera came to seeing a hand, so "it doesn't work" has a number
  // attached rather than a shrug.
  palmScore: number;
  bestScore: number;
  palmNeeded: number;
  preview: { enabled: boolean; open: boolean; error: string };
}

// The glanceable half, carried on the status poll for the header chip.
export interface HandMouseSummary {
  running: boolean;
  pose?: string;
  action?: string;
  fps?: number;
  ready?: boolean;
  why?: string;
  error?: string;
  palmScore?: number;
  palmNeeded?: number;
  seeingHand?: boolean;
  camera?: number | null;
  minutes?: number;
  dryRun?: boolean;
  previewOpen?: boolean;
}

export interface DexterStatus {
  status: string;
  runtime?: RuntimeHealth;
  toolCount?: number;
  intentMode?: string;
  voice?: VoiceStatus;
  handMouse?: HandMouseSummary;
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
export interface ProfileChannel {
  name: string;
  channel_id: string;
  brand: string;
}

export interface UserProfile {
  name: string;
  timezone: string;
  location: string;
  roles: string[];
  preferences: string[];
  working_hours: string;
  channels: ProfileChannel[];
  notes: string;
}

export interface SettingRow {
  key: string;
  label: string;
  help: string;
  kind: 'bool' | 'int' | 'float' | 'string' | 'choice' | 'model';
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

// ─────────────────── Sentinel, resources and the apps (v9) ───────────────────
//
// Dexter asks Sentinel before loading a model or starting a project. These are
// the shapes `/api/dexter/resources` and `/api/dexter/apps` answer with. Every
// field is nullable on purpose: the guardian may be absent, or be the older
// build with no RAM/disk commands, and the panel has to say which rather than
// render an empty gauge that reads as a fault.

export interface VramState {
  name: string;
  totalMib: number;
  usedMib: number;
  freeMib: number;
  zone?: string;
}

export interface RamState {
  zone?: string;
  totalMib: number | null;
  availMib: number | null;
  commitMib: number | null;
  commitLimitMib: number | null;
  top?: { name?: string; pid?: number; ram_mib?: number }[];
  source?: string;
}

export interface DiskDrive {
  letter: string;
  total: number;
  free: number;
  zone?: string;
}

export interface DiskState {
  drives: DiskDrive[];
  last_cleanup?: { at?: string; freed_bytes?: number; mode?: string } | null;
  pending?: boolean;
  source?: string;
}

export interface ResourceHolder {
  name: string;
  pid?: number;
  vramMib: number;
  frozen?: boolean;
}

export interface FrozenProcess {
  pid?: number;
  name: string;
  class?: string;
  reason?: string;
}

export interface ShedSummary {
  level?: string;
  reason?: string;
  steps?: string[];
  freedMib?: number;
  elapsedMs?: number;
  at?: number;
}

export interface ResourceSnapshot {
  sentinel: { connected: boolean; version: string | null; features: string[]; registered?: boolean | null };
  vram: VramState | null;
  ram: RamState | null;
  disk: DiskState | null;
  holders: ResourceHolder[];
  frozen: FrozenProcess[];
  reservations: unknown[];
  trayAlive: boolean | null;
  models: {
    loaded: {
      name: string; sizeMb: number; vramMb: number; onGpu: boolean;
      /** Read off Ollama's own numbers: all on the card, all in RAM, or split. */
      place?: 'gpu' | 'cpu' | 'split';
    }[];
    ours: string[];
  };
  lastShed: ShedSummary;
  /** The rung the last escalated turn ran on. Absent until one has (v12, P3.3). */
  ladder?: LadderInfo;
  budgets: { vramMib: number; ramMib: number; engineVramMib: number; engineRamMib: number };
}

export interface AppRow {
  id: string;
  label: string;
  class: 'assistant' | 'interactive' | 'batch' | string;
  running: boolean;
  evidence?: string;
  detail?: string;
  pids: number[];
  /** The port to show: the live one when it was identified, else the preferred one. */
  port?: number | null;
  /** Where it is, confirmed by its own health route. Null means "we could not tell". */
  livePort?: number | null;
  /** What it asks for at startup. It steps forward when that one is held. */
  preferredPort?: number | null;
  /** Sub-service ports it prefers: {comfyui: 8188}, {xtts: 8096, vite: 3005}, … */
  extraPreferred?: Record<string, number>;
  vramMib: number;
  ramMib: number;
  exclusiveWith: string[];
  canLaunch: boolean;
  canStop: boolean;
  batchJob: boolean;
  notes?: string;
  warn?: string;
  root?: string;
  rssMib?: number | null;
  uptimeSeconds?: number | null;
}

export interface ToolReply {
  ok: boolean;
  summary: string;
  data?: Record<string, unknown>;
}

// ─────────────────── the scheduler (reminders + actions) ───────────────────
//
// One entry can be a reminder or an *action* Dexter performs on a schedule. The
// action's `type` decides which extra fields matter; the rest are optional so a
// single form and a single list can cover every kind.
export type ScheduleActionType =
  | 'reminder'
  | 'goal'
  | 'command'
  | 'app'
  | 'workflow'
  | 'code_task';

export interface ScheduleAction {
  type: ScheduleActionType;
  text?: string;       // reminder
  goal?: string;       // goal
  command?: string;    // command
  shell?: 'powershell' | 'cmd';
  app?: string;        // app
  projectId?: string;  // workflow
  feedback?: string | null;
  task?: string;       // code_task
  project?: string;
  mode?: string;
  channels?: string[]; // reminder delivery channels
}

export interface ScheduleItem {
  id: string;
  kind: 'reminder' | 'action';
  actionType: ScheduleActionType;
  label: string;
  action?: ScheduleAction;
  whenText?: string | null;
  at?: string | null;
  cron?: Record<string, unknown> | null;
  channels?: string[] | null;
  lastRun?: string | null;
}

// ─────────────────── device pairing (mobile access) ───────────────────
export interface PairCodeInfo {
  code: string;
  expiresIn: number;
  url: string | null;
  pairUrl: string | null;
}

export interface PairedDevice {
  id: string;
  name: string;
  createdAt: number;
  lastSeen: number;
}
