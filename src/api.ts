/// <reference types="vite/client" />
// Single home for the backend base URL, the per-run auth token, and every typed
// endpoint call (roadmap Phase 6, item 11 + 3). Nothing outside this file builds
// a backend URL, and since GUARDIAN_PLAN.md section 11 the port is not written
// down here at all - Dexter's backend takes the first free port from 8098
// upwards, so any literal in this file would be wrong the first time something
// else held it.
//
// The backend mints a fresh session token every start and requires it as
// X-Dexter-Token on every /api/ call. The token itself is fetched from the
// bootstrap endpoint, which only answers local shells (loopback bind + Host
// check on the server side).

import type {
  BgTask,
  BuzzcafProject,
  BuzzcafState,
  DexterStatus,
  FileResult,
  HandMouseStatus,
  SettingRow,
  SettingsUpdateResult,
  SkillItem,
  Task,
  UserProfile,
  WorkbookSnapshot,
 MissionStatus,
  ResourceSnapshot,
  AppRow,
  ScheduleItem,
  ScheduleAction,
  PairCodeInfo,
  PairedDevice,
  ToolReply } from './types';

// A stable id for this page/tab, generated once per load. Sent on every chat
// turn so the SSE `chat_turn` mirror can skip the device that originated the
// turn (it already rendered it) while every other open device appends it.
export const CLIENT_ID: string = (() => {
  try {
    return crypto.randomUUID();
  } catch {
    return Math.random().toString(36).slice(2) + Date.now().toString(36);
  }
})();

function resolveApiBase(): string {
  // Served by the backend itself - the pywebview window, and anything that
  // opens the built bundle - so the origin already carries the right port
  // whatever it turned out to be (section 11, rule 6).
  if (typeof window !== 'undefined' && /^https?:$/.test(window.location?.protocol || '')) {
    return window.location.origin;
  }
  // A shell with no http origin (Electron's file://). It gets the backend port
  // baked in at build time from VITE_DEXTER_PORT; relative URLs are the last
  // resort and only work behind a proxy.
  const port = import.meta.env?.VITE_DEXTER_PORT;
  return port ? `http://127.0.0.1:${port}` : '';
}

// In `npm run dev` this is the Vite origin and Vite proxies /api and /health to
// the backend port it was launched with (see vite.config.ts).
export const API_BASE = resolveApiBase();
const DEXTER = `${API_BASE}/api/dexter`;

let token: string | null = null;
let tokenPromise: Promise<string | null> | null = null;

// A paired phone keeps its long-lived device token here; a PC shell fetches the
// shared session token instead. localStorage can be absent/blocked, so every
// access is guarded.
const DEVICE_TOKEN_KEY = 'dexter.deviceToken';
const LOOPBACK_HOSTS = new Set(['127.0.0.1', 'localhost', '::1', '']);

/** Are we one of the PC's own shells? Only those may fetch the shared token. */
export function isLocalShell(): boolean {
  try {
    return LOOPBACK_HOSTS.has((window.location?.hostname || '').toLowerCase());
  } catch {
    return true;
  }
}

function readDeviceToken(): string | null {
  try {
    return window.localStorage.getItem(DEVICE_TOKEN_KEY);
  } catch {
    return null;
  }
}

export function storeDeviceToken(value: string | null): void {
  try {
    if (value) window.localStorage.setItem(DEVICE_TOKEN_KEY, value);
    else window.localStorage.removeItem(DEVICE_TOKEN_KEY);
  } catch {
    /* private mode - keep it in memory only */
  }
  token = value;
}

/** A remote device with no paired token yet: the app shows the pairing screen
 *  rather than failing every call with 401. */
export function needsPairing(): boolean {
  return !isLocalShell() && !readDeviceToken();
}

async function fetchToken(): Promise<string | null> {
  // Off-box: never hit /auth/token (the backend refuses it), use the stored
  // device token straight from pairing.
  if (!isLocalShell()) {
    token = readDeviceToken();
    return token;
  }
  try {
    const res = await fetch(`${DEXTER}/auth/token`);
    if (res.ok) {
      const data = await res.json();
      token = data.token || null;
    }
  } catch {
    token = null;
  }
  return token;
}

function ensureToken(): Promise<string | null> {
  if (token) return Promise.resolve(token);
  const stored = !isLocalShell() ? readDeviceToken() : null;
  if (stored) {
    token = stored;
    return Promise.resolve(stored);
  }
  if (!tokenPromise) {
    tokenPromise = fetchToken().finally(() => {
      tokenPromise = null;
    });
  }
  return tokenPromise;
}

