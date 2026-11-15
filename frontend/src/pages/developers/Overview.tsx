import React, { useState, useEffect } from 'react';
import { Activity, Clock, Cpu, RefreshCw, Zap, Server, AlertTriangle } from 'lucide-react';
import { fetchAdminJson } from './api';

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

interface ModelStatus {
  id: string;
  name: string;
  category: string;
  provider: string;
  size_mb: number;
  status: 'warm' | 'idle';
  memory_mb: number;
}

interface ModelsStatusResponse {
  models: ModelStatus[];
  system_memory: {
    total_mb: number;
    available_mb: number;
    used_mb: number;
  };
}

export const Overview: React.FC = () => {
  const [timeWindow, setTimeWindow] = useState<'1h' | '24h' | '7d' | '30d'>('24h');
  const [summary, setSummary] = useState<UsageSummary | null>(null);
  const [timeseries, setTimeseries] = useState<TimeseriesPoint[]>([]);
  const [modelsStatus, setModelsStatus] = useState<ModelsStatusResponse | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [sumData, tsData, modData] = await Promise.all([
        fetchAdminJson<UsageSummary>(`/v1/admin/usage/summary?since=${timeWindow}&group_by=model`),
        fetchAdminJson<{ series: TimeseriesPoint[] }>(`/v1/admin/usage/timeseries?interval=hour&since=${timeWindow}`),
        fetchAdminJson<ModelsStatusResponse>('/v1/admin/models/status'),
      ]);
      setSummary(sumData);
      setTimeseries(tsData.series || []);
      setModelsStatus(modData);
    } catch (err: any) {
      setError(err?.message || 'Failed to load telemetry metrics');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const timer = setInterval(loadData, 30000);
    return () => clearInterval(timer);
  }, [timeWindow]);

  const maxRequests = Math.max(...timeseries.map((p) => p.requests), 1);

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header & Filter Controls */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-100">Developer Dashboard Overview</h1>
          <p className="text-sm text-slate-400">Live API usage, latency percentiles, throughput, and warm engine states</p>
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
            {summary?.p50_latency_ms || 0} <span className="text-xs font-normal text-slate-400">/ {summary?.p95_latency_ms || 0} ms</span>
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

      {/* Activity Timeseries Bar Chart */}
      <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4">
        <div className="flex items-center justify-between">
          <div>
            <h3 className="font-semibold text-slate-200 text-sm">Request Volume Over Time</h3>
            <p className="text-xs text-slate-500">Hourly activity breakdown</p>
          </div>
        </div>

        {timeseries.length === 0 ? (
          <div className="h-40 flex items-center justify-center text-xs text-slate-500 border border-dashed border-slate-800 rounded-lg">
            No telemetry events recorded in selected time window ({timeWindow}).
          </div>
        ) : (
          <div className="h-44 flex items-end gap-1.5 pt-6 pb-2 border-b border-slate-800 overflow-x-auto">
            {timeseries.map((pt, i) => {
              const heightPct = Math.max((pt.requests / maxRequests) * 100, 6);
              const hasErrors = pt.errors > 0;
              return (
                <div
                  key={i}
                  className="flex-1 min-w-[14px] flex flex-col items-center justify-end group relative"
                >
                  {/* Tooltip */}
                  <div className="absolute -top-12 hidden group-hover:flex flex-col items-center z-20 pointer-events-none">
                    <div className="bg-slate-900 border border-slate-700 text-slate-200 text-[10px] px-2 py-1 rounded shadow-lg whitespace-nowrap">
                      {pt.bucket_label}: {pt.requests} req ({pt.errors} err) · p50 {pt.p50_latency_ms}ms
                    </div>
                  </div>
                  {/* Bar */}
                  <div
                    style={{ height: `${heightPct}%` }}
                    className={`w-full rounded-t transition-all ${
                      hasErrors ? 'bg-amber-500 hover:bg-amber-400' : 'bg-indigo-600 hover:bg-indigo-500'
                    }`}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Bottom Grid: Models Warm State & Model Breakdown */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Warm Engines & Memory */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-slate-200 text-sm">Warm Engines & Memory</h3>
              <p className="text-xs text-slate-500">Local AI inference engine memory allocation</p>
            </div>
            <Server className="w-4 h-4 text-slate-400" />
          </div>

          {/* System Memory Bar */}
          {modelsStatus?.system_memory && (
            <div className="space-y-1.5 bg-slate-900/60 border border-slate-800/80 p-3 rounded-lg text-xs">
              <div className="flex justify-between text-slate-400">
                <span>System RAM</span>
                <span>
                  {modelsStatus.system_memory.used_mb} MB / {modelsStatus.system_memory.total_mb} MB
                </span>
              </div>
              <div className="w-full h-2 bg-slate-800 rounded-full overflow-hidden">
                <div
                  className="h-full bg-indigo-500 rounded-full"
                  style={{
                    width: `${Math.min(
                      (modelsStatus.system_memory.used_mb / modelsStatus.system_memory.total_mb) * 100,
                      100
                    )}%`,
                  }}
                />
              </div>
            </div>
          )}

          {/* Models Status Pills List */}
          <div className="space-y-2">
            {modelsStatus?.models.map((m) => (
              <div
                key={m.id}
                className="flex items-center justify-between p-3 bg-slate-900 border border-slate-800/60 rounded-lg text-xs"
              >
                <div className="flex items-center gap-2.5">
                  <Cpu className="w-4 h-4 text-indigo-400" />
                  <div>
                    <div className="font-medium text-slate-200">{m.name}</div>
                    <div className="text-[10px] text-slate-500 uppercase tracking-wide">
                      {m.category} · {m.provider}
                    </div>
                  </div>
                </div>

                <div className="flex items-center gap-3">
                  <span className="text-slate-400 font-mono">{m.size_mb} MB</span>
                  <span
                    className={`px-2 py-0.5 rounded-full text-[10px] font-semibold border ${
                      m.status === 'warm'
                        ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                        : 'bg-slate-800 text-slate-400 border-slate-700'
                    }`}
                  >
                    {m.status.toUpperCase()}
                  </span>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Model Usage Breakdown */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4">
          <div className="flex items-center justify-between">
            <div>
              <h3 className="font-semibold text-slate-200 text-sm">Model Activity Breakdown</h3>
              <p className="text-xs text-slate-500">Volume and latency per model</p>
            </div>
            <Activity className="w-4 h-4 text-slate-400" />
          </div>

          {(!summary?.groups || summary.groups.length === 0) ? (
            <div className="h-48 flex items-center justify-center text-xs text-slate-500 border border-dashed border-slate-800 rounded-lg">
              No model usage logged yet.
            </div>
          ) : (
            <div className="space-y-2">
              {summary.groups.map((g) => (
                <div
                  key={g.name}
                  className="p-3 bg-slate-900 border border-slate-800/60 rounded-lg space-y-2 text-xs"
                >
                  <div className="flex items-center justify-between">
                    <span className="font-medium text-slate-200 font-mono">{g.name}</span>
                    <span className="text-slate-400 font-semibold">{g.requests} reqs</span>
                  </div>
                  <div className="flex items-center justify-between text-slate-500 text-[11px]">
                    <span>Avg Latency: <span className="text-slate-300 font-mono">{g.avg_latency_ms} ms</span></span>
                    <span>Errors: <span className={g.errors > 0 ? 'text-amber-400 font-medium' : 'text-slate-400'}>{g.errors}</span></span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
