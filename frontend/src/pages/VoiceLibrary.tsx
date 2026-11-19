import React, { useEffect, useState } from 'react';
import { Play, RefreshCw, Library, Edit2, Check } from 'lucide-react';
import { Voice } from '../types/api';
import { StreamingPCMPlayer } from '../audio/pcmPlayer';
import { fetchWithAuth } from '../api/client';

export const VoiceLibrary: React.FC = () => {
  const [voices, setVoices] = useState<Voice[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [playingVoice, setPlayingVoice] = useState<string | null>(null);
  const [editingVoice, setEditingVoice] = useState<Voice | null>(null);
  const [editGender, setEditGender] = useState<'male' | 'female' | 'unknown'>('unknown');
  const [editName, setEditName] = useState<string>('');
  const [savingMeta, setSavingMeta] = useState<boolean>(false);
  const [notification, setNotification] = useState<string | null>(null);

  const [player] = useState(() => new StreamingPCMPlayer(24000));

  useEffect(() => {
    fetchVoices();
  }, []);

  const fetchVoices = async () => {
    try {
      setLoading(true);
      const res = await fetchWithAuth('/v1/voices');
      if (res.ok) {
        const data = await res.json();
        setVoices(data.voices || []);
      }
    } catch (err) {
      console.error('Error loading voices:', err);
    } finally {
      setLoading(false);
    }
  };

  const handlePreview = async (voice: Voice) => {
    try {
      setPlayingVoice(voice.voice_id);
      player.stop();

      const res = await fetchWithAuth(`/v1/voices/${voice.voice_id}/preview`, {
        method: 'POST',
      });

      if (res.ok) {
        const pcm = await res.arrayBuffer();
        player.feed(pcm);
      }
    } catch (err) {
      console.error('Preview error:', err);
    } finally {
      setPlayingVoice(null);
    }
  };

  const openEditModal = (v: Voice) => {
    setEditingVoice(v);
    setEditName(v.name);
    setEditGender(v.gender || 'unknown');
  };

  const handleSaveMeta = async () => {
    if (!editingVoice) return;
    try {
      setSavingMeta(true);
      const res = await fetchWithAuth(`/v1/voices/${editingVoice.voice_id}/meta`, {
        method: 'PUT',
        body: JSON.stringify({
          name: editName,
          gender: editGender,
        }),
      });
      if (res.ok) {
        const updated = await res.json();
        setVoices((prev) =>
          prev.map((v) => (v.voice_id === editingVoice.voice_id ? { ...v, ...updated } : v))
        );
        setEditingVoice(null);
        setNotification('Voice metadata updated!');
        setTimeout(() => setNotification(null), 3000);
      }
    } catch (err: any) {
      console.error('Error saving voice meta:', err);
    } finally {
      setSavingMeta(false);
    }
  };

  const getGenderBadge = (gender?: string) => {
    switch (gender) {
      case 'female':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-pink-500/10 text-pink-400 border border-pink-500/20">
            Female
          </span>
        );
      case 'male':
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-sky-500/10 text-sky-400 border border-sky-500/20">
            Male
          </span>
        );
      default:
        return (
          <span className="px-2 py-0.5 rounded text-[10px] font-medium bg-slate-800 text-slate-400 border border-slate-700">
            Unknown
          </span>
        );
    }
  };

  return (
    <div className="max-w-5xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-100 flex items-center gap-2.5">
            <Library className="w-7 h-7 text-indigo-400" />
            Voice Library
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Available Kokoro voices with gender tags, preview samples, and manual metadata overrides.
          </p>
        </div>
      </div>

      {notification && (
        <div className="p-3 bg-emerald-500/10 border border-emerald-500/30 rounded-xl flex items-center gap-2 text-xs text-emerald-300">
          <Check className="w-4 h-4 text-emerald-400" />
          <span>{notification}</span>
        </div>
      )}

      {/* Edit Metadata Modal */}
      {editingVoice && (
        <div className="fixed inset-0 z-50 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="bg-slate-900 border border-slate-800 rounded-xl p-5 max-w-sm w-full space-y-4 shadow-2xl">
            <h3 className="font-semibold text-slate-100 text-sm">
              Edit Voice: <span className="font-mono text-indigo-400">{editingVoice.voice_id}</span>
            </h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-slate-400 block mb-1">Display Name</label>
                <input
                  type="text"
                  value={editName}
                  onChange={(e) => setEditName(e.target.value)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
                />
              </div>
              <div>
                <label className="text-xs text-slate-400 block mb-1">Gender Tag</label>
                <select
                  value={editGender}
                  onChange={(e) => setEditGender(e.target.value as any)}
                  className="w-full bg-slate-950 border border-slate-800 rounded-lg p-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500"
                >
                  <option value="female">Female</option>
                  <option value="male">Male</option>
                  <option value="unknown">Unknown / Neutral</option>
                </select>
              </div>
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <button
                onClick={() => setEditingVoice(null)}
                className="px-3 py-1.5 text-xs text-slate-400 hover:text-slate-200"
              >
                Cancel
              </button>
              <button
                onClick={handleSaveMeta}
                disabled={savingMeta}
                className="px-4 py-1.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-xs font-medium"
              >
                {savingMeta ? 'Saving...' : 'Save Override'}
              </button>
            </div>
          </div>
        </div>
      )}

      {loading ? (
        <div className="flex items-center justify-center p-16 text-slate-500">
          <RefreshCw className="w-6 h-6 animate-spin text-indigo-400" />
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {voices.map((v) => (
            <div
              key={v.voice_id}
              className="bg-slate-950 border border-slate-800 rounded-xl p-5 flex flex-col justify-between hover:border-slate-700 transition space-y-4"
            >
              <div>
                <div className="flex items-center justify-between">
                  {getGenderBadge(v.gender)}
                  <button
                    onClick={() => openEditModal(v)}
                    className="p-1 text-slate-500 hover:text-slate-300 rounded hover:bg-slate-900"
                    title="Edit Metadata"
                  >
                    <Edit2 className="w-3.5 h-3.5" />
                  </button>
                </div>
                <h3 className="font-semibold text-slate-200 text-sm mt-2">{v.name}</h3>
                <p className="text-xs text-slate-500 font-mono mt-0.5">{v.voice_id}</p>
                <div className="text-[11px] text-slate-500 mt-1">
                  Engine: <span className="font-mono text-slate-400">{v.engine || 'kokoro'}</span>
                </div>
              </div>

              <button
                onClick={() => handlePreview(v)}
                disabled={playingVoice === v.voice_id}
                className="w-full py-2 bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 rounded-lg text-xs font-medium flex items-center justify-center gap-2 transition"
              >
                {playingVoice === v.voice_id ? (
                  <>
                    <RefreshCw className="w-3.5 h-3.5 animate-spin" /> Playing Sample...
                  </>
                ) : (
                  <>
                    <Play className="w-3.5 h-3.5 fill-current" /> Preview Voice
                  </>
                )}
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  );
};