export async function apiFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const withToken = (t: string | null): RequestInit => ({
    ...init,
    headers: { ...(init.headers || {}), ...(t ? { 'X-Dexter-Token': t } : {}) },
  });
  let res = await fetch(url, withToken(await ensureToken()));
  if (res.status === 401) {
    // The backend restarted and rotated its token - refresh once and retry.
    token = null;
    const fresh = await ensureToken();
    if (fresh) res = await fetch(url, withToken(fresh));
  }
  return res;
}

// EventSource cannot send headers, so the events stream authenticates by query param.
export async function eventsUrl(): Promise<string> {
  const t = await ensureToken();
  return `${DEXTER}/events${t ? `?token=${encodeURIComponent(t)}` : ''}`;
}

// ─────────────────────────── helpers ───────────────────────────

const jsonHeaders = { 'Content-Type': 'application/json' };

async function getJson<T>(path: string): Promise<T> {
  const res = await apiFetch(`${DEXTER}${path}`);
  return res.json() as Promise<T>;
}

async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const res = await apiFetch(`${DEXTER}${path}`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(body ?? {}),
  });
  return res.json() as Promise<T>;
}

async function post(path: string, body?: unknown): Promise<void> {
  await apiFetch(`${DEXTER}${path}`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify(body ?? {}),
  });
}

// ─────────────────────────── planner ───────────────────────────

export const getStatus = () => getJson<DexterStatus>('/status');

export interface HistoryTurn {
  role: string;
  content: string;
}

export const getHistory = (limit = 20, session = 'default') =>
  getJson<{ session: string; turns: HistoryTurn[] }>(
    `/memory/history?session=${encodeURIComponent(session)}&limit=${limit}`,
  );

export const getSettings = () => getJson<{ settings: SettingRow[] }>('/settings');

export const updateSettings = (changes: Record<string, unknown>) =>
  postJson<SettingsUpdateResult>('/settings', { changes });

export const getProfile = () => getJson<{ profile: UserProfile; summary: string }>('/profile');
export const updateProfile = (profile: Partial<UserProfile>) =>
  postJson<{ profile: UserProfile; summary: string }>('/profile', { profile });
export const reindexKnowledge = () => postJson<Record<string, unknown>>('/knowledge/reindex');

export const getWorkbook = () => getJson<WorkbookSnapshot>('/workbook');
export const getMission = () => getJson<MissionStatus>('/mission');
export const setInitiative = (mode: 'dexter' | 'me') => postJson<MissionStatus>('/initiative', { mode });

export const addCommitment = (text: string, when?: string, goal?: string) =>
  postJson<{ ok: boolean; summary: string }>('/workbook/commitments', { text, when, goal });

export const finishCommitment = (id: number) => post(`/workbook/commitments/${id}/done`);

export const rollCommitment = (id: number) => post(`/workbook/commitments/${id}/roll`);

export const closeOutDay = () => postJson<{ done: number; rolled: number }>(
  '/workbook/close-out', { rollUnfinished: true },
);

export const getPlanner = () => getJson<{ tasks?: Task[] }>('/planner');
export const addPlannerTask = (title: string) => post('/planner/add', { title });
export const togglePlannerTask = (taskId: string) => post(`/planner/toggle/${taskId}`);

// ─────────────────────────── skills ────────────────────────────

export const getSkills = () => getJson<SkillItem[]>('/skills');

// ─────────────────────────── buzzcaf ───────────────────────────

export const getBuzzcafHealth = () => getJson<{ online?: boolean }>('/buzzcaf/health');
export const getBuzzcafProjects = () => getJson<BuzzcafProject[]>('/buzzcaf/projects');
export const executeBuzzcafStep = (projectId: string) =>
  postJson<BuzzcafProject>(`/buzzcaf/projects/${projectId}/execute`);
export const startBuzzcaf = () =>
  postJson<{ ready?: boolean; message?: string }>('/buzzcaf/start');
export const getBuzzcafState = () => getJson<BuzzcafState>('/buzzcaf/state');
export const approveBuzzcafStep = (projectId: string, feedback?: string) =>
  postJson<BuzzcafProject>(`/buzzcaf/projects/${projectId}/approve`, feedback ? { feedback } : {});

// ─────────────────────────── system ────────────────────────────

export const launchApp = (appName: string) => post('/system/launch', { appName });

// ───────────────────────── file system ─────────────────────────

export const searchFiles = (query: string) =>
  postJson<{ results?: FileResult[] }>('/filesystem/search', { query });
export const openFile = (path: string) => post('/filesystem/open', { path });

// ─────────────────────── background tasks ──────────────────────

export const getTasks = () => getJson<BgTask[]>('/tasks');
export const confirmTask = (taskId: string, approve: boolean) =>
  post(`/tasks/${taskId}/confirm`, { approve });
export const cancelTask = (taskId: string) => post(`/tasks/${taskId}/cancel`);

// ─────────────────────── device pairing (mobile) ───────────────────────

