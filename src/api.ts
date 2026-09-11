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
  SettingRow,
  SettingsUpdateResult,
  SkillItem,
  Task,
  UserProfile,
  WorkbookSnapshot,
 MissionStatus,
  ResourceSnapshot,
  AppRow,
  ToolReply } from './types';

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

async function fetchToken(): Promise<string | null> {
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
): Promise<Response> {
  return apiFetch(`${DEXTER}/chat/stream`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({
      message,
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

// Cut the reply that is being generated (the stop button / Esc).
export const voiceInterrupt = (session = 'default') => post('/voice/interrupt', { session });

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
