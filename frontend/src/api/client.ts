/**
 * Shared API Client and Authentication Configuration
 */

export function getApiBase(): string {
  if (typeof window !== 'undefined') {
    const custom = localStorage.getItem('api_base');
    if (custom) return custom.replace(/\/+$/, '');

    // In Vite dev mode (port 5173), Vite dev proxy forwards /v1 and /health to 8000
    if (window.location.port === '5173') {
      return '';
    }
    // If frontend is served directly by the backend (port 8000)
    if (window.location.port === '8000') {
      return '';
    }
    // If accessed over LAN or another port, target port 8000 on the same host
    if (window.location.hostname && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1') {
      return `${window.location.protocol}//${window.location.hostname}:8000`;
    }
  }
  return 'http://127.0.0.1:8000';
}

export function getApiKey(): string {
  if (typeof window !== 'undefined') {
    return (
      localStorage.getItem('admin_api_key') ||
      localStorage.getItem('api_key') ||
      'dev-voice-key-12345'
    );
  }
  return 'dev-voice-key-12345';
}

export function setApiKey(key: string): void {
  if (typeof window !== 'undefined') {
    localStorage.setItem('admin_api_key', key);
    localStorage.setItem('api_key', key);
  }
}

export function getAuthHeaders(includeContentType = true): Record<string, string> {
  const headers: Record<string, string> = {
    Authorization: `Bearer ${getApiKey()}`,
  };
  if (includeContentType) {
    headers['Content-Type'] = 'application/json';
  }
  return headers;
}

export async function apiClient<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const base = getApiBase();
  const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
  const url = base ? `${base}${cleanEndpoint}` : cleanEndpoint;

  const isFormData = options.body instanceof FormData;
  const defaultHeaders: Record<string, string> = {
    Authorization: `Bearer ${getApiKey()}`,
  };
  if (!isFormData) {
    defaultHeaders['Content-Type'] = 'application/json';
  }

  const mergedHeaders = {
    ...defaultHeaders,
    ...(options.headers || {}),
  };

  const res = await fetch(url, {
    ...options,
    headers: mergedHeaders,
  });

  if (!res.ok) {
    let errMessage = `HTTP error ${res.status}`;
    try {
      const errData = await res.json();
      errMessage =
        errData?.error?.message ||
        errData?.detail ||
        errData?.message ||
        `HTTP error ${res.status}`;
    } catch {
      // response body wasn't JSON
    }
    throw new Error(errMessage);
  }

  return res.json();
}
