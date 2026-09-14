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