/** Minted on the PC (loopback only) to show a QR the phone scans. */
export const mintPairingCode = () => postJson<PairCodeInfo>('/pair/code');

/** Redeem a pairing code from the phone; stores the returned device token. */
export async function redeemPairing(code: string, name?: string): Promise<{ token: string; id: string }> {
  // /pair/redeem is token-exempt, so call fetch directly - apiFetch would try to
  // attach a token we do not have yet.
  const res = await fetch(`${DEXTER}/pair/redeem`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ code: (code || '').trim().toUpperCase(), name: name || 'phone' }),
  });
  if (!res.ok) {
    const detail = await res.json().catch(() => ({}));
    throw new Error((detail as { detail?: string }).detail || 'Pairing failed.');
  }
  const data = (await res.json()) as { token: string; id: string };
  storeDeviceToken(data.token);
  return data;
}

export const listDevices = () => getJson<{ devices: PairedDevice[] }>('/devices');
export const revokeDevice = (id: string) =>
  apiFetch(`${DEXTER}/devices/${id}`, { method: 'DELETE' }).then((r) => r.json());

// ─────────────────────────── scheduler ───────────────────────────

export const getSchedule = () =>
  getJson<{ items: ScheduleItem[]; scheduler: Record<string, unknown> }>('/schedule');

export const createSchedule = (action: ScheduleAction, when: string) =>
  postJson<{ ok: boolean; when?: string; id?: string; error?: string }>('/schedule', { action, when });

export const updateSchedule = (id: string, body: { action?: ScheduleAction; when?: string }) =>
  apiFetch(`${DEXTER}/schedule/${id}`, {
    method: 'PUT',
    headers: jsonHeaders,
    body: JSON.stringify(body),
  }).then((r) => r.json());

export const deleteSchedule = (id: string) =>
  apiFetch(`${DEXTER}/schedule/${id}`, { method: 'DELETE' }).then((r) => r.json());

export const runScheduleNow = (id: string) => post(`/schedule/${id}/run`);

// ───────────────────────── reminders ───────────────────────────

export const addReminder = (text: string, when: string) =>
  post('/reminders', { text, when });

// ─────────────────────── LLM provider / models ─────────────────

export interface LlmProviderInfo {
  online: boolean;
  url: string;
  models: string[];
}

export interface LlmModelsResponse {
  providers: Record<string, LlmProviderInfo>; // keyed 'llamacpp' | 'lmstudio' | 'ollama'
  preferred: string; // configured default: 'auto' | 'llamacpp' | 'lmstudio' | 'ollama'
  defaultModel: Record<string, string | null>;
}

// Which providers are up and what models each exposes - drives the UI picker.
export const getModels = () => getJson<LlmModelsResponse>('/llm/models');

// Token accounting: every model call's prompt/completion tokens, all-time and
// this session, per provider and per model (backend/token_ledger.py).
export interface TokenUsageBucket {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
}
export interface TokenUsageRow extends TokenUsageBucket {
  name: string; // provider, or "provider/model"
  provider?: string;
  model?: string;
}
export interface TokenUsageResponse {
  since: string;
  sessionSince: string;
  allTime: TokenUsageBucket;
  session: TokenUsageBucket;
  providers: TokenUsageRow[];
  models: TokenUsageRow[];
  sessionProviders: TokenUsageRow[];
  sessionModels: TokenUsageRow[];
}
export const getTokenUsage = () => getJson<TokenUsageResponse>('/llm/usage');
export const resetTokenUsage = () => postJson<TokenUsageResponse>('/llm/usage/reset');

// ─────────────────────── streaming chat ────────────────────────

// Returns the raw streaming Response; the caller decodes the SSE body itself so
// tokens can be rendered as they arrive. `provider`/`model` let the UI route a
// turn to a specific backend; 'auto' + null falls back to the server's
// configured preference (DEXTER_LLM_PROVIDER).
export interface ChatStreamOptions {
  // Speak the reply: the stream carries `audio` events, one clip per sentence.
  speak?: boolean;
  // The message was spoken, so the reply is shaped to be heard, not read.
  spoken?: boolean;
}

export function chatStream(
  message: string,
  provider: string = 'auto',
  model: string | null = null,
  options: ChatStreamOptions = {},
  signal?: AbortSignal,
): Promise<Response> {
  return apiFetch(`${DEXTER}/chat/stream`, {
    method: 'POST',
    headers: jsonHeaders,
    signal,
    body: JSON.stringify({
      message,
      origin: CLIENT_ID,
      provider: provider || 'auto',
      model: model || null,
      voiceEnabled: Boolean(options.speak),
      voiceInput: Boolean(options.spoken),
    }),
  });
}

// ───────────────────────────── voice (v10) ─────────────────────────────

