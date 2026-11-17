import React, { useState, useEffect } from 'react';
import { Download, RefreshCw, Filter, ShieldCheck, ChevronLeft, ChevronRight } from 'lucide-react';
import { fetchAdminJson, getAdminApiKey } from './api';

interface RequestLogItem {
  id: number;
  ts: number;
  timestamp_iso: string;
  key_id?: string;
  key_name?: string;
  key_prefix: string;
  profile_id?: string;
  profile_name: string;
  actor_type?: string;
  endpoint: string;
  model: string;
  status: number;
  latency_ms: number;
  ttfb_ms?: number | null;
  input_units: number;
  output_units: number;
  bytes_in: number;
  bytes_out: number;
  error_code?: string;
}

interface RequestLogResponse {
  total: number;
  limit: number;
  offset: number;
  items: RequestLogItem[];
}

export const RequestLog: React.FC = () => {
  const [items, setItems] = useState<RequestLogItem[]>([]);
  const [total, setTotal] = useState<number>(0);
  const [limit] = useState<number>(50);
  const [offset, setOffset] = useState<number>(0);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Filters
  const [filterEndpoint, setFilterEndpoint] = useState<string>('');
  const [filterStatus, setFilterStatus] = useState<string>('');
  const [filterSince, setFilterSince] = useState<string>('24h');
  const [filterActorType, setFilterActorType] = useState<string>('');
  const [filterModel, setFilterModel] = useState<string>('');

  const loadRequests = async () => {
    setLoading(true);
    setError(null);
    try {
      let query = `/v1/admin/usage/requests?limit=${limit}&offset=${offset}`;
      if (filterEndpoint) query += `&endpoint=${encodeURIComponent(filterEndpoint)}`;
      if (filterStatus) query += `&status=${filterStatus}`;
      if (filterSince) query += `&since=${filterSince}`;
      if (filterActorType) query += `&actor_type=${filterActorType}`;
      if (filterModel) query += `&model=${encodeURIComponent(filterModel)}`;

      const data = await fetchAdminJson<RequestLogResponse>(query);
      setItems(data.items || []);
      setTotal(data.total || 0);
    } catch (err: any) {
      setError(err?.message || 'Failed to fetch request log');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadRequests();
  }, [offset, filterEndpoint, filterStatus, filterSince, filterActorType, filterModel]);

  const handleExportCsv = () => {
    const adminKey = getAdminApiKey();
    let url = `/v1/admin/usage/requests?export=csv`;
    if (filterEndpoint) url += `&endpoint=${encodeURIComponent(filterEndpoint)}`;
    if (filterStatus) url += `&status=${filterStatus}`;
    if (filterSince) url += `&since=${filterSince}`;
    if (filterActorType) url += `&actor_type=${filterActorType}`;
    if (filterModel) url += `&model=${encodeURIComponent(filterModel)}`;

    // Direct fetch with download
    fetch(url, {
      credentials: 'include',
      headers: adminKey ? { Authorization: `Bearer ${adminKey}` } : {},
    })
      .then((res) => res.blob())
      .then((blob) => {
        const link = document.createElement('a');
        link.href = window.URL.createObjectURL(blob);
        link.download = `voice-agent-requests-${new Date().toISOString().slice(0, 10)}.csv`;
        document.body.appendChild(link);
        link.click();
        link.remove();
      })
      .catch((err) => alert(`CSV export failed: ${err.message}`));
  };

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-100">API Request Log</h1>
          <p className="text-sm text-slate-400">
            Real-time audit log of network requests, latency metrics, and quota units
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={handleExportCsv}
            className="flex items-center gap-2 px-3 py-2 bg-slate-900 border border-slate-800 hover:border-slate-700 text-slate-300 rounded-lg text-xs font-medium transition-all"
          >
            <Download className="w-3.5 h-3.5 text-indigo-400" />
            <span>Export CSV</span>
          </button>
          <button
            onClick={() => loadRequests()}
            disabled={loading}
            className="p-2 bg-slate-900 border border-slate-800 hover:border-slate-700 text-slate-400 hover:text-slate-200 rounded-lg transition-all"
            title="Refresh Log"
          >
            <RefreshCw className={`w-4 h-4 ${loading ? 'animate-spin text-indigo-400' : ''}`} />
          </button>
        </div>
      </div>

      {/* Privacy Notice Banner */}
      <div className="flex items-center gap-3 p-3.5 bg-indigo-950/30 border border-indigo-500/20 rounded-xl text-xs text-indigo-300">
        <ShieldCheck className="w-4 h-4 text-indigo-400 shrink-0" />
        <span>
          <strong>Privacy Preserved:</strong> Request bodies, conversational prompts, transcripts, audio streams, and
          generated completions are never stored in the database or included in CSV exports.
        </span>
      </div>

      {/* Filter Toolbar */}
      <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 flex flex-wrap items-center gap-3 text-xs">
        <div className="flex items-center gap-2 text-slate-400">
          <Filter className="w-3.5 h-3.5 text-slate-500" />
          <span>Filters:</span>
        </div>

        {/* Time window */}
        <select
          value={filterSince}
          onChange={(e) => {
            setOffset(0);
            setFilterSince(e.target.value);
          }}
          className="bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-slate-200 outline-none"
        >
          <option value="1h">Last 1 hour</option>
          <option value="24h">Last 24 hours</option>
          <option value="7d">Last 7 days</option>
          <option value="30d">Last 30 days</option>
        </select>

        {/* Endpoint filter */}
        <select
          value={filterEndpoint}
          onChange={(e) => {
            setOffset(0);
            setFilterEndpoint(e.target.value);
          }}
          className="bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-slate-200 outline-none"
        >
          <option value="">All Endpoints</option>
          <option value="/v1/chat/completions">Chat Completions (/v1/chat/completions)</option>
          <option value="/v1/audio/transcriptions">STT Batch (/v1/audio/transcriptions)</option>
          <option value="/v1/audio/transcriptions/stream">STT Stream (/v1/audio/transcriptions/stream)</option>
          <option value="/v1/text-to-speech">TTS Stream (/v1/text-to-speech)</option>
          <option value="/v1/voice/stream">Voice Pipeline (/v1/voice/stream)</option>
        </select>

        {/* Status filter */}
        <select
          value={filterStatus}
          onChange={(e) => {
            setOffset(0);
            setFilterStatus(e.target.value);
          }}
          className="bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-slate-200 outline-none"
        >
          <option value="">All Statuses</option>
          <option value="200">200 OK</option>
          <option value="400">400 Bad Request</option>
          <option value="401">401 Unauthorized</option>
          <option value="403">403 Forbidden</option>
          <option value="429">429 Quota Exceeded</option>
          <option value="500">500 Server Error</option>
          <option value="503">503 Service Unavailable</option>
        </select>

        {/* Actor type filter */}
        <select
          value={filterActorType}
          onChange={(e) => {
            setOffset(0);
            setFilterActorType(e.target.value);
          }}
          className="bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-slate-200 outline-none"
        >
          <option value="">All Actors</option>
          <option value="key">API Keys</option>
          <option value="admin">Admin Session</option>
          <option value="system">System / Internal</option>
        </select>

        {/* Model filter */}
        <input
          type="text"
          placeholder="Filter model..."
          value={filterModel}
          onChange={(e) => {
            setOffset(0);
            setFilterModel(e.target.value);
          }}
          className="bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-slate-200 outline-none w-32 placeholder:text-slate-600"
        />

        <div className="ml-auto text-slate-500 font-mono text-[11px]">
          Showing {items.length} of {total} events
        </div>
      </div>

      {error && (
        <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-300 text-sm">
          {error}
        </div>
      )}

      {/* Requests Table */}
      <div className="bg-slate-950 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-xs text-slate-300">
            <thead className="bg-slate-900/80 text-slate-400 text-[11px] font-medium uppercase tracking-wider border-b border-slate-800 select-none">
              <tr>
                <th className="py-3 px-4">Time</th>
                <th className="py-3 px-4">Key / Profile</th>
                <th className="py-3 px-4">Endpoint</th>
                <th className="py-3 px-4">Model</th>
                <th className="py-3 px-4">Status</th>
                <th className="py-3 px-4">Latency</th>
                <th className="py-3 px-4">TTFB</th>
                <th className="py-3 px-4">Units</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-900/60 font-mono text-[11px]">
              {items.length === 0 ? (
                <tr>
                  <td colSpan={8} className="py-12 text-center text-slate-500 font-sans">
                    No requests found matching current filters.
                  </td>
                </tr>
              ) : (
                items.map((item) => {
                  const isOk = item.status < 400;
                  const isRateLimit = item.status === 429;
                  const is5xx = item.status >= 500;

                  const keyLabel =
                    item.key_name && item.key_name !== 'None'
                      ? item.key_name
                      : item.key_prefix && item.key_prefix !== 'None'
                      ? item.key_prefix
                      : item.actor_type === 'admin'
                      ? 'Admin'
                      : 'System (unattributed)';

                  const profileLabel =
                    item.profile_name && item.profile_name !== 'None' && item.profile_name !== '—'
                      ? item.profile_name
                      : item.actor_type
                      ? `[${item.actor_type}]`
                      : '—';

                  const modelLabel = item.model && item.model !== 'None' ? item.model : '—';

                  return (
                    <tr key={item.id} className="hover:bg-slate-900/40 transition-colors">
                      <td className="py-2.5 px-4 text-slate-400 whitespace-nowrap">
                        {item.timestamp_iso ? item.timestamp_iso.replace('T', ' ').replace('Z', '') : '-'}
                      </td>
                      <td className="py-2.5 px-4 whitespace-nowrap">
                        <div className="text-slate-200 font-sans font-medium">{keyLabel}</div>
                        <div className="text-[10px] text-slate-500">{profileLabel}</div>
                      </td>
                      <td className="py-2.5 px-4 font-sans text-slate-300 truncate max-w-[200px]" title={item.endpoint}>
                        {item.endpoint}
                      </td>
                      <td className="py-2.5 px-4 text-slate-400 truncate max-w-[140px]" title={modelLabel}>
                        {modelLabel}
                      </td>
                      <td className="py-2.5 px-4 whitespace-nowrap">
                        <span
                          className={`px-2 py-0.5 rounded font-semibold text-[10px] border ${
                            isOk
                              ? 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'
                              : isRateLimit
                              ? 'bg-amber-500/10 text-amber-400 border-amber-500/20'
                              : is5xx
                              ? 'bg-rose-500/10 text-rose-400 border-rose-500/20'
                              : 'bg-slate-800 text-slate-300 border-slate-700'
                          }`}
                        >
                          {item.status}
                        </span>
                      </td>
                      <td className="py-2.5 px-4 text-slate-300 whitespace-nowrap">{item.latency_ms} ms</td>
                      <td className="py-2.5 px-4 text-slate-400 whitespace-nowrap">
                        {item.ttfb_ms !== null && item.ttfb_ms !== undefined ? `${item.ttfb_ms} ms` : '-'}
                      </td>
                      <td className="py-2.5 px-4 text-slate-400 whitespace-nowrap font-sans text-[10px]">
                        {item.input_units > 0 || item.output_units > 0 ? (
                          <span>
                            {item.input_units} in / {item.output_units} out
                          </span>
                        ) : (
                          '-'
                        )}
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>

        {/* Pagination Bar */}
        <div className="p-3 bg-slate-900/50 border-t border-slate-800 flex items-center justify-between text-xs text-slate-400">
          <div>
            Page {Math.floor(offset / limit) + 1} of {Math.max(1, Math.ceil(total / limit))}
          </div>
          <div className="flex items-center gap-2">
            <button
              disabled={offset === 0}
              onClick={() => setOffset(Math.max(0, offset - limit))}
              className="p-1 rounded bg-slate-900 border border-slate-800 disabled:opacity-40 disabled:pointer-events-none hover:text-slate-200"
            >
              <ChevronLeft className="w-4 h-4" />
            </button>
            <button
              disabled={offset + limit >= total}
              onClick={() => setOffset(offset + limit)}
              className="p-1 rounded bg-slate-900 border border-slate-800 disabled:opacity-40 disabled:pointer-events-none hover:text-slate-200"
            >
              <ChevronRight className="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
