import React, { useEffect, useState } from 'react';
import {
  Cpu,
  Download,
  Trash2,
  AlertTriangle,
  CheckCircle,
  HardDrive,
  Activity,
  Search,
  Loader2,
} from 'lucide-react';
import { GalleryModel, SystemResources, InstallJob } from '../types/api';

const API_BASE = 'http://127.0.0.1:8000';
const API_KEY = 'dev-voice-key-12345';
const AUTH_HEADERS = { Authorization: `Bearer ${API_KEY}` };

export const ModelsRegistry: React.FC = () => {
  const [activeTab, setActiveTab] = useState<'explore' | 'installed'>('explore');
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [models, setModels] = useState<GalleryModel[]>([]);
  const [resources, setResources] = useState<SystemResources | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [activeJobs, setActiveJobs] = useState<Record<string, InstallJob>>({});
  const [warningModel, setWarningModel] = useState<GalleryModel | null>(null);
  const [uninstallingId, setUninstallingId] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<{ type: 'success' | 'error'; msg: string } | null>(null);

  useEffect(() => {
    fetchData();
  }, [categoryFilter]);

  const fetchData = async () => {
    try {
      setLoading(true);
      // Fetch system resources
      const resRes = await fetch(`${API_BASE}/v1/system/resources`, { headers: AUTH_HEADERS });
      if (resRes.ok) {
        const resData: SystemResources = await resRes.json();
        setResources(resData);
      }

      // Fetch gallery models
      const catParam = categoryFilter !== 'all' ? `category=${categoryFilter}` : '';
      const galRes = await fetch(`${API_BASE}/v1/gallery/models?${catParam}`, { headers: AUTH_HEADERS });
      if (galRes.ok) {
        const galData = await galRes.json();
        setModels(galData.models || []);
      }
    } catch (err: any) {
      console.error('Error loading gallery:', err);
    } finally {
      setLoading(false);
    }
  };

  const handleInstallClick = (model: GalleryModel) => {
    if (model.fit_score < 50) {
      setWarningModel(model);
    } else {
      executeInstall(model.id);
    }
  };

  const executeInstall = async (modelId: string) => {
    setWarningModel(null);
    try {
      const res = await fetch(`${API_BASE}/v1/models/install`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
        body: JSON.stringify({ model_id: modelId }),
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Install request failed');
      }
      const job: InstallJob = await res.json();
      setActiveJobs((prev) => ({ ...prev, [modelId]: job }));
      pollJob(job.job_id, modelId);
    } catch (err: any) {
      setFeedback({ type: 'error', msg: err.message });
      setTimeout(() => setFeedback(null), 5000);
    }
  };

  const pollJob = (jobId: string, modelId: string) => {
    const interval = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/v1/models/install/${jobId}`, { headers: AUTH_HEADERS });
        if (res.ok) {
          const updated: InstallJob = await res.json();
          setActiveJobs((prev) => ({ ...prev, [modelId]: updated }));

          if (updated.status === 'completed') {
            clearInterval(interval);
            setFeedback({ type: 'success', msg: `Model ${modelId} installed successfully!` });
            setTimeout(() => setFeedback(null), 5000);
            fetchData();
          } else if (updated.status === 'failed') {
            clearInterval(interval);
            setFeedback({ type: 'error', msg: updated.error_message || 'Installation failed' });
            setTimeout(() => setFeedback(null), 6000);
          }
        }
      } catch {
        clearInterval(interval);
      }
    }, 800);
  };

  const handleUninstall = async (modelId: string) => {
    if (!confirm(`Are you sure you want to uninstall ${modelId}?`)) return;
    setUninstallingId(modelId);
    try {
      const res = await fetch(`${API_BASE}/v1/models/${encodeURIComponent(modelId)}`, {
        method: 'DELETE',
        headers: AUTH_HEADERS,
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Uninstall failed');
      }
      setFeedback({ type: 'success', msg: `Model ${modelId} uninstalled and freed from disk.` });
      setTimeout(() => setFeedback(null), 4000);
      await fetchData();
    } catch (err: any) {
      setFeedback({ type: 'error', msg: err.message });
      setTimeout(() => setFeedback(null), 5000);
    } finally {
      setUninstallingId(null);
    }
  };

  const filteredModels = models.filter((m) => {
    if (activeTab === 'installed' && !m.installed) return false;
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      return (
        m.name.toLowerCase().includes(q) ||
        m.id.toLowerCase().includes(q) ||
        m.description.toLowerCase().includes(q)
      );
    }
    return true;
  });

  const getFitBadge = (score: number) => {
    if (score >= 90) {
      return (
        <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-mono">
          {score}/100 • Excellent Fit
        </span>
      );
    }
    if (score >= 75) {
      return (
        <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-sky-500/10 text-sky-400 border border-sky-500/20 font-mono">
          {score}/100 • Good Fit
        </span>
      );
    }
    if (score >= 50) {
      return (
        <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-amber-500/10 text-amber-400 border border-amber-500/20 font-mono">
          {score}/100 • Tight Fit
        </span>
      );
    }
    return (
      <span className="px-2 py-0.5 rounded text-[11px] font-medium bg-rose-500/10 text-rose-400 border border-rose-500/20 font-mono">
        {score}/100 • Exceeds Budget
      </span>
    );
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header and System Memory Chip */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-100 flex items-center gap-2.5">
            <Cpu className="w-7 h-7 text-indigo-400" />
            Model Registry & Gallery
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Explore and install verified STT, LLM, and TTS models ranked by unified memory fit score.
          </p>
        </div>

        {resources && (
          <div className="flex items-center gap-3 bg-slate-950 border border-slate-800 px-4 py-2 rounded-xl text-xs">
            <div className="flex items-center gap-1.5 text-slate-300">
              <Activity className="w-3.5 h-3.5 text-indigo-400" />
              <span>RAM:</span>
              <span className="font-mono text-indigo-300 font-semibold">
                {(resources.memory.available_mb / 1024).toFixed(1)} GB Avail
              </span>
              <span className="text-slate-500">
                / {(resources.memory.total_mb / 1024).toFixed(1)} GB
              </span>
            </div>
            <div className="h-3 w-px bg-slate-800" />
            <div className="flex items-center gap-1.5 text-slate-300">
              <HardDrive className="w-3.5 h-3.5 text-emerald-400" />
              <span>Disk:</span>
              <span className="font-mono text-emerald-300 font-semibold">
                {resources.disk.free_gb.toFixed(0)} GB Free
              </span>
            </div>
            {resources.hardware?.has_metal_gpu && (
              <>
                <div className="h-3 w-px bg-slate-800" />
                <span className="px-2 py-0.5 rounded bg-purple-500/10 text-purple-400 border border-purple-500/20 font-mono">
                  Metal GPU
                </span>
              </>
            )}
          </div>
        )}
      </div>

      {feedback && (
        <div
          className={`p-3.5 rounded-xl text-sm flex items-center gap-2.5 ${
            feedback.type === 'success'
              ? 'bg-emerald-500/10 border border-emerald-500/30 text-emerald-300'
              : 'bg-rose-500/10 border border-rose-500/30 text-rose-300'
          }`}
        >
          {feedback.type === 'success' ? (
            <CheckCircle className="w-4 h-4 text-emerald-400 shrink-0" />
          ) : (
            <AlertTriangle className="w-4 h-4 text-rose-400 shrink-0" />
          )}
          <span>{feedback.msg}</span>
        </div>
      )}

      {/* Tabs & Search Controls */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
        <div className="flex items-center gap-2">
          <button
            onClick={() => setActiveTab('explore')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
              activeTab === 'explore'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'
            }`}
          >
            Explore Gallery ({models.length})
          </button>
          <button
            onClick={() => setActiveTab('installed')}
            className={`px-4 py-2 rounded-lg text-sm font-medium transition ${
              activeTab === 'installed'
                ? 'bg-indigo-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'
            }`}
          >
            Installed Models ({models.filter((m) => m.installed).length})
          </button>
        </div>

        <div className="flex items-center gap-3">
          {/* Category Filter */}
          <div className="flex items-center gap-1.5 bg-slate-950 border border-slate-800 rounded-lg p-1 text-xs">
            {['all', 'llm', 'stt', 'tts'].map((cat) => (
              <button
                key={cat}
                onClick={() => setCategoryFilter(cat)}
                className={`px-3 py-1 rounded-md uppercase font-medium transition ${
                  categoryFilter === cat
                    ? 'bg-indigo-600/20 text-indigo-400 border border-indigo-500/30'
                    : 'text-slate-400 hover:text-slate-200'
                }`}
              >
                {cat}
              </button>
            ))}
          </div>

          {/* Search Box */}
          <div className="relative">
            <Search className="w-4 h-4 text-slate-500 absolute left-3 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              placeholder="Filter by name or ID..."
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              className="bg-slate-950 border border-slate-800 rounded-lg pl-9 pr-3 py-1.5 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 w-52"
            />
          </div>
        </div>
      </div>

      {/* Warning Modal */}
      {warningModel && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-amber-500/40 rounded-xl p-6 max-w-md w-full space-y-4 shadow-2xl">
            <div className="flex items-center gap-3 text-amber-400">
              <AlertTriangle className="w-6 h-6" />
              <h3 className="font-semibold text-base text-slate-100">Memory Budget Warning</h3>
            </div>
            <p className="text-xs text-slate-300 leading-relaxed">
              <strong className="text-amber-300">{warningModel.name}</strong> requires approximately{' '}
              <strong className="text-white font-mono">{warningModel.ram_required_mb} MB RAM</strong>
              . This exceeds the safe resident threshold for your 8 GB Unified Memory configuration
              and may induce aggressive swapping.
            </p>
            <div className="flex justify-end gap-3 pt-2">
              <button
                onClick={() => setWarningModel(null)}
                className="px-4 py-2 rounded-lg text-xs font-medium text-slate-400 hover:text-slate-200"
              >
                Cancel
              </button>
              <button
                onClick={() => executeInstall(warningModel.id)}
                className="px-4 py-2 rounded-lg text-xs font-medium bg-amber-600 hover:bg-amber-500 text-white"
              >
                Proceed Anyway
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Grid of Model Cards */}
      {loading ? (
        <div className="p-16 text-center text-slate-500 text-sm flex items-center justify-center gap-2">
          <Loader2 className="w-5 h-5 animate-spin" /> Scanning model registry...
        </div>
      ) : filteredModels.length === 0 ? (
        <div className="p-16 text-center text-slate-500 text-sm">
          No models found matching criteria.
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-5">
          {filteredModels.map((model) => {
            const job = activeJobs[model.id];
            const isInstalling = job && ['queued', 'downloading', 'verifying', 'registering'].includes(job.status);

            return (
              <div
                key={model.id}
                className="bg-slate-950 border border-slate-800 rounded-xl p-5 flex flex-col justify-between space-y-4 hover:border-slate-700 transition"
              >
                <div className="space-y-3">
                  <div className="flex items-start justify-between gap-2">
                    <div>
                      <h3 className="font-semibold text-slate-100 text-sm">{model.name}</h3>
                      <span className="text-[11px] font-mono text-slate-500">{model.id}</span>
                    </div>
                    <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-slate-900 border border-slate-800 text-indigo-400 shrink-0">
                      {model.category}
                    </span>
                  </div>

                  <p className="text-xs text-slate-400 leading-relaxed line-clamp-2">
                    {model.description}
                  </p>

                  <div className="flex flex-wrap items-center gap-2 pt-1">
                    {getFitBadge(model.fit_score)}
                    <span className="text-[11px] font-mono text-slate-500">
                      RAM: ~{model.ram_required_mb} MB
                    </span>
                  </div>
                </div>

                <div className="space-y-3 pt-3 border-t border-slate-900">
                  <div className="flex items-center justify-between text-[11px] text-slate-500 font-mono">
                    <span>Size: {model.size_mb} MB</span>
                    <span>Backend: {model.provider}</span>
                  </div>

                  {isInstalling ? (
                    <div className="space-y-1.5">
                      <div className="flex justify-between text-[11px] text-indigo-400 capitalize">
                        <span>{job.status === 'registering' ? 'Registering with engine...' : job.status === 'verifying' ? 'Verifying model...' : 'Downloading weights...'}</span>
                        <span>{job.progress_percent}%</span>
                      </div>
                      <div className="w-full bg-slate-900 rounded-full h-1.5 overflow-hidden">
                        <div
                          className="bg-indigo-500 h-1.5 transition-all duration-300"
                          style={{ width: `${job.progress_percent}%` }}
                        />
                      </div>
                    </div>
                  ) : model.installed ? (
                    <div className="flex items-center justify-between">
                      <span className="flex items-center gap-1.5 text-xs text-emerald-400 font-medium">
                        <CheckCircle className="w-3.5 h-3.5" /> Installed
                      </span>
                      <button
                        onClick={() => handleUninstall(model.id)}
                        disabled={uninstallingId === model.id}
                        className="p-1.5 text-slate-500 hover:text-rose-400 hover:bg-slate-900 rounded-md transition disabled:opacity-50"
                        title="Uninstall Model"
                      >
                        {uninstallingId === model.id ? (
                          <Loader2 className="w-3.5 h-3.5 animate-spin text-rose-400" />
                        ) : (
                          <Trash2 className="w-3.5 h-3.5" />
                        )}
                      </button>
                    </div>
                  ) : (
                    <button
                      onClick={() => handleInstallClick(model)}
                      className="w-full flex items-center justify-center gap-2 py-2 px-3 bg-slate-900 hover:bg-indigo-600 text-slate-200 hover:text-white rounded-lg text-xs font-medium transition border border-slate-800 hover:border-transparent"
                    >
                      <Download className="w-3.5 h-3.5" /> Install Model
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
};
