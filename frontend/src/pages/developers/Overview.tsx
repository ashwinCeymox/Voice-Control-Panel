import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  Activity,
  Clock,
  Cpu,
  RefreshCw,
  Zap,
  Server,
  AlertTriangle,
  Trash2,
  Shield,
  CheckCircle2,
  X,
  Radio,
} from 'lucide-react';
import { fetchAdminJson, getAdminApiKey, getAdminHeaders } from './api';

interface UsageSummary {
  since: number;
  until: number;
  total_requests: number;
  success_count: number;
  error_count: number;
  status_429_count: number;
  status_503_count: number;
  success_rate: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  avg_ttfb_ms: number;
  llm_tokens_in: number;
  llm_tokens_out: number;
  stt_audio_seconds_in: number;
  tts_audio_seconds_out: number;
  tts_characters_in: number;
  stt_fallbacks?: Record<string, number>;
  groups: Array<{
    name: string;
    requests: number;
    errors: number;
    input_units: number;
    output_units: number;
    avg_latency_ms: number;
    p50_latency_ms: number;
  }>;
}

interface TimeseriesPoint {
  timestamp: number;
  bucket_label: string;
  requests: number;
  errors: number;
  tokens_in: number;
  tokens_out: number;
  audio_seconds_in: number;
  audio_seconds_out: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
}

interface ResidentModel {
  id: string;
  name: string;
  engine: string;
  state: 'active' | 'idle' | 'stale';
  is_protected: boolean;
  in_flight: number;
  memory_rss_bytes: number;
  memory_rss_mb: number;
  last_used: number;
  source: string;
}

interface ResidencyStatusResponse {
  idle_timeout_seconds: number;
  stale_timeout_seconds: number;
  auto_flush_seconds: number;
  auto_flush_enabled: boolean;
  protected_models: string[];
  models: ResidentModel[];
}

interface SystemMemoryInfo {
  total_mb: number;
  available_mb: number;
  used_mb: number;
  percent: number;
}

interface ModelActivityItem {
  model_id: string;
  display_name: string;
  engine: string;
  state: string;
  memory_mb: number;
  requests: number;
  errors: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  ttfb_ms?: number | null;
  last_used_ts: number;
  last_used_iso: string;
  top_keys: Array<{ name: string; requests: number }>;
}

interface ToastMessage {
  id: string;
  level: 'info' | 'warning' | 'error';
  message: string;
  timestamp: number;
}

