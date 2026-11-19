import React, { useState, useEffect } from 'react';
import {
  Key, Plus, Copy, Check, Pause, Play, Trash2, RotateCcw,
  AlertTriangle, Layers, X
} from 'lucide-react';
import { fetchAdminJson } from './api';

interface Profile {
  id: string;
  name: string;
  description?: string;
  contact?: string;
  notes?: string;
  disabled_at?: number | null;
  created_at: number;
}

interface ApiKeyItem {
  id: string;
  name: string;
  role: string;
  scopes: string[];
  rate_limit: number;
  max_concurrent: number;
  max_daily_tokens?: number | null;
  max_daily_audio_seconds?: number | null;
  expires_at?: number | null;
  revoked_at?: number | null;
  created_at: number;
  profile_id?: string | null;
  prefix?: string | null;
  status: string;
  model_allowlist: string[];
  ip_allowlist: string[];
  rotated_from?: string | null;
  rotation_expires_at?: number | null;
  last_used_at?: number | null;
}

export const ApiKeys: React.FC = () => {
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [keys, setKeys] = useState<ApiKeyItem[]>([]);
  const [selectedProfileId, setSelectedProfileId] = useState<string | null>(null);
  const [_loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  // Modals state
  const [showNewProfileModal, setShowNewProfileModal] = useState<boolean>(false);
  const [newProfileName, setNewProfileName] = useState<string>('');
  const [newProfileDesc, setNewProfileDesc] = useState<string>('');
  const [newProfileContact, setNewProfileContact] = useState<string>('');
  const [newProfileNotes, setNewProfileNotes] = useState<string>('');

  const [showNewKeyModal, setShowNewKeyModal] = useState<boolean>(false);
  const [newKeyName, setNewKeyName] = useState<string>('');
  const [newKeyProfileId, setNewKeyProfileId] = useState<string>('');
  const [newKeyScopes, setNewKeyScopes] = useState<string[]>([
    'llm',
    'stt',
    'tts',
    'stt_stream',
    'voice',
    'voice_stream',
  ]);
  const [newKeyRateLimit, setNewKeyRateLimit] = useState<number>(60);
  const [newKeyMaxConcurrent, setNewKeyMaxConcurrent] = useState<number>(5);
  const [newKeyDailyTokens, setNewKeyDailyTokens] = useState<string>('');
  const [newKeyDailyAudio, setNewKeyDailyAudio] = useState<string>('');
  const [newKeyModelAllowlist, setNewKeyModelAllowlist] = useState<string>('');
  const [newKeyIpAllowlist, setNewKeyIpAllowlist] = useState<string>('');

  // One-time Plain Key reveal modal
  const [createdPlainKey, setCreatedPlainKey] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState<boolean>(false);

  // Rotate Modal
  const [rotateKeyId, setRotateKeyId] = useState<string | null>(null);
  const [rotateGraceDays, setRotateGraceDays] = useState<number>(1.0);

  const loadData = async () => {
    setLoading(true);
    setError(null);
    try {
      const [profs, keyList] = await Promise.all([
        fetchAdminJson<Profile[]>('/v1/admin/profiles'),
        fetchAdminJson<ApiKeyItem[]>('/v1/admin/keys'),
      ]);
      setProfiles(profs);
      setKeys(keyList);
      if (profs.length > 0 && !selectedProfileId) {
        setSelectedProfileId(profs[0].id);
      }
    } catch (err: any) {
      setError(err?.message || 'Failed to fetch profiles or keys');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const handleCreateProfile = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newProfileName.trim()) return;
    try {
      const created = await fetchAdminJson<Profile>('/v1/admin/profiles', {
        method: 'POST',
        body: JSON.stringify({
          name: newProfileName.trim(),
          description: newProfileDesc.trim() || undefined,
          contact: newProfileContact.trim() || undefined,
          notes: newProfileNotes.trim() || undefined,
        }),
      });
      setProfiles([created, ...profiles]);
      setSelectedProfileId(created.id);
      setShowNewProfileModal(false);
      setNewProfileName('');
      setNewProfileDesc('');
      setNewProfileContact('');
      setNewProfileNotes('');
    } catch (err: any) {
      alert(`Error creating profile: ${err.message}`);
    }
  };

  const handleToggleProfileDisabled = async (profile: Profile) => {
    const isCurrentlyDisabled = Boolean(profile.disabled_at);
    try {
      const updated = await fetchAdminJson<Profile>(`/v1/admin/profiles/${profile.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ disabled: !isCurrentlyDisabled }),
      });
      setProfiles(profiles.map((p) => (p.id === profile.id ? updated : p)));
    } catch (err: any) {
      alert(`Failed to toggle profile state: ${err.message}`);
    }
  };

  const handleCreateKey = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newKeyName.trim()) return;
    try {
      const models = newKeyModelAllowlist
        .split(',')
        .map((m) => m.trim())
        .filter(Boolean);
      const ips = newKeyIpAllowlist
        .split(',')
        .map((i) => i.trim())
        .filter(Boolean);

      const res = await fetchAdminJson<any>('/v1/admin/keys', {
        method: 'POST',
        body: JSON.stringify({
          name: newKeyName.trim(),
          profile_id: newKeyProfileId || selectedProfileId || undefined,
          scopes: newKeyScopes,
          rate_limit: Number(newKeyRateLimit) || 60,
          max_concurrent: Number(newKeyMaxConcurrent) || 5,
          max_daily_tokens: newKeyDailyTokens ? Number(newKeyDailyTokens) : undefined,
          max_daily_audio_seconds: newKeyDailyAudio ? Number(newKeyDailyAudio) : undefined,
          model_allowlist: models.length > 0 ? models : undefined,
          ip_allowlist: ips.length > 0 ? ips : undefined,
        }),
      });

      // Show one-time secret key
      setCreatedPlainKey(res.key);
      setShowNewKeyModal(false);
      setNewKeyName('');
      setNewKeyModelAllowlist('');
      setNewKeyIpAllowlist('');
      await loadData();
    } catch (err: any) {
      alert(`Error creating key: ${err.message}`);
    }
  };

  const handleTogglePauseKey = async (key: ApiKeyItem) => {
    const nextStatus = key.status === 'paused' ? 'active' : 'paused';
    try {
      const updated = await fetchAdminJson<ApiKeyItem>(`/v1/admin/keys/${key.id}`, {
        method: 'PATCH',
        body: JSON.stringify({ status: nextStatus }),
      });
      setKeys(keys.map((k) => (k.id === key.id ? updated : k)));
    } catch (err: any) {
      alert(`Failed to update key: ${err.message}`);
    }
  };

  const handleRevokeKey = async (keyId: string) => {
    if (!confirm('Are you sure you want to revoke this API key? This cannot be undone.')) return;
    try {
      await fetchAdminJson(`/v1/admin/keys/${keyId}`, { method: 'DELETE' });
      setKeys(keys.map((k) => (k.id === keyId ? { ...k, status: 'revoked' } : k)));
    } catch (err: any) {
      alert(`Failed to revoke key: ${err.message}`);
    }
  };

  const handleRotateKey = async () => {
    if (!rotateKeyId) return;
    try {
      const res = await fetchAdminJson<any>(`/v1/admin/keys/${rotateKeyId}/rotate`, {
        method: 'POST',
        body: JSON.stringify({ grace_period_days: rotateGraceDays }),
      });
      setRotateKeyId(null);
      setCreatedPlainKey(res.key);
      await loadData();
    } catch (err: any) {
      alert(`Rotation failed: ${err.message}`);
    }
  };

  const selectedProfile = profiles.find((p) => p.id === selectedProfileId);
  const filteredKeys = selectedProfileId
    ? keys.filter((k) => k.profile_id === selectedProfileId)
    : keys;

  return (
    <div className="max-w-7xl mx-auto space-y-6">
      {/* Top Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-100">Application Profiles & API Keys</h1>
          <p className="text-sm text-slate-400">
            Manage consuming projects, generate scoped access tokens, and enforce network limits
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowNewProfileModal(true)}
            className="flex items-center gap-2 px-3 py-2 bg-slate-900 border border-slate-800 hover:border-slate-700 text-slate-300 rounded-lg text-xs font-medium transition-all"
          >
            <Plus className="w-3.5 h-3.5 text-slate-400" />
            <span>New Profile</span>
          </button>
          <button
            onClick={() => {
              setNewKeyProfileId(selectedProfileId || '');
              setShowNewKeyModal(true);
            }}
            className="flex items-center gap-2 px-3 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-sm transition-all"
          >
            <Key className="w-3.5 h-3.5" />
            <span>+ New Key</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="p-4 bg-rose-500/10 border border-rose-500/20 rounded-xl text-rose-300 text-sm">
          {error}
        </div>
      )}

      {/* Main Two-Column Layout */}
      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* Left Column: Profiles List */}
        <div className="lg:col-span-1 bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-4">
          <div className="flex items-center justify-between text-xs font-semibold text-slate-400 uppercase tracking-wider px-2">
            <span>Profiles ({profiles.length})</span>
            <Layers className="w-3.5 h-3.5 text-slate-500" />
          </div>

          <div className="space-y-1">
            <button
              onClick={() => setSelectedProfileId(null)}
              className={`w-full text-left px-3 py-2.5 rounded-lg text-xs font-medium transition-all flex items-center justify-between ${
                selectedProfileId === null
                  ? 'bg-indigo-600/15 text-indigo-400 border border-indigo-500/30'
                  : 'text-slate-400 hover:text-slate-200 hover:bg-slate-900'
              }`}
            >
              <span>All Consuming Projects</span>
              <span className="text-[10px] bg-slate-800 px-1.5 py-0.5 rounded text-slate-300">{keys.length}</span>
            </button>

            {profiles.map((p) => {
              const isSelected = selectedProfileId === p.id;
              const pKeyCount = keys.filter((k) => k.profile_id === p.id).length;
              const isDisabled = Boolean(p.disabled_at);

              return (
                <div
                  key={p.id}
                  onClick={() => setSelectedProfileId(p.id)}
                  className={`w-full cursor-pointer p-3 rounded-lg text-xs transition-all space-y-1.5 border ${
                    isSelected
                      ? 'bg-indigo-600/15 border-indigo-500/30 text-slate-200'
                      : 'border-transparent text-slate-400 hover:text-slate-200 hover:bg-slate-900'
                  }`}
                >
                  <div className="flex items-center justify-between font-semibold text-slate-200">
                    <span className="truncate">{p.name}</span>
                    <span
                      className={`w-2 h-2 rounded-full shrink-0 ${
                        isDisabled ? 'bg-amber-400' : 'bg-emerald-400'
                      }`}
                      title={isDisabled ? 'Disabled' : 'Active'}
                    />
                  </div>
                  {p.description && <p className="text-[11px] text-slate-500 truncate">{p.description}</p>}
                  <div className="flex items-center justify-between text-[10px] text-slate-500 pt-1 border-t border-slate-900">
                    <span>{p.contact || 'No contact'}</span>
                    <span className="font-mono">{pKeyCount} keys</span>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        {/* Right Column: Key Cards for Selected Profile */}
        <div className="lg:col-span-3 space-y-4">
          {/* Header of selected profile */}
          {selectedProfile && (
            <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs">
              <div>
                <div className="flex items-center gap-2">
                  <h2 className="font-semibold text-slate-200 text-sm">{selectedProfile.name}</h2>
                  {selectedProfile.disabled_at ? (
                    <span className="bg-amber-500/10 text-amber-400 border border-amber-500/20 px-2 py-0.5 rounded-full text-[10px] font-medium">
                      Profile Disabled (All keys blocked)
                    </span>
                  ) : (
                    <span className="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded-full text-[10px] font-medium">
                      Active
                    </span>
                  )}
                </div>
                <p className="text-slate-400 text-xs mt-0.5">{selectedProfile.description || 'No description provided'}</p>
              </div>

              <div className="flex items-center gap-2">
                <button
                  onClick={() => handleToggleProfileDisabled(selectedProfile)}
                  className={`px-3 py-1.5 rounded-lg text-xs font-medium border transition-all ${
                    selectedProfile.disabled_at
                      ? 'bg-emerald-500/10 border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/20'
                      : 'bg-amber-500/10 border-amber-500/30 text-amber-400 hover:bg-amber-500/20'
                  }`}
                >
                  {selectedProfile.disabled_at ? 'Enable Profile' : 'Disable Profile'}
                </button>
              </div>
            </div>
          )}

          {/* Key Cards Grid */}
          {filteredKeys.length === 0 ? (
            <div className="bg-slate-950 border border-dashed border-slate-800 rounded-xl p-12 text-center space-y-3">
              <Key className="w-8 h-8 text-slate-600 mx-auto" />
              <div className="text-sm font-semibold text-slate-300">No API Keys Generated</div>
              <p className="text-xs text-slate-500 max-w-sm mx-auto">
                Create an API key for your consuming application to allow it to invoke LLM, STT, or TTS over the network.
              </p>
              <button
                onClick={() => {
                  setNewKeyProfileId(selectedProfileId || '');
                  setShowNewKeyModal(true);
                }}
                className="mt-2 px-3 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold"
              >
                + Create First Key
              </button>
            </div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
              {filteredKeys.map((key) => {
                const isPaused = key.status === 'paused';
                const isRevoked = key.status === 'revoked';
                const isRotating = Boolean(key.rotation_expires_at);

                return (
                  <div
                    key={key.id}
                    className="bg-slate-950 border border-slate-800 rounded-xl p-5 space-y-3 relative hover:border-slate-700 transition-all text-xs"
                  >
                    {/* Header: Name and Status */}
                    <div className="flex items-center justify-between">
                      <div className="font-semibold text-slate-100 text-sm truncate">{key.name}</div>
                      <div>
                        {isRevoked ? (
                          <span className="bg-rose-500/10 text-rose-400 border border-rose-500/20 px-2 py-0.5 rounded-full text-[10px] font-medium">
                            Revoked
                          </span>
                        ) : isPaused ? (
                          <span className="bg-amber-500/10 text-amber-400 border border-amber-500/20 px-2 py-0.5 rounded-full text-[10px] font-medium flex items-center gap-1">
                            <span className="w-1.5 h-1.5 rounded-full bg-amber-400" /> Paused
                          </span>
                        ) : (
                          <span className="bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 px-2 py-0.5 rounded-full text-[10px] font-medium flex items-center gap-1">
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" /> Active
                          </span>
                        )}
                      </div>
                    </div>

                    {/* Key Prefix Display */}
                    <div className="flex items-center justify-between bg-slate-900 border border-slate-800 px-2.5 py-1.5 rounded-md font-mono text-[11px] text-slate-300">
                      <span>{key.prefix || 'va_live_••••...'}</span>
                      <span className="text-[10px] text-slate-500 font-sans">Prefix Only</span>
                    </div>

                    {/* Scopes Chips */}
                    <div className="flex flex-wrap gap-1.5">
                      {key.scopes.map((s) => (
                        <span
                          key={s}
                          className="bg-indigo-600/10 text-indigo-400 border border-indigo-500/20 px-2 py-0.5 rounded text-[10px] font-medium uppercase"
                        >
                          {s}
                        </span>
                      ))}
                    </div>

                    {/* Constraints & Limits */}
                    <div className="space-y-1 text-slate-400 text-[11px] pt-1">
                      <div className="flex justify-between">
                        <span>Limits:</span>
                        <span className="text-slate-300 font-mono">
                          {key.rate_limit} req/min · {key.max_concurrent} conc.
                        </span>
                      </div>
                      <div className="flex justify-between">
                        <span>Models:</span>
                        <span className="text-slate-300 truncate max-w-[180px]">
                          {key.model_allowlist && key.model_allowlist.length > 0
                            ? key.model_allowlist.join(', ')
                            : 'All installed'}
                        </span>
                      </div>
                      {key.ip_allowlist && key.ip_allowlist.length > 0 && (
                        <div className="flex justify-between">
                          <span>IP Allowlist:</span>
                          <span className="text-sky-400 font-mono truncate max-w-[180px]">
                            {key.ip_allowlist.join(', ')}
                          </span>
                        </div>
                      )}
                      {isRotating && (
                        <div className="text-amber-400 text-[10px] bg-amber-500/10 p-1.5 rounded border border-amber-500/20">
                          Rotating: Old key expires in{' '}
                          {Math.max(0, Math.round(((key.rotation_expires_at || 0) - Date.now() / 1000) / 3600))} hours
                        </div>
                      )}
                    </div>

                    {/* Card Actions */}
                    <div className="flex items-center justify-between pt-3 border-t border-slate-900">
                      <div className="text-[10px] text-slate-500">
                        {key.last_used_at
                          ? `Used ${new Date(key.last_used_at * 1000).toLocaleTimeString()}`
                          : 'Never used'}
                      </div>

                      <div className="flex items-center gap-1.5">
                        {!isRevoked && (
                          <>
                            <button
                              onClick={() => handleTogglePauseKey(key)}
                              className="p-1.5 hover:bg-slate-800 text-slate-400 hover:text-slate-200 rounded"
                              title={isPaused ? 'Resume Key' : 'Pause Key'}
                            >
                              {isPaused ? <Play className="w-3.5 h-3.5" /> : <Pause className="w-3.5 h-3.5" />}
                            </button>
                            <button
                              onClick={() => setRotateKeyId(key.id)}
                              className="p-1.5 hover:bg-slate-800 text-slate-400 hover:text-slate-200 rounded"
                              title="Rotate Key with Grace Period"
                            >
                              <RotateCcw className="w-3.5 h-3.5" />
                            </button>
                            <button
                              onClick={() => handleRevokeKey(key.id)}
                              className="p-1.5 hover:bg-slate-800 text-rose-400 hover:text-rose-300 rounded"
                              title="Revoke Key Immediately"
                            >
                              <Trash2 className="w-3.5 h-3.5" />
                            </button>
                          </>
                        )}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {/* MODAL: One-Time Secret Key Reveal */}
      {createdPlainKey && (
        <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-950 border border-amber-500/40 rounded-xl p-6 max-w-lg w-full space-y-4 shadow-2xl">
            <div className="flex items-center gap-3 text-amber-400 font-semibold text-base">
              <AlertTriangle className="w-5 h-5 shrink-0" />
              <span>Save Your API Key Now</span>
            </div>

            <p className="text-xs text-slate-300 leading-relaxed">
              This is the <strong>only time</strong> the full plain key is shown. Once you close this modal, only the
              prefix can be viewed.
            </p>

            <div className="space-y-1.5">
              <label className="text-[10px] uppercase font-semibold text-slate-400 tracking-wider">Secret API Key</label>
              <div className="flex items-center gap-2 bg-slate-900 border border-slate-700 p-2.5 rounded-lg font-mono text-xs text-indigo-300 break-all select-all">
                <span>{createdPlainKey}</span>
              </div>
            </div>

            <div className="flex items-center justify-between pt-2">
              <button
                onClick={() => {
                  navigator.clipboard.writeText(createdPlainKey);
                  setCopiedKey(true);
                  setTimeout(() => setCopiedKey(false), 3000);
                }}
                className="flex items-center gap-2 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-semibold shadow-sm transition-all"
              >
                {copiedKey ? <Check className="w-4 h-4 text-emerald-300" /> : <Copy className="w-4 h-4" />}
                <span>{copiedKey ? 'Copied to Clipboard!' : 'Copy Key'}</span>
              </button>

              <button
                onClick={() => setCreatedPlainKey(null)}
                className="px-4 py-2 bg-slate-900 border border-slate-800 hover:border-slate-700 text-slate-300 rounded-lg text-xs font-medium"
              >
                Done / Close
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL: Create New Profile */}
      {showNewProfileModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 max-w-md w-full space-y-4 shadow-xl">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-slate-100 text-sm">Create Application Profile</h3>
              <button onClick={() => setShowNewProfileModal(false)} className="text-slate-500 hover:text-slate-300">
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleCreateProfile} className="space-y-3 text-xs">
              <div>
                <label className="block text-slate-400 mb-1">Profile / Project Name *</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. telephony-backend"
                  value={newProfileName}
                  onChange={(e) => setNewProfileName(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-slate-400 mb-1">Description</label>
                <input
                  type="text"
                  placeholder="e.g. Inbound SIP PBX gateway integration"
                  value={newProfileDesc}
                  onChange={(e) => setNewProfileDesc(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 outline-none focus:border-indigo-500"
                />
              </div>

              <div>
                <label className="block text-slate-400 mb-1">Owner Contact</label>
                <input
                  type="text"
                  placeholder="e.g. ops@example.com"
                  value={newProfileContact}
                  onChange={(e) => setNewProfileContact(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 outline-none focus:border-indigo-500"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-3">
                <button
                  type="button"
                  onClick={() => setShowNewProfileModal(false)}
                  className="px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg text-slate-400 hover:text-slate-200"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-semibold"
                >
                  Create Profile
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: Create New API Key */}
      {showNewKeyModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 max-w-lg w-full space-y-4 shadow-xl max-h-[90vh] overflow-y-auto">
            <div className="flex items-center justify-between">
              <h3 className="font-semibold text-slate-100 text-sm">Generate New API Key</h3>
              <button onClick={() => setShowNewKeyModal(false)} className="text-slate-500 hover:text-slate-300">
                <X className="w-4 h-4" />
              </button>
            </div>

            <form onSubmit={handleCreateKey} className="space-y-4 text-xs">
              <div>
                <label className="block text-slate-400 mb-1">Owning Profile *</label>
                <select
                  value={newKeyProfileId}
                  onChange={(e) => setNewKeyProfileId(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 outline-none focus:border-indigo-500"
                >
                  <option value="">Unassigned</option>
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id}>
                      {p.name}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="block text-slate-400 mb-1">Key Name / Identifier *</label>
                <input
                  type="text"
                  required
                  placeholder="e.g. prod-voice-worker"
                  value={newKeyName}
                  onChange={(e) => setNewKeyName(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 outline-none focus:border-indigo-500"
                />
              </div>

              {/* Scopes Selection */}
              <div>
                <label className="block text-slate-400 mb-1.5 font-medium">Allowed Scopes</label>
                <div className="grid grid-cols-3 gap-2">
                  {['llm', 'stt', 'tts', 'stt_stream', 'voice', 'voice_stream'].map((scope) => {
                    const checked = newKeyScopes.includes(scope);
                    return (
                      <label
                        key={scope}
                        className={`flex items-center gap-2 p-2 rounded-lg border cursor-pointer select-none transition-all ${
                          checked
                            ? 'bg-indigo-600/15 border-indigo-500/30 text-indigo-300'
                            : 'bg-slate-900 border-slate-800 text-slate-400'
                        }`}
                      >
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(e) => {
                            if (e.target.checked) {
                              setNewKeyScopes([...newKeyScopes, scope]);
                            } else {
                              setNewKeyScopes(newKeyScopes.filter((s) => s !== scope));
                            }
                          }}
                          className="rounded border-slate-700 bg-slate-800 text-indigo-500"
                        />
                        <span className="font-mono text-[11px]">{scope}</span>
                      </label>
                    );
                  })}
                </div>
              </div>

              {/* Limits */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-400 mb-1">Rate Limit (req/min)</label>
                  <input
                    type="number"
                    min="1"
                    max="10000"
                    value={newKeyRateLimit}
                    onChange={(e) => setNewKeyRateLimit(Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-slate-400 mb-1">Max Concurrency</label>
                  <input
                    type="number"
                    min="1"
                    max="100"
                    value={newKeyMaxConcurrent}
                    onChange={(e) => setNewKeyMaxConcurrent(Number(e.target.value))}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono"
                  />
                </div>
              </div>

              {/* Optional Daily Quotas */}
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-slate-400 mb-1">Daily Token Quota (optional)</label>
                  <input
                    type="number"
                    placeholder="e.g. 50000"
                    value={newKeyDailyTokens}
                    onChange={(e) => setNewKeyDailyTokens(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono"
                  />
                </div>
                <div>
                  <label className="block text-slate-400 mb-1">Daily Audio Secs (optional)</label>
                  <input
                    type="number"
                    placeholder="e.g. 3600"
                    value={newKeyDailyAudio}
                    onChange={(e) => setNewKeyDailyAudio(e.target.value)}
                    className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono"
                  />
                </div>
              </div>

              {/* Allowlists */}
              <div>
                <label className="block text-slate-400 mb-1">Model Allowlist (comma-separated, optional)</label>
                <input
                  type="text"
                  placeholder="e.g. whisper-base, kokoro"
                  value={newKeyModelAllowlist}
                  onChange={(e) => setNewKeyModelAllowlist(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono text-[11px]"
                />
              </div>

              <div>
                <label className="block text-slate-400 mb-1">IP Allowlist CIDR (optional)</label>
                <input
                  type="text"
                  placeholder="e.g. 192.168.1.0/24"
                  value={newKeyIpAllowlist}
                  onChange={(e) => setNewKeyIpAllowlist(e.target.value)}
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono text-[11px]"
                />
              </div>

              <div className="flex items-center justify-end gap-2 pt-3 border-t border-slate-900">
                <button
                  type="button"
                  onClick={() => setShowNewKeyModal(false)}
                  className="px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg text-slate-400 hover:text-slate-200"
                >
                  Cancel
                </button>
                <button
                  type="submit"
                  className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-semibold"
                >
                  Generate Key
                </button>
              </div>
            </form>
          </div>
        </div>
      )}

      {/* MODAL: Rotate Key */}
      {rotateKeyId && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
          <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 max-w-sm w-full space-y-4 shadow-xl text-xs">
            <h3 className="font-semibold text-slate-100 text-sm">Rotate API Key</h3>
            <p className="text-slate-400">
              Rotating creates a new active key while keeping the existing key valid during a transition grace period.
            </p>

            <div>
              <label className="block text-slate-400 mb-1">Grace Period (Days)</label>
              <select
                value={rotateGraceDays}
                onChange={(e) => setRotateGraceDays(Number(e.target.value))}
                className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-slate-200 font-mono"
              >
                <option value={0.04}>1 hour (test)</option>
                <option value={1.0}>1 Day (24 hours)</option>
                <option value={3.0}>3 Days</option>
                <option value={7.0}>7 Days</option>
                <option value={14.0}>14 Days</option>
              </select>
            </div>

            <div className="flex items-center justify-end gap-2 pt-2">
              <button
                onClick={() => setRotateKeyId(null)}
                className="px-3 py-1.5 bg-slate-900 border border-slate-800 rounded-lg text-slate-400"
              >
                Cancel
              </button>
              <button
                onClick={handleRotateKey}
                className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg font-semibold"
              >
                Confirm Rotation
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
