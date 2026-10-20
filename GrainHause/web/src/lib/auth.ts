// Dummy front-end-only auth for the GrainHause marketing site. There is no server: credentials
// are checked against a single hard-coded demo account and the "session" just lives in
// localStorage so a refresh keeps you logged in.

export const DEMO_EMAIL = "eric@grainhouse.com";
export const DEMO_PASSWORD = "Eric123#$1";

export type Session = { email: string };

const KEY = "gh-session";

export function loadSession(): Session | null {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed.email === "string" ? { email: parsed.email } : null;
  } catch {
    return null;
  }
}

export function saveSession(session: Session) {
  try {
    localStorage.setItem(KEY, JSON.stringify(session));
  } catch {
    /* ignore (private browsing, etc.) */
  }
}

export function clearSession() {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

export function checkCredentials(email: string, password: string): boolean {
  return email.trim().toLowerCase() === DEMO_EMAIL && password === DEMO_PASSWORD;
}
