/**
 * Shared API Client and Authentication Configuration
 */

export function getApiBase(): string {
  if (typeof window !== 'undefined') {
    const custom = sessionStorage.getItem('api_base') || localStorage.getItem('api_base');
    if (custom) return custom.replace(/\/+$/, '');

    const envBase = (import.meta as any).env?.VITE_API_BASE_URL;
    if (envBase) return envBase.replace(/\/+$/, '');

    // In Vite dev mode (port 5173), Vite dev proxy forwards /v1 and /health to 8000
    if (window.location.port === '5173') {
      return '';
    }
    // If frontend is served directly by the backend (port 8000)
    if (window.location.port === '8000') {
      return '';
    }
    // Same origin behind reverse proxy (Caddy / Nginx / RunPod proxy on 80/443 or default empty port)
    if (!window.location.port || window.location.port === '443' || window.location.port === '80') {
      return '';
    }
    // If running locally on another dev port, target port 8000 on localhost
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
      return `${window.location.protocol}//${window.location.hostname}:8000`;
    }
    return `${window.location.protocol}//${window.location.host}`;
  }
  return '';
}

export function getWsBase(): string {
  if (typeof window !== 'undefined') {
    const custom = sessionStorage.getItem('api_base') || localStorage.getItem('api_base');
    if (custom) {
      return custom.replace(/^http/, 'ws').replace(/\/+$/, '');
    }
    const envWs = (import.meta as any).env?.VITE_WS_BASE_URL;
    if (envWs) return envWs.replace(/\/+$/, '');

    const wsProto = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

    // In Vite dev mode (port 5173) running strictly on localhost
    if (window.location.port === '5173' && (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1')) {
      return `${wsProto}//${window.location.hostname}:8000`;
    }

    // In all other environments (RunPod proxy, Hetzner cloud host:port, reverse proxy):
    // window.location.host preserves exact hostname and port (e.g. '159.69.x.x:8000' or 'xxxx-8000.proxy.runpod.net')
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
  if (key && key !== 'null' && key !== 'undefined') {
    headers['Authorization'] = `Bearer ${key}`;
  }
  if (includeContentType) {
    headers['Content-Type'] = 'application/json';
  }
  return headers;
}

export async function fetchWithAuth(endpoint: string, options: RequestInit = {}): Promise<Response> {
  const base = getApiBase();
  let url = endpoint;
  if (!endpoint.startsWith('http://') && !endpoint.startsWith('https://')) {
    const cleanEndpoint = endpoint.startsWith('/') ? endpoint : `/${endpoint}`;
    url = base ? `${base}${cleanEndpoint}` : cleanEndpoint;
  }

  const isFormData = options.body instanceof FormData;
  const defaultHeaders: Record<string, string> = {
    'X-Requested-With': 'XMLHttpRequest',
  };
  if (!isFormData && !(options.headers && (options.headers as any)['Content-Type'])) {
    defaultHeaders['Content-Type'] = 'application/json';
  }

  const apiKey = getApiKey();
  if (apiKey && apiKey !== 'null' && apiKey !== 'undefined') {
    defaultHeaders['Authorization'] = `Bearer ${apiKey}`;
  }

  const mergedHeaders = {
    ...defaultHeaders,
    ...(options.headers || {}),
  };

  return fetch(url, {
    credentials: 'include',
    ...options,
    headers: mergedHeaders,
  });
}

export async function apiClient<T>(endpoint: string, options: RequestInit = {}): Promise<T> {
  const res = await fetchWithAuth(endpoint, options);

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

export async function getWebSocketUrl(path: string): Promise<string> {
  const wsBase = getWsBase();
  const cleanPath = path.startsWith('/') ? path : `/${path}`;
  const apiKey = getApiKey();
  if (apiKey && apiKey !== 'null' && apiKey !== 'undefined') {
    const sep = cleanPath.includes('?') ? '&' : '?';
    return `${wsBase}${cleanPath}${sep}api_key=${encodeURIComponent(apiKey)}`;
  }
  // Try to acquire single-use WebSocket ticket for secure authenticated sessions
  try {
    const data = await apiClient<{ ticket: string }>('/v1/auth/ticket', { method: 'POST' });
    if (data?.ticket) {
      const sep = cleanPath.includes('?') ? '&' : '?';
      return `${wsBase}${cleanPath}${sep}ticket=${encodeURIComponent(data.ticket)}`;
    }
  } catch (e) {
    console.warn('Failed to acquire ticket, falling back to direct cookie WebSocket:', e);
  }
  return `${wsBase}${cleanPath}`;
}