export const Overview: React.FC = () => {
  const [timeWindow, setTimeWindow] = useState<'1h' | '24h' | '7d' | '30d'>('24h');
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesPoint[]>([]);
  const [residency, setResidency] = useState<ResidencyStatusResponse | null>(null);
  const [systemMemory, setSystemMemory] = useState<SystemMemoryInfo | null>(null);
  const [modelActivity, setModelActivity] = useState<ModelActivityItem[]>([]);
  const [toasts, setToasts] = useState<ToastMessage[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [actionLoading, setActionLoading] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const seenToastIds = useRef<Set<string>>(new Set());
  const toastCooldowns = useRef<Map<string, number>>(new Map());

  const addToast = (message: string, level: 'info' | 'warning' | 'error' = 'info', customId?: string) => {
    const toastId = customId || `toast-${Date.now()}-${Math.random().toString(36).substring(2, 6)}`;
    if (seenToastIds.current.has(toastId)) return;

    // Throttle identical or repeated flush notifications within 10s
    const now = Date.now();
    const lastSeen = toastCooldowns.current.get(message) || 0;
    if (now - lastSeen < 10000) {
      return;
    }
    toastCooldowns.current.set(message, now);
    seenToastIds.current.add(toastId);

    const newToast: ToastMessage = {
      id: toastId,
      level,
      message,
      timestamp: Date.now(),
    };
    setToasts((prev) => {
      if (prev.some((t) => t.id === toastId || t.message === message)) {
        return prev;
      }
      return [...prev.slice(-3), newToast];
    });
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== newToast.id));
    }, 5000);
  };

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [sumData, tsData, resData, statusData, actData] = await Promise.all([
        fetchAdminJson<UsageSummary>(`/v1/admin/usage/summary?since=${timeWindow}&group_by=model`),
        fetchAdminJson<{ series: TimeseriesPoint[] }>(`/v1/admin/usage/timeseries?interval=hour&since=${timeWindow}`),
        fetchAdminJson<ResidencyStatusResponse>('/v1/admin/residency'),
        fetchAdminJson<{ system_memory: SystemMemoryInfo }>('/v1/admin/models/status'),
        fetchAdminJson<ModelActivityItem[]>(`/v1/admin/usage/models?since=${timeWindow}`),
      ]);
      setSummary(sumData);
      setTimeseries(tsData.series || []);
      setResidency(resData);
      if (statusData?.system_memory) setSystemMemory(statusData.system_memory);
      setModelActivity(Array.isArray(actData) ? actData : []);
    } catch (err: any) {
      setError(err?.message || 'Failed to load telemetry metrics');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const timer = setInterval(loadData, 20000);
    return () => clearInterval(timer);
  }, [timeWindow]);

  // Connect to SSE Live stream for realtime telemetry and toasts
  useEffect(() => {
    const adminKey = getAdminApiKey();
    const url = adminKey ? `/v1/admin/stream/overview?api_key=${encodeURIComponent(adminKey)}` : '/v1/admin/stream/overview';

    let eventSource: EventSource | null = null;
    try {
      eventSource = new EventSource(url, { withCredentials: true });

      eventSource.onmessage = (event) => {
        try {
          const payload = JSON.parse(event.data);
          if (payload.residency) setResidency(payload.residency);
          if (payload.system_memory) setSystemMemory(payload.system_memory);
          if (payload.stt_fallbacks) {
            setSummary((prev) => (prev ? { ...prev, stt_fallbacks: payload.stt_fallbacks } : prev));
          }
          if (Array.isArray(payload.toasts) && payload.toasts.length > 0) {
            payload.toasts.forEach((t: any) => {
              const msg = t.message || (typeof t === 'string' ? t : JSON.stringify(t));
              const lvl = t.level || 'info';
              const tid = t.id || `toast-${t.model_id || msg}`;
              if (!seenToastIds.current.has(tid)) {
                addToast(msg, lvl, tid);
              }
            });
          }
        } catch (e) {
          // ignore parse errors
        }
      };

      eventSource.onerror = () => {
        if (eventSource) eventSource.close();
      };
    } catch (e) {
      // EventSource fallback
    }

    return () => {
      if (eventSource) eventSource.close();
    };
  }, []);

  const handleFlushModel = async (modelId: string, force = false) => {
    setActionLoading(modelId);
    try {
      const res = await fetch(`/v1/admin/residency/${encodeURIComponent(modelId)}/flush?force=${force}`, {
        method: 'POST',
        headers: getAdminHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        const flushId = `flush-${modelId}-${Math.floor(Date.now() / 8000)}`;
        addToast(data.message || `Model '${modelId}' unloaded successfully`, 'info', flushId);
        await loadData();
      } else {
        addToast(data.detail || `Could not unload '${modelId}'`, 'warning');
      }
    } catch (err: any) {
      addToast(`Flush failed: ${err.message}`, 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const handleFlushIdle = async () => {
    setActionLoading('flush_idle');
    try {
      const res = await fetch('/v1/admin/residency/flush-idle', {
        method: 'POST',
        headers: getAdminHeaders(),
      });
      const data = await res.json().catch(() => ({}));
      if (res.ok) {
        const count = data.results?.length || 0;
        const flushIdleId = `flush-idle-${Math.floor(Date.now() / 8000)}`;
        addToast(`Flushed ${count} idle models from memory`, 'info', flushIdleId);
        await loadData();
      } else {
        addToast(data.detail || 'Could not flush idle models', 'warning');
      }
    } catch (err: any) {
      addToast(`Flush failed: ${err.message}`, 'error');
    } finally {
      setActionLoading(null);
    }
  };

  const maxRequests = Math.max(...timeseries.map((p) => p.requests), 1);
  const totalVolume = timeseries.reduce((sum, p) => sum + p.requests, 0);
  const totalErrors = timeseries.reduce((sum, p) => sum + p.errors, 0);
  const totalSuccess = Math.max(0, totalVolume - totalErrors);
  const successRate = totalVolume > 0 ? ((totalSuccess / totalVolume) * 100).toFixed(1) : '100';

  const xTicks = useMemo(() => {
    if (timeseries.length === 0) return [];
    if (timeseries.length <= 5) {
      return timeseries.map((pt, idx) => {
        const [datePart, timePart] = pt.bucket_label.split(' ');
        const [, m, d] = (datePart || '').split('-');
        const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        const monthStr = monthNames[parseInt(m, 10) - 1] || m;
        return {
          index: idx,
          formatted: `${monthStr} ${parseInt(d, 10)} ${timePart || ''}`.trim(),
        };
      });
    }
    const count = 5;
    const step = (timeseries.length - 1) / (count - 1);
    const result: Array<{ index: number; formatted: string }> = [];
    let lastDate: string | undefined = undefined;

    for (let i = 0; i < count; i++) {
      const idx = Math.min(Math.round(i * step), timeseries.length - 1);
      const pt = timeseries[idx];
      if (pt) {
        const [datePart, timePart] = pt.bucket_label.split(' ');
        const [, m, d] = (datePart || '').split('-');
        const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        const monthStr = monthNames[parseInt(m, 10) - 1] || m;

        let label = timePart || pt.bucket_label;
        if (i === 0 || i === count - 1 || datePart !== lastDate) {
          label = `${monthStr} ${parseInt(d, 10)} ${timePart || ''}`.trim();
          lastDate = datePart;
        }
        result.push({ index: idx, formatted: label });
      }
    }
    return result;
  }, [timeseries]);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Toast Notifications Overlay */}
      {toasts.length > 0 && (
        <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 max-w-sm pointer-events-none">
          {toasts.map((t) => (
            <div
              key={t.id}
              className={`p-3 rounded-xl shadow-xl border text-xs flex items-center justify-between gap-3 pointer-events-auto transition-all ${
                t.level === 'error'
                  ? 'bg-rose-950/90 text-rose-200 border-rose-500/30'
                  : t.level === 'warning'
                  ? 'bg-amber-950/90 text-amber-200 border-amber-500/30'
                  : 'bg-indigo-950/90 text-indigo-200 border-indigo-500/30'
              }`}
            >
              <div className="flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 shrink-0 text-indigo-400" />
                <span>{t.message}</span>
              </div>
              <button
                onClick={() => setToasts((prev) => prev.filter((item) => item.id !== t.id))}
                className="text-slate-400 hover:text-slate-200 p-0.5"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Header & Filter Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-100">Developer Dashboard Overview</h1>
          <p className="text-sm text-slate-400">
            Live API usage, latency percentiles, throughput, and warm engine states
          </p>
        </div>

        <div className="flex items-center gap-3">
          <div className="flex bg-slate-950 border border-slate-800 rounded-lg p-0.5">
            {(['1h', '24h', '7d', '30d'] as const).map((w) => (
              <button
                key={w}
                onClick={() => setTimeWindow(w)}
                className={`px-3 py-1 text-xs font-medium rounded-md transition-all ${
                  timeWindow === w
                    ? 'bg-indigo-600 text-white shadow-sm'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {w}
              </button>
            ))}
          </div>

          <button
            onClick={loadData}
            disabled={loading}
            className="p-2 bg-slate-950 border border-slate-800 hover:border-slate-700 text-slate-400 hover:text-slate-200 rounded-lg transition-all"
            title="Refresh Metrics"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-indigo-400' : ''}`} />
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-300 text-sm flex items-center gap-3">
          <AlertTriangle className="w-5 h-5 text-rose-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Top Cards Grid */}
      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
        {/* Total Requests & Success Rate */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-5 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Total Requests</span>
            <Activity className="w-4 h-4 text-indigo-400" />
          </div>
          <div className="text-2xl font-bold text-slate-100">{summary?.total_requests?.toLocaleString() || 0}</div>
          <div className="text-xs text-slate-500 flex items-center justify-between">
            <span>Success Rate</span>
            <span className="text-emerald-400 font-medium">
              {summary ? `${(summary.success_rate * 100).toFixed(1)}%` : '100%'}
            </span>
          </div>
        </div>

        {/* Errors & 429/503 Gate */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-5 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Errors & Rate Limits</span>
            <AlertTriangle className="w-4 h-4 text-amber-400" />
          </div>
          <div className="text-2xl font-bold text-slate-100">{summary?.error_count || 0}</div>
          <div className="text-xs text-slate-500 flex items-center gap-2">
            <span className="bg-amber-500/10 text-amber-400 px-1.5 py-0.5 rounded border border-amber-500/20 font-mono">
              429: {summary?.status_429_count || 0}
            </span>
            <span className="bg-rose-500/10 text-rose-400 px-1.5 py-0.5 rounded border border-rose-500/20 font-mono">
              503: {summary?.status_503_count || 0}
            </span>
          </div>
        </div>

        {/* Latency p50 / p95 */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-5 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Latency (p50 / p95)</span>
            <Clock className="w-4 h-4 text-sky-400" />
          </div>
          <div className="text-2xl font-bold text-slate-100">
            {summary?.p50_latency_ms || 0}{' '}
            <span className="text-xs font-normal text-slate-400">/ {summary?.p95_latency_ms || 0} ms</span>
          </div>
          <div className="text-xs text-slate-500 flex items-center justify-between">
            <span>Avg Stream TTFB</span>
            <span className="text-sky-400 font-mono">{summary?.avg_ttfb_ms || 0} ms</span>
          </div>
        </div>

        {/* Usage Units */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-5 space-y-2">
          <div className="flex items-center justify-between text-xs text-slate-400">
            <span>Units Consumed</span>
            <Zap className="w-4 h-4 text-emerald-400" />
          </div>
          <div className="text-base font-semibold text-slate-200">
            LLM: <span className="text-emerald-400 font-mono">{(summary?.llm_tokens_in || 0) + (summary?.llm_tokens_out || 0)}</span> tok
          </div>
          <div className="text-xs text-slate-500 flex items-center justify-between">
            <span>STT / TTS Audio</span>
            <span className="text-slate-300 font-mono">
              {((summary?.stt_audio_seconds_in || 0) + (summary?.tts_audio_seconds_out || 0)).toFixed(1)}s
            </span>
          </div>
        </div>
      </div>

      {/* STT Fallback Counters by Reason */}
      <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2 bg-amber-500/10 border border-amber-500/20 rounded-lg text-amber-400">
            <Radio className="w-4 h-4" />
          </div>
          <div>
            <h4 className="text-xs font-semibold text-slate-200">Streaming STT Fallback Counters</h4>
            <p className="text-[11px] text-slate-500">Explicit telemetry tracking engine substitutions and unavailable models</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3 text-xs">
          <div className="px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg flex items-center gap-2">
            <span className="text-slate-400">Model Unavailable:</span>
            <span className="font-mono font-semibold text-rose-400">{summary?.stt_fallbacks?.model_unavailable || 0}</span>
          </div>
          <div className="px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg flex items-center gap-2">
            <span className="text-slate-400">Empty Model Rejected:</span>
            <span className="font-mono font-semibold text-amber-400">{summary?.stt_fallbacks?.empty_model || 0}</span>
          </div>
          <div className="px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg flex items-center gap-2">
            <span className="text-slate-400">Service Unreachable:</span>
            <span className="font-mono font-semibold text-sky-400">{summary?.stt_fallbacks?.service_unreachable || 0}</span>
          </div>
          <div className="px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg flex items-center gap-2">
            <span className="text-slate-400">Stream Timeouts:</span>
            <span className="font-mono font-semibold text-slate-300">{summary?.stt_fallbacks?.timeout || 0}</span>
          </div>
        </div>
      </div>

      {/* Activity Timeseries Bar Chart */}
      <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-5">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4 border-b border-slate-900 pb-4">
          <div>
            <div className="flex items-center gap-2.5">
              <h3 className="font-semibold text-slate-200 text-sm">Request Volume Over Time</h3>
              <span className="text-[11px] px-2 py-0.5 rounded-full bg-slate-900 border border-slate-800 text-slate-400 font-mono">
                {timeWindow}
              </span>
            </div>
            <p className="text-xs text-slate-500 mt-0.5">Hourly activity breakdown (zero-filled continuous interval)</p>
          </div>

          {/* Legend and Key Summary Metrics */}
          <div className="flex flex-wrap items-center gap-2.5 text-xs">
            {/* Legend pills */}
            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-slate-900/90 border border-slate-800 rounded-lg">
              <span className="w-2.5 h-2.5 rounded-sm bg-indigo-500 inline-block shadow-sm" />
              <span className="text-slate-400 text-[11px]">Successful:</span>
              <span className="font-mono font-semibold text-indigo-300">{totalSuccess}</span>
            </div>

            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-slate-900/90 border border-slate-800 rounded-lg">
              <span className="w-2.5 h-2.5 rounded-sm bg-amber-500 inline-block shadow-sm" />
              <span className="text-slate-400 text-[11px]">Errors:</span>
              <span className="font-mono font-semibold text-amber-300">{totalErrors}</span>
            </div>

            <div className="flex items-center gap-1.5 px-2.5 py-1 bg-slate-900/90 border border-slate-800 rounded-lg">
              <span className="w-2.5 h-0.5 bg-slate-700 inline-block rounded-full" />
              <span className="text-slate-500 text-[11px]">Idle (0)</span>
            </div>

            <div className="h-4 w-px bg-slate-800 hidden sm:block" />

            {/* Peak & Total Metrics */}
            <div className="flex items-center gap-2.5 text-[11px] text-slate-400 bg-slate-900/50 px-3 py-1 rounded-lg border border-slate-900">
              <div>
                Peak: <span className="text-slate-200 font-mono font-semibold">{totalVolume > 0 ? `${maxRequests} req/h` : '0 req/h'}</span>
              </div>
              <span className="text-slate-700">•</span>
              <div>
                Total: <span className="text-slate-200 font-mono font-semibold">{totalVolume} reqs</span>
              </div>
              {totalVolume > 0 && (
                <>
                  <span className="text-slate-700">•</span>
                  <div>
                    Success: <span className={`font-mono font-semibold ${Number(successRate) >= 95 ? 'text-emerald-400' : Number(successRate) >= 80 ? 'text-amber-400' : 'text-rose-400'}`}>{successRate}%</span>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>

        {timeseries.length === 0 ? (
          <div className="h-44 flex flex-col items-center justify-center text-xs text-slate-400 border border-dashed border-slate-700 rounded-lg gap-1.5 bg-slate-900/40">
            <Activity className="w-5 h-5 text-slate-500 mb-1" />
            <span className="font-medium">No data available for this window ({timeWindow})</span>
            <span className="text-[11px] text-slate-500">Send requests via API or Playgrounds to see live volume</span>
          </div>
        ) : (
          <div className="space-y-2">
            {/* Plot Area with Y-axis */}
            <div className="flex gap-3 pt-6">
              {/* Y-axis Labels */}
              <div className="w-9 h-48 flex flex-col justify-between items-end text-[10px] font-mono text-slate-500 shrink-0 pb-1 select-none">
                <span>{maxRequests}</span>
                <span>{Math.round(maxRequests * 0.75)}</span>
                <span>{Math.round(maxRequests * 0.5)}</span>
                <span>{Math.round(maxRequests * 0.25)}</span>
                <span>0</span>
              </div>

              {/* Chart Grid & Bars Container */}
              <div className="flex-1 relative h-48">
                {/* Horizontal Gridlines */}
                <div className="absolute inset-0 flex flex-col justify-between pointer-events-none pb-1">
                  <div className="border-t border-slate-800/80 border-dashed w-full" />
                  <div className="border-t border-slate-800/50 border-dashed w-full" />
                  <div className="border-t border-slate-800/50 border-dashed w-full" />
                  <div className="border-t border-slate-800/50 border-dashed w-full" />
                  <div className="border-t border-slate-800 w-full" />
                </div>

                {/* Bars */}
                <div className="absolute inset-0 flex items-end gap-1 pb-1">
                  {timeseries.map((pt, i) => {
                    const heightPct = pt.requests > 0 ? Math.max((pt.requests / maxRequests) * 100, 6) : 0;
                    const errCount = pt.errors;
                    const succCount = Math.max(0, pt.requests - pt.errors);
                    const isTooltipLeft = i < 3;
                    const isTooltipRight = i >= timeseries.length - 3;

                    return (
                      <div
                        key={i}
                        className="flex-1 min-w-[14px] h-full flex flex-col items-center justify-end group relative hover:bg-slate-900/40 rounded transition-colors"
                      >
                        {/* Hover Tooltip */}
                        <div
                          className={`absolute bottom-full mb-3 hidden group-hover:flex flex-col z-30 pointer-events-none ${
                            isTooltipLeft
                              ? 'left-0'
                              : isTooltipRight
                              ? 'right-0'
                              : 'left-1/2 -translate-x-1/2'
                          }`}
                        >
                          <div className="bg-slate-900/95 backdrop-blur-md border border-slate-700/80 text-slate-200 text-xs px-3 py-2 rounded-lg shadow-2xl whitespace-nowrap space-y-1.5 min-w-[160px]">
                            <div className="font-semibold text-slate-100 border-b border-slate-800 pb-1 flex items-center justify-between gap-3">
                              <span>{pt.bucket_label}</span>
                              <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-slate-800 text-slate-300">
                                {pt.requests} {pt.requests === 1 ? 'req' : 'reqs'}
                              </span>
                            </div>
                            {pt.requests === 0 ? (
                              <div className="text-[11px] text-slate-500 italic py-0.5">No requests recorded (Idle)</div>
                            ) : (
                              <>
                                <div className="grid grid-cols-2 gap-x-3 gap-y-1 text-[11px]">
                                  <div className="flex items-center gap-1.5">
                                    <span className="w-2 h-2 rounded-sm bg-indigo-500 shrink-0" />
                                    <span className="text-slate-400">Success:</span>
                                    <span className="font-mono text-indigo-300 font-semibold">{succCount}</span>
                                  </div>
                                  <div className="flex items-center gap-1.5">
                                    <span className="w-2 h-2 rounded-sm bg-amber-500 shrink-0" />
                                    <span className="text-slate-400">Errors:</span>
                                    <span className="font-mono text-amber-300 font-semibold">{errCount}</span>
                                  </div>
                                  <div className="flex items-center gap-1.5 text-slate-400">
                                    <span>p50:</span>
                                    <span className="font-mono text-slate-200">{pt.p50_latency_ms}ms</span>
                                  </div>
                                  <div className="flex items-center gap-1.5 text-slate-400">
                                    <span>p95:</span>
                                    <span className="font-mono text-slate-200">{pt.p95_latency_ms}ms</span>
                                  </div>
                                </div>
                                {(pt.tokens_in > 0 || pt.tokens_out > 0) && (
                                  <div className="text-[10px] text-slate-400 border-t border-slate-800/80 pt-1 flex justify-between">
                                    <span>Tokens:</span>
                                    <span className="font-mono text-emerald-400 font-semibold">{Math.round(pt.tokens_in + pt.tokens_out)}</span>
                                  </div>
                                )}
                                {(pt.audio_seconds_in > 0 || pt.audio_seconds_out > 0) && (
                                  <div className="text-[10px] text-slate-400 border-t border-slate-800/80 pt-1 flex justify-between">
                                    <span>Audio:</span>
                                    <span className="font-mono text-sky-400 font-semibold">{(pt.audio_seconds_in + pt.audio_seconds_out).toFixed(1)}s</span>
                                  </div>
                                )}
                              </>
                            )}
                          </div>
                        </div>

                        {/* Bar Graphic */}
                        {pt.requests === 0 ? (
                          <div className="w-full h-[3px] bg-slate-800/80 rounded-full group-hover:bg-slate-600 transition-colors" />
                        ) : (
                          <div
                            style={{ height: `${heightPct}%` }}
                            className="w-full relative flex flex-col justify-end rounded-t overflow-visible transition-all shadow-sm group-hover:brightness-110"
                          >
                            {/* Value label directly above bar */}
                            <div className="absolute -top-5 left-1/2 -translate-x-1/2 text-[10px] font-mono font-semibold text-slate-300 group-hover:text-white transition-colors pointer-events-none select-none whitespace-nowrap">
                              {pt.requests}
                            </div>

                            {/* Stacked bar segments container */}
                            <div className="w-full h-full flex flex-col justify-end rounded-t overflow-hidden">
                              {/* Error segment (top) */}
                              {errCount > 0 && (
                                <div
                                  style={{ height: `${(errCount / pt.requests) * 100}%` }}
                                  className="w-full bg-amber-500 hover:bg-amber-400 transition-colors"
                                />
                              )}
                              {/* Success segment (bottom) */}
                              {succCount > 0 && (
                                <div
                                  style={{ height: `${(succCount / pt.requests) * 100}%` }}
                                  className="w-full bg-indigo-500 hover:bg-indigo-400 transition-colors"
                                />
                              )}
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* X-axis Timeline Labels */}
            {timeseries.length > 0 && (
              <div className="ml-12 mt-1.5 flex justify-between text-[10px] text-slate-500 font-mono select-none">
                {xTicks.map((tick, idx) => (
                  <span
                    key={idx}
                    className={
                      idx === 0
                        ? 'text-left'
                        : idx === xTicks.length - 1
                        ? 'text-right'
                        : 'text-center'
                    }
                  >
                    {tick.formatted}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      {/* Bottom Grid: Warm Engines & Model Activity */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Warm Engines & Memory */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-slate-200 text-sm">Warm Engines & Memory</h3>
              <p className="text-xs text-slate-500">Live resident models in process memory</p>
            </div>
            <div className="flex items-center gap-2">
              <button
                onClick={handleFlushIdle}
                disabled={actionLoading === 'flush_idle'}
                className="px-2.5 py-1 text-xs font-medium rounded-lg bg-slate-900 border border-slate-800 hover:border-slate-700 text-slate-300 hover:text-slate-100 flex items-center gap-1.5 transition-all disabled:opacity-50"
                title="Unload all idle models"
              >
                <Trash2 className="w-3 h-3 text-rose-400" />
                <span>Flush all idle</span>
              </button>
              <Server className="w-4 h-4 text-slate-400" />
            </div>
          </div>

          {/* System Memory Bar */}
          {systemMemory && (
            <div className="space-y-1.5 bg-slate-900/60 border border-slate-800/80 p-3 rounded-lg text-xs">
              <div className="flex justify-between text-slate-400">
                <span>System RAM (Measured)</span>
                <span className="font-mono">
                  {systemMemory.used_mb} MB / {systemMemory.total_mb} MB ({systemMemory.percent}%)
                </span>
              </div>
              <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                <div
                  className={`h-full rounded-full transition-all ${
                    systemMemory.percent > 85 ? 'bg-rose-500' : systemMemory.percent > 70 ? 'bg-amber-500' : 'bg-indigo-500'
                  }`}
                  style={{ width: `${Math.min(systemMemory.percent, 100)}%` }}
                />
              </div>
            </div>
          )}

          {/* Models Residency List */}
          <div className="space-y-2">
            {(!residency?.models || residency.models.length === 0) ? (
              <div className="h-32 flex flex-col items-center justify-center text-xs text-slate-500 border border-dashed border-slate-800 rounded-lg gap-1">
                <Cpu className="w-4 h-4 text-slate-600 mb-1" />
                <span>No models currently loaded in memory</span>
                <span className="text-[11px] text-slate-600">Models load automatically on first request</span>
              </div>
            ) : (
              residency.models.map((m) => {
                const isIdle = m.state === 'idle';

                return (
                  <div
                    key={m.id}
                    className="flex items-center justify-between p-3 bg-slate-900 border border-slate-800/60 rounded-lg text-xs"
                  >
                    <div className="flex items-center gap-2.5">
                      <Cpu className="w-4 h-4 text-indigo-400" />
                      <div>
                        <div className="font-medium text-slate-200">{m.name || m.id}</div>
                        <div className="text-[10px] text-slate-500 tracking-wide">
                          {m.engine} · {m.memory_rss_mb > 0 ? `${m.memory_rss_mb} MB (measured)` : 'Active'}
                        </div>
                      </div>
                    </div>

                    <div className="flex items-center gap-2.5">
                      {m.is_protected && (
                        <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold border bg-indigo-500/10 text-indigo-400 border-indigo-500/20 flex items-center gap-1">
                          <Shield className="w-2.5 h-2.5" />
                          PROTECTED
                        </span>
                      )}
                      <span
                        className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border ${
                          m.state === 'active'
                            ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                            : isIdle
                            ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                            : 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                        }`}
                      >
                        {m.state.toUpperCase()}
                      </span>

                      <button
                        onClick={() => handleFlushModel(m.id, m.is_protected)}
                        disabled={actionLoading === m.id}
                        className="px-2 py-1 rounded bg-slate-800 hover:bg-rose-500/20 hover:text-rose-300 text-slate-400 text-[11px] border border-slate-700/60 transition-all disabled:opacity-50"
                        title="Flush model from memory"
                      >
                        {actionLoading === m.id ? (
                          <RefreshCw className="w-3 h-3 animate-spin text-rose-400" />
                        ) : (
                          'Flush'
                        )}
                      </button>
                    </div>
                  </div>
                );
              })
            )}
          </div>
        </div>

        {/* Model Activity Breakdown Table */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-slate-200 text-sm">Model Activity Breakdown</h3>
              <p className="text-xs text-slate-500">Telemetry, percentiles, and top keys</p>
            </div>
            <Activity className="w-4 h-4 text-slate-400" />
          </div>

          {modelActivity.length === 0 ? (
            <div className="h-48 flex items-center justify-center text-xs text-slate-500 border border-dashed border-slate-800 rounded-lg">
              No model telemetry recorded in selected window ({timeWindow}).
            </div>
          ) : (
            <div className="space-y-2 max-h-[360px] overflow-y-auto pr-1">
              {modelActivity.map((item) => (
                <div
                  key={item.model_id}
                  className="p-3 bg-slate-900 border border-slate-800/60 rounded-lg space-y-2 text-xs"
                >
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2">
                      <span className="font-semibold text-slate-200 font-mono">{item.display_name || item.model_id}</span>
                      <span className="text-[10px] text-slate-500 uppercase">{item.engine}</span>
                    </div>
                    <span className="text-slate-300 font-mono font-medium">
                      {item.requests} reqs {item.errors > 0 && <span className="text-rose-400">({item.errors} err)</span>}
                    </span>
                  </div>

                  <div className="grid grid-cols-2 sm:grid-cols-3 gap-2 text-[11px] text-slate-400 pt-1 border-t border-slate-800/50">
                    <div>
                      <span className="text-slate-500">Latency: </span>
                      <span className="text-slate-300 font-mono">{item.p50_latency_ms} / {item.p95_latency_ms} ms</span>
                    </div>
                    <div>
                      <span className="text-slate-500">TTFB: </span>
                      <span className="text-sky-400 font-mono">{item.ttfb_ms !== null && item.ttfb_ms !== undefined ? `${item.ttfb_ms} ms` : '—'}</span>
                    </div>
                    <div>
                      <span className="text-slate-500">Memory: </span>
                      <span className="text-slate-300 font-mono">{item.memory_mb > 0 ? `${item.memory_mb} MB` : '—'}</span>
                    </div>
                  </div>

                  {item.top_keys && item.top_keys.length > 0 && (
                    <div className="text-[10px] text-slate-500 flex flex-wrap items-center gap-1.5 pt-0.5">
                      <span className="text-slate-600 font-medium">Top Keys:</span>
                      {item.top_keys.map((k) => (
                        <span key={k.name} className="bg-slate-800/80 text-slate-300 px-1.5 py-0.5 rounded font-mono">
                          {k.name} ({k.requests})
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
