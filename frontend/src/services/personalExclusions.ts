import { ref } from 'vue';
import { ApiError, request } from './http';
import { csrfHeaders, ensureGuestSession } from './userSession';

export const exclusionsStorageKey = 'toxicheck.personal-exclusions';

export function readPersonalExclusions(): string[] {
  try {
    const value: unknown = JSON.parse(window.localStorage.getItem(exclusionsStorageKey) || '[]');
    return Array.isArray(value)
      ? [...new Set(value.filter((id): id is string => typeof id === 'string'))]
      : [];
  } catch {
    return [];
  }
}

type ServerPreferences = { excludedIngredientIds: string[]; revision: number; localImported: boolean; unresolvedIds: string[] };
export const personalExclusions = ref(readPersonalExclusions());
export const preferencesLoaded = ref(false);
export const preferencesSaving = ref(false);
export const preferencesError = ref('');
export const preferencesNotice = ref('');
let revision = 0;
let owner = '';
let loading: Promise<void> | undefined;
let writing: Promise<void> | undefined;
let dirty = false;

function cache(ids: string[]) {
  try { localStorage.setItem(exclusionsStorageKey, JSON.stringify(ids)); }
  catch { preferencesNotice.value = 'Браузер не разрешил сохранить локальную копию настроек.'; }
}
const sameIds = (a: string[], b: string[]) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());

export function loadPersonalPreferences(force = false): Promise<void> {
  if (loading) return loading;
  if (preferencesLoaded.value && !force) return Promise.resolve();
  if (writing) return writing.then(() => loadPersonalPreferences(force));
  loading = (async () => {
    preferencesError.value = '';
    const session = await ensureGuestSession();
    let data = await request<ServerPreferences>('/preferences/me', { cache: 'no-store' });
    if (!data.localImported) {
      const legacy = readPersonalExclusions();
      if (legacy.length) {
        try { localStorage.setItem(`${exclusionsStorageKey}.backup`, JSON.stringify(legacy)); } catch { /* The server import still works without local storage. */ }
      }
      data = await request<ServerPreferences>('/preferences/me/import', {
        method: 'POST', headers: csrfHeaders(session.csrf), body: JSON.stringify({ excludedIngredientIds: legacy })
      });
      if (data.unresolvedIds.length) preferencesNotice.value = `Не удалось перенести исключения: ${data.unresolvedIds.length}. Исходный список сохранён в локальной копии, если браузер разрешает хранение.`;
    }
    owner = session.user!.id;
    revision = data.revision;
    personalExclusions.value = data.excludedIngredientIds;
    cache(data.excludedIngredientIds);
    dirty = false;
    preferencesLoaded.value = true;
  })().catch(error => {
    preferencesError.value = error instanceof Error ? error.message : 'Не удалось загрузить профиль';
    throw error;
  }).finally(() => { loading = undefined; });
  return loading;
}

export function savePersonalPreferences(): Promise<void> {
  if (writing) return writing;
  if (!preferencesLoaded.value) return Promise.resolve();
  dirty = true;
  writing = (async () => {
    preferencesSaving.value = true;
    preferencesError.value = '';
    try {
      const session = await ensureGuestSession();
      if (session.user?.id !== owner) throw new Error('Сессия изменилась. Загрузите сохранённые настройки.');
      while (dirty) {
        dirty = false;
        const ids = [...personalExclusions.value];
        let result: ServerPreferences;
        try {
          result = await request<ServerPreferences>('/preferences/me', { method: 'PUT', headers: csrfHeaders(session.csrf),
            body: JSON.stringify({ excludedIngredientIds: ids, revision }) });
        } catch (error) {
          if (!(error instanceof ApiError) || error.status !== 409) throw error;
          const current = await request<ServerPreferences>('/preferences/me', { cache: 'no-store' });
          if (!sameIds(current.excludedIngredientIds, ids)) throw error;
          result = current;
        }
        revision = result.revision;
      }
    } catch (error) {
      dirty = true;
      preferencesError.value = error instanceof Error ? error.message : 'Не удалось сохранить исключения';
    } finally { preferencesSaving.value = false; }
  })().finally(() => { writing = undefined; });
  return writing;
}

export function setPersonalExclusions(ids: string[]) {
  if (!preferencesLoaded.value) return;
  personalExclusions.value = [...new Set(ids)];
  cache(personalExclusions.value);
  dirty = true;
  void savePersonalPreferences();
}
