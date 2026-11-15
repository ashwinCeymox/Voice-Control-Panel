/**
 * Admin API client helpers for Developer Dashboard.
 */

export function getAdminApiKey(): string {
  return localStorage.getItem('admin_api_key') || 'dev-voice-key-12345';
}

export function setAdminApiKey(key: string): void {
  localStorage.setItem('admin_api_key', key);
}

export function getAdminHeaders(): Record<string, string> {
  return {
    'Authorization': `Bearer ${getAdminApiKey()}`,
    'Content-Type': 'application/json',
  };
}

export async function fetchAdminJson<T>(url: string, options: RequestInit = {}): Promise<T> {
  const headers = {
    ...getAdminHeaders(),
    ...(options.headers || {}),
  };
  const res = await fetch(url, { ...options, headers });
  if (!res.ok) {
    let errMessage = `HTTP error ${res.status}`;
    try {
      const errData = await res.json();
      errMessage = errData?.error?.message || errData?.detail || errMessage;
    } catch {
      // ignore
    }
    throw new Error(errMessage);
  }
  return res.json();
}
