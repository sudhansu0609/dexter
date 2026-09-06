// Single home for the backend base URL, the per-run auth token, and every typed
// endpoint call (roadmap Phase 6, item 11 + 3). Nothing outside this file builds
// a backend URL or references the port, so `:8098` appears in exactly one place.
//
// The backend mints a fresh session token every start and requires it as
// X-Dexter-Token on every /api/ call. The token itself is fetched from the
// bootstrap endpoint, which only answers local shells (loopback bind + Host
// check on the server side).

import type {
  BgTask,
  BuzzcafProject,
  DexterStatus,
  FileResult,
  OverNightItem,
  SettingRow,
  SettingsUpdateResult,
  SkillItem,
  Task,
  WorkbookSnapshot,
} from './types';

export const API_BASE = 'http://localhost:8098';
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

// WebSocket cannot send headers either; the voice audio channel authenticates
// the same way (roadmap Phase 4, item 4.2).
export async function voiceWsUrl(session = 'default'): Promise<string> {
  const t = await ensureToken();
  const wsBase = API_BASE.replace(/^http/, 'ws');
  return `${wsBase}/api/dexter/voice/ws?session=${encodeURIComponent(session)}${
    t ? `&token=${encodeURIComponent(t)}` : ''
  }`;
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

export const getWorkbook = () => getJson<WorkbookSnapshot>('/workbook');

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

// ───────────────────────── overnight queue ─────────────────────

export const getOvernightQueue = () =>
  getJson<OverNightItem[]>('/spilledcoffee/overnight-queue');
export const curateOvernight = (niche: string, count: number) =>
  post('/spilledcoffee/curate', { niche, count });

// ─────────────────────────── skills ────────────────────────────

export const getSkills = () => getJson<SkillItem[]>('/skills');

// ─────────────────────────── buzzcaf ───────────────────────────

export const getBuzzcafHealth = () => getJson<{ online?: boolean }>('/buzzcaf/health');
export const getBuzzcafProjects = () => getJson<BuzzcafProject[]>('/buzzcaf/projects');
export const executeBuzzcafStep = (projectId: string) =>
  postJson<BuzzcafProject>(`/buzzcaf/projects/${projectId}/execute`);
export const startBuzzcaf = () =>
  postJson<{ ready?: boolean; message?: string }>('/buzzcaf/start');

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

// ───────────────────────────── TTS ─────────────────────────────

export async function generateTts(
  text: string,
  emotion: string | undefined,
  isReminder: boolean,
): Promise<Blob> {
  const res = await apiFetch(`${DEXTER}/tts/generate`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ text, voiceKey: 'michael', emotion, isReminder }),
  });
  if (!res.ok) throw new Error('TTS fetch failed');
  return res.blob();
}

// Absolute URL for a streamed audio clip (the backend returns a relative path).
export const audioUrl = (url: string) => (url.startsWith('http') ? url : `${API_BASE}${url}`);

// ─────────────────────── LLM provider / models ─────────────────

export interface LlmProviderInfo {
  online: boolean;
  url: string;
  models: string[];
}

export interface LlmModelsResponse {
  providers: Record<string, LlmProviderInfo>; // keyed 'lmstudio' | 'ollama'
  preferred: string; // configured default: 'auto' | 'lmstudio' | 'ollama'
  defaultModel: Record<string, string | null>;
}

// Which providers are up and what models each exposes - drives the UI picker.
export const getModels = () => getJson<LlmModelsResponse>('/llm/models');

// ─────────────────────── streaming chat ────────────────────────

// Returns the raw streaming Response; the caller decodes the SSE body itself so
// tokens/audio can be rendered as they arrive. `provider`/`model` let the UI
// route a turn to a specific backend; 'auto' + null falls back to the server's
// configured preference (DEXTER_LLM_PROVIDER).
export function chatStream(
  message: string,
  voiceEnabled: boolean,
  provider: string = 'auto',
  model: string | null = null,
): Promise<Response> {
  return apiFetch(`${DEXTER}/chat/stream`, {
    method: 'POST',
    headers: jsonHeaders,
    body: JSON.stringify({ message, provider: provider || 'auto', model: model || null, voiceEnabled }),
  });
}
