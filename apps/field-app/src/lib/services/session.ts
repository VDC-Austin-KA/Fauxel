import * as SecureStore from 'expo-secure-store';
import { normalizeBaseUrl, Session } from '../api/fauxel';

const KEY = 'fauxel.session.v1';

export async function loadSession(): Promise<Session | null> {
  const raw = await SecureStore.getItemAsync(KEY);
  if (!raw) return null;
  try {
    const s = JSON.parse(raw);
    return typeof s.baseUrl === 'string' && typeof s.apiKey === 'string' ? s : null;
  } catch {
    return null;
  }
}

export async function saveSession(baseUrlInput: string, apiKey: string): Promise<Session> {
  const key = apiKey.trim();
  if (!key) throw new Error('API key required');
  const s = { baseUrl: normalizeBaseUrl(baseUrlInput), apiKey: key };
  await SecureStore.setItemAsync(KEY, JSON.stringify(s), { keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY });
  return s;
}

export async function clearSession() {
  await SecureStore.deleteItemAsync(KEY);
}
