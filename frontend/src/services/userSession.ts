import { ref } from 'vue';
import { request } from './http';

export type SessionUser = { id: string; role: string; account_kind: string; display_name: string | null };
export type BrowserSession = { user: SessionUser | null; csrf: string | null; admin: SessionUser | null; admin_csrf: string | null };
export const browserSession = ref<BrowserSession | null>(null);
let pending: Promise<BrowserSession> | undefined;

export async function refreshSession(): Promise<BrowserSession> {
  const state = await request<BrowserSession>('/auth/session', { cache: 'no-store' });
  browserSession.value = state;
  return state;
}

export function ensureGuestSession(): Promise<BrowserSession> {
  if (pending) return pending;
  pending = (async () => {
    let state = await refreshSession();
    if (!state.user) state = await request<BrowserSession>('/auth/guest', {
      method: 'POST', headers: { 'X-ToxiCheck-Request': '1' }, body: '{}'
    });
    browserSession.value = state;
    return state;
  })().finally(() => { pending = undefined; });
  return pending;
}

export function csrfHeaders(token: string | null | undefined) {
  if (!token) throw new Error('Сессия истекла. Обновите страницу.');
  return { 'X-ToxiCheck-Request': '1', 'X-CSRF-Token': token };
}

export async function loginAdmin(login: string, password: string) {
  const guest = await ensureGuestSession();
  const state = await request<BrowserSession>('/auth/admin/login', {
    method: 'POST', headers: csrfHeaders(guest.csrf), body: JSON.stringify({ login, password })
  });
  browserSession.value = state;
}

export async function logoutAdmin(all = false) {
  const state = await refreshSession();
  if (!state.admin) return;
  browserSession.value = await request<BrowserSession>(`/auth/admin/logout?all_sessions=${all}`, {
    method: 'POST', headers: csrfHeaders(state.admin_csrf), body: '{}'
  });
}
