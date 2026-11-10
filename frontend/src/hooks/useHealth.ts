import { useState, useEffect } from 'react';
import { ReadyResponse } from '../types/api';

const API_BASE = 'http://127.0.0.1:8000';

export function useHealth(pollIntervalMs = 5000) {
  const [health, setHealth] = useState<ReadyResponse | null>(null);
  const [isBackendOnline, setIsBackendOnline] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let isMounted = true;

    async function checkHealth() {
      try {
        const res = await fetch(`${API_BASE}/health/ready`);
        if (!res.ok) throw new Error('Health check non-200');
        const data: ReadyResponse = await res.json();
        if (isMounted) {
          setHealth(data);
          setIsBackendOnline(true);
          setLoading(false);
        }
      } catch (err) {
        if (isMounted) {
          setIsBackendOnline(false);
          setHealth(null);
          setLoading(false);
        }
      }
    }

    checkHealth();
    const interval = setInterval(checkHealth, pollIntervalMs);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [pollIntervalMs]);

  return { health, isBackendOnline, loading };
}
