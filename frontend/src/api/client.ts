/**
 * Shared API Client and Authentication Configuration
 */

export function getApiBase(): string {
  if (typeof window !== 'undefined') {
    const custom = sessionStorage.getItem('api_base') || localStorage.getItem('api_base');
    if (custom) return custom.replace(/\/+$/, '');

    // In Vite dev mode (port 5173), Vite dev proxy forwards /v1 and /health to 8000
    if (window.location.port === '5173') {
      return '';
    }
    // If frontend is served directly by the backend (port 8000)
    if (window.location.port === '8000') {
      return '';
    }
    // Same origin behind reverse proxy (Caddy / Nginx on 80/443 or default empty port)
    if (!window.location.port || window.location.port === '443' || window.location.port === '80') {
      return '';
    }
    // If running locally on another dev port, target port 8000 on localhost
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
      return `${window.location.protocol}//${window.location.hostname}:8000`;
    }
    return '';
  }
  return '';
}

export function getWsBase(): string {
  if (typeof window !== 'undefined') {
    const custom = sessionStorage.getItem('api_base') || localStorage.getItem('api_base');
    if (custom) {
      return custom.replace(/^http/, 'ws').replace(/\/+$/, '');
    }
    // In Vite dev mode (port 5173) or local backend (port 8000)
    if (window.location.port === '5173' || window.location.port === '8000') {
      const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
      return `${wsProto}//${window.location.hostname}:8000`;
    }
    // Same origin behind reverse proxy (Caddy / Nginx on standard port 80/443)
    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${wsProto}//${window.location.host}`;
  }
  return 'ws://127.0.0.1:8000';
}

export function getApiKey(): string {
  if (typeof window !== 'undefined') {
    const key =
      sessionStorage.getItem('admin_api_key') ||
      sessionStorage.getItem('api_key') ||
      localStorage.getItem('admin_api_key') ||
      localStorage.getItem('api_key');
    if (key) return key;
    return '';
  }
  return '';
}

export function setApiKey(key: string): void {
  if (typeof window !== 'undefined') {
    // Store in sessionStorage (never survives tab close, protects against persistent XSS)
    sessionStorage.setItem('admin_api_key', key);
    sessionStorage.setItem('api_key', key);
    // Remove from legacy localStorage
    localStorage.removeItem('admin_api_key');
    localStorage.removeItem('api_key');
  }
}

export function clearApiKey(): void {
  if (typeof window !== 'undefined') {
    sessionStorage.removeItem('admin_api_key');
    sessionStorage.removeItem('api_key');
    localStorage.removeItem('admin_api_key');
    localStorage.removeItem('api_key');
  }
}

export function getAuthHeaders(includeContentType = true): Record<string, string> {
  const headers: Record<string, string> = {
    'X-Requested-With': 'XMLHttpRequest',
  };
  const key = getApiKey();
  if (key) {
    headers['Authorization'] = `Bearer ${key}`;
  }
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
    'X-Requested-With': 'XMLHttpRequest',
  };
  if (!isFormData) {
    defaultHeaders['Content-Type'] = 'application/json';
  }

  const apiKey = getApiKey();
  if (apiKey) {
    defaultHeaders['Authorization'] = `Bearer ${apiKey}`;
  }

  const mergedHeaders = {
    ...defaultHeaders,
    ...(options.headers || {}),
  };

  const res = await fetch(url, {
    credentials: 'include',
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
