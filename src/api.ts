// Single home for the backend base URL and the per-run auth token.
//
// The backend mints a fresh session token every start and requires it as
// X-Dexter-Token on every /api/ call. The token itself is fetched from the
// bootstrap endpoint, which only answers local shells (loopback bind + Host
// check on the server side).

export const API_BASE = 'http://localhost:8098';

let token: string | null = null;
let tokenPromise: Promise<string | null> | null = null;

async function fetchToken(): Promise<string | null> {
  try {
    const res = await fetch(`${API_BASE}/api/dexter/auth/token`);
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
  return `${API_BASE}/api/dexter/events${t ? `?token=${encodeURIComponent(t)}` : ''}`;
}
