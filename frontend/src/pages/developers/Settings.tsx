import React, { useState, useEffect } from 'react';
import { Save, AlertTriangle, Check } from 'lucide-react';
import { fetchAdminJson } from './api';

interface DashboardSettings {
  base_url: string;
  default_rate_limit: number;
  default_max_concurrent: number;
  usage_retention_days: number;
  updated_at?: number;
}

export const Settings: React.FC = () => {
  const defaultUrl = typeof window !== 'undefined' && window.location.hostname && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1'
    ? window.location.origin
    : 'http://127.0.0.1:8000';
  const [baseUrl, setBaseUrl] = useState<string>(defaultUrl);
  const [defaultRateLimit, setDefaultRateLimit] = useState<number>(60);
  const [defaultMaxConcurrent, setDefaultMaxConcurrent] = useState<number>(5);
  const [retentionDays, setRetentionDays] = useState<number>(30);
  const [sttStreamEnabled, setSttStreamEnabled] = useState<boolean>(true);
  const [streamingBridgeEnabled, setStreamingBridgeEnabled] = useState<boolean>(false);

  const [_loading, setLoading] = useState<boolean>(true);
  const [saving, setSaving] = useState<boolean>(false);
  const [saveSuccess, setSaveSuccess] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const loadSettings = async () => {
      setLoading(true);
      setError(null);
      try {
        const data = await fetchAdminJson<DashboardSettings>('/v1/admin/settings/dashboard');
        if (data.base_url) {
          const isRemoteClient =
            typeof window !== 'undefined' &&
            window.location.hostname &&
            window.location.hostname !== 'localhost' &&
            window.location.hostname !== '127.0.0.1';
          const isLoopback =
            data.base_url.includes('127.0.0.1') || data.base_url.includes('localhost');
          if (isRemoteClient && isLoopback) {
            setBaseUrl(window.location.origin);
          } else {
            setBaseUrl(data.base_url);
          }
        }
        if (data.default_rate_limit) setDefaultRateLimit(data.default_rate_limit);
        if (data.default_max_concurrent) setDefaultMaxConcurrent(data.default_max_concurrent);
        if (data.usage_retention_days) setRetentionDays(data.usage_retention_days);

        try {
          const sttData = await fetchAdminJson<any>('/v1/admin/settings/stt');
          if (sttData) {
            if (sttData.stt_stream_enabled !== undefined) setSttStreamEnabled(Boolean(sttData.stt_stream_enabled));
            if (sttData.stt_stream_native_enabled !== undefined) {
              setStreamingBridgeEnabled(Boolean(sttData.stt_stream_native_enabled));
            } else if (sttData.streaming_bridge_enabled !== undefined) {
              setStreamingBridgeEnabled(Boolean(sttData.streaming_bridge_enabled));
            }
          }
        } catch {}
      } catch (err: any) {
        setError(err?.message || 'Failed to load dashboard settings');
      } finally {
        setLoading(false);
      }
    };

    loadSettings();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setSaveSuccess(false);
    setError(null);

    try {
      await fetchAdminJson<DashboardSettings>('/v1/admin/settings/dashboard', {
        method: 'PUT',
        body: JSON.stringify({
          base_url: baseUrl.trim(),
          default_rate_limit: Number(defaultRateLimit),
          default_max_concurrent: Number(defaultMaxConcurrent),
          usage_retention_days: Number(retentionDays),
        }),
      });

      try {
        await fetchAdminJson('/v1/admin/settings/stt', {
          method: 'PUT',
          body: JSON.stringify({
            stt_stream_enabled: sttStreamEnabled,
            stt_stream_native_enabled: streamingBridgeEnabled,
            streaming_bridge_enabled: streamingBridgeEnabled,
          }),
        });
      } catch (sttErr) {
        console.warn('Failed to update STT admin settings:', sttErr);
      }

      setSaveSuccess(true);
      setTimeout(() => setSaveSuccess(false), 4000);
    } catch (err: any) {
      setError(err?.message || 'Failed to save settings');
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-100">Developer Dashboard Settings</h1>
        <p className="text-sm text-slate-400">
          Configure network gateway address, snippet base URLs, key generation defaults, and retention rules
        </p>
      </div>

      {/* LAN Security Notice */}
      <div className="p-4 bg-amber-500/10 border border-amber-500/20 rounded-xl space-y-2 text-xs text-amber-300">
        <div className="flex items-center gap-2 font-semibold">
          <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
          <span>Local Area Network (LAN) HTTP Security</span>
        </div>
        <p className="text-slate-300 leading-relaxed">
          On a local LAN, HTTP transmits API keys unencrypted across the network switch. This is standard for local
          workstations, but you should restrict API keys to designated client IPs using the <strong>IP Allowlist</strong>{' '}
          (CIDR) setting on each key.
        </p>
      </div>

      {error && (
        <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-300 text-sm">
          {error}
        </div>
      )}

      {saveSuccess && (
        <div className="p-4 bg-emerald-500/10 border border-emerald-500/20 rounded-xl text-emerald-300 text-sm flex items-center gap-2">
          <Check className="w-4 h-4 text-emerald-400" />
          <span>Dashboard settings updated successfully!</span>
        </div>
      )}

      {/* Settings Form */}
      <form onSubmit={handleSave} className="space-y-6">
        {/* Gateway Base URL */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4 text-xs">
          <div>
            <h3 className="font-semibold text-slate-200 text-sm">Snippet Gateway Base URL</h3>
            <p className="text-slate-400 mt-0.5">
              The external or LAN address rendered in copyable code snippets (e.g. <code>http://192.168.1.100:8000</code>).
              Must point to this FastAPI Gateway, <strong>never</strong> to internal LocalAI or local-tts ports.
            </p>
          </div>

          <div>
            <label className="block text-slate-400 mb-1 font-medium">Base URL</label>
            <input
              type="text"
              required
              value={baseUrl}
              onChange={(e) => setBaseUrl(e.target.value)}
              placeholder="http://192.168.1.50:8000"
              className="w-full max-w-lg bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs outline-none focus:border-indigo-500"
            />
          </div>
        </div>

        {/* Default Limits for New Keys */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4 text-xs">
          <div>
            <h3 className="font-semibold text-slate-200 text-sm">New API Key Defaults</h3>
            <p className="text-slate-400 mt-0.5">Default rate limits applied when creating new application keys.</p>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 max-w-lg">
            <div>
              <label className="block text-slate-400 mb-1 font-medium">Default Rate Limit (req/min)</label>
              <input
                type="number"
                min="1"
                max="10000"
                value={defaultRateLimit}
                onChange={(e) => setDefaultRateLimit(Number(e.target.value))}
                className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs outline-none focus:border-indigo-500"
              />
            </div>
            <div>
              <label className="block text-slate-400 mb-1 font-medium">Default Max Concurrency</label>
              <input
                type="number"
                min="1"
                max="100"
                value={defaultMaxConcurrent}
                onChange={(e) => setDefaultMaxConcurrent(Number(e.target.value))}
                className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs outline-none focus:border-indigo-500"
              />
            </div>
          </div>
        </div>

        {/* Retention Policy */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4 text-xs">
          <div>
            <h3 className="font-semibold text-slate-200 text-sm">Data Retention Policy</h3>
            <p className="text-slate-400 mt-0.5">
              Granular request event logs older than this threshold are purged to save disk space on 8 GB local hosts.
            </p>
          </div>

          <div className="max-w-xs">
            <label className="block text-slate-400 mb-1 font-medium">Usage Retention (Days)</label>
            <input
              type="number"
              min="1"
              max="365"
              value={retentionDays}
              onChange={(e) => setRetentionDays(Number(e.target.value))}
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono text-xs outline-none focus:border-indigo-500"
            />
          </div>
        </div>

        {/* Streaming STT Settings */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4 text-xs">
          <div>
            <h3 className="font-semibold text-slate-200 text-sm">Streaming STT Configuration</h3>
            <p className="text-slate-400 mt-0.5">
              Control live native streaming speech recognition services without restarting the gateway.
            </p>
          </div>

          <div className="space-y-4 max-w-lg">
            <div className="flex items-center justify-between p-3 bg-slate-900 border border-slate-800 rounded-lg">
              <div>
                <span className="font-medium text-slate-200 block">Native Streaming STT</span>
                <span className="text-[11px] text-slate-400">Sherpa-ONNX online neural streaming WebSocket route</span>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={sttStreamEnabled}
                  onChange={(e) => setSttStreamEnabled(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-emerald-600"></div>
              </label>
            </div>

            <div className="flex items-center justify-between p-3 bg-slate-900 border border-slate-800 rounded-lg">
              <div>
                <div className="flex items-center gap-1.5">
                  <span className="font-medium text-slate-200 block">Native Streaming STT (:8089)</span>
                  <span className="text-[10px] uppercase font-mono px-1.5 py-0.2 rounded bg-emerald-950/80 border border-emerald-800 text-emerald-400">Runtime Toggle</span>
                </div>
                <span className="text-[11px] text-slate-400">Enables native parakeet-cpp streaming service (port 8089, STT_STREAM_NATIVE_ENABLED)</span>
              </div>
              <label className="relative inline-flex items-center cursor-pointer">
                <input
                  type="checkbox"
                  checked={streamingBridgeEnabled}
                  onChange={(e) => setStreamingBridgeEnabled(e.target.checked)}
                  className="sr-only peer"
                />
                <div className="w-9 h-5 bg-slate-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-white after:border-slate-300 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-indigo-600"></div>
              </label>
            </div>
          </div>
        </div>

        {/* Save Button */}
        <div className="flex justify-end">
          <button
            type="submit"
            disabled={saving}
            className="flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-sm transition-all disabled:opacity-50"
          >
            <Save className="w-4 h-4" />
            <span>{saving ? 'Saving...' : 'Save Settings'}</span>
          </button>
        </div>
      </form>
    </div>
  );
};