// WebSocket cannot send headers either; the voice audio channel authenticates
// with the token as a query param, like the SSE stream. `window` names which
// shell window this is (full / popup) so the backend can arbitrate the mic.
export async function voiceWsUrl(windowName = 'full', session = 'default'): Promise<string> {
  const t = await ensureToken();
  const wsBase = API_BASE.replace(/^http/, 'ws');
  return `${wsBase}/api/dexter/voice/ws?session=${encodeURIComponent(session)}&window=${encodeURIComponent(
    windowName,
  )}${t ? `&token=${encodeURIComponent(t)}` : ''}`;
}

// One clip for something Dexter says on its own (a reminder, a check-in).
export async function generateTts(text: string, emotion: string | undefined, isReminder: boolean): Promise<Blob> {
  const res = await apiFetch(`${DEXTER}/tts/generate`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ text, emotion, isReminder }),
  });
  if (!res.ok) throw new Error(`TTS failed: ${res.status}`);
  return res.blob();
}

// Absolute URL for a streamed audio clip (the backend returns a relative path).
export const audioUrl = (url: string) => (url.startsWith('http') ? url : `${API_BASE}${url}`);

// Publish the voice state (one of idle|listening|thinking|speaking) to the bus
// files the ai-visualizer face polls. Fire-and-forget: the face is non-critical,
// so a failed post must never disturb the voice loop.
export const postVoiceBus = (state: string, samples?: number[]) =>
  post('/voice-bus', samples && samples.length ? { state, samples } : { state });

// Cut the reply that is being generated (the stop button / Esc).
export const voiceInterrupt = (session = 'default') => post('/voice/interrupt', { session });
// Mic off/on for the whole session. Independent of the audio socket on purpose:
// muting a window closes its socket, and the backend would otherwise hand the
// mic to the other window. Setting it here mutes the session no matter which
// window holds the mic.
export const voiceMic = (muted: boolean, session = 'default') => post('/voice/mic', { muted, session });
export const cancelChat = (session = 'default') => post('/chat/cancel', { session });

// Who Dexter listens to (v10.2): enrolment state and the speaker settings.
export interface VoiceProfile {
  available: boolean;
  enrolled: boolean;
  enrolledAt?: string | null;
  enrolledSeconds?: number | null;
  strict: boolean;
  threshold: number;
  dexterVoice?: string | null;
  wakeMode?: string;
  voiceKey?: string;
}
export const getVoiceProfile = () => getJson<VoiceProfile>('/voice/profile');
export async function forgetVoiceProfile(): Promise<VoiceProfile> {
  const res = await apiFetch(`${DEXTER}/voice/profile`, { method: 'DELETE' });
  if (!res.ok) throw new Error(`Forget failed: ${res.status}`);
  return res.json();
}

// One clip in a given voice, for picking one by ear.
export async function previewVoice(voiceKey: string, text?: string): Promise<Blob> {
  const res = await apiFetch(`${DEXTER}/voice/preview`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ voiceKey, text: text || null }),
  });
  if (!res.ok) throw new Error(`Preview failed: ${res.status}`);
  return res.blob();
}

// ───────────────── resources, apps and nudges (roadmap v9) ─────────────────

export const getResources = () => getJson<ResourceSnapshot>('/resources');

export const getApps = () => getJson<{ apps: AppRow[]; root: string }>('/apps');

export const startAppById = (id: string) =>
  postJson<ToolReply>(`/apps/${encodeURIComponent(id)}/start`);

// A batch app in the middle of a job needs `confirm` - the same guard the chat
// asks for out loud, so the panel is not the quiet way round it.
export const stopAppById = (id: string, confirm = false) =>
  postJson<ToolReply>(`/apps/${encodeURIComponent(id)}/stop?confirm=${confirm ? 'true' : 'false'}`);

// The Sentinel/batch tools are reached the way the user reaches them: as a
// sentence. One code path for "pause batch jobs", whether tapped or typed.
export const runPhrase = (message: string) =>
  postJson<{ reply: string; action: string }>('/chat', { message });

// ───────────────────── the webcam hand mouse (v13.6) ─────────────────────
//
// Its own endpoint rather than a slice of /status: the panel needs the gesture
// map and the catalogues behind it, and while tracking is on it wants a faster
// tick than the status poll gives.

export const getHandMouse = () => getJson<HandMouseStatus>('/handmouse');

export const startHandMouse = (opts: { dryRun?: boolean; camera?: number } = {}) =>
  postJson<ToolReply>('/handmouse/start', { dryRun: !!opts.dryRun, camera: opts.camera ?? null });

export const stopHandMouse = () => postJson<ToolReply>('/handmouse/stop');

// Changing a gesture goes through the same tool the spoken route uses, so the
// panel and "make a fist the right click" can never disagree.
export const bindHandGesture = (pose: string, action: string) =>
  postJson<ToolReply>('/handmouse/bind', { pose, action });
