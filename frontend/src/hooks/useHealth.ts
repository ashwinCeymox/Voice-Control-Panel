import { useState, useEffect } from 'react';
import { ReadyResponse } from '../types/api';
import { getApiBase } from '../api/client';

export function useHealth(pollIntervalMs = 5000) {
  const [health, setHealth] = useState<ReadyResponse | null>(null);
  const [isBackendOnline, setIsBackendOnline] = useState<boolean>(false);
  const [loading, setLoading] = useState<boolean>(true);

  useEffect(() => {
    let isMounted = true;

    async function checkHealth() {
      try {
        const base = getApiBase();
        const res = await fetch(`${base}/health/ready`);
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
