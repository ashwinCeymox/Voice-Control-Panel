import React, { useState, useEffect } from 'react';
import { Play, Square, RefreshCw, Volume2, AlertCircle } from 'lucide-react';
import { StreamingPCMPlayer } from '../audio/pcmPlayer';
import { Voice } from '../types/api';
import { fetchWithAuth } from '../api/client';

export const TTSPlayground: React.FC = () => {
  const [text, setText] = useState(
    'Hello! This is a test of self-hosted text to speech synthesis using Kokoro on an Apple Silicon M2.'
  );
  const [installedModels, setInstalledModels] = useState<Array<{ id: string; name: string }>>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [voices, setVoices] = useState<Voice[]>([]);
  const [voiceId, setVoiceId] = useState<string>('af_heart');
  const [speed, setSpeed] = useState<number>(1.0);
  const [loading, setLoading] = useState<boolean>(false);
  const [previewingId, setPreviewingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [player] = useState(() => new StreamingPCMPlayer(24000));

  useEffect(() => {
    fetchTTSOptions();
  }, []);

  const fetchTTSOptions = async () => {
    try {
      // 1. Fetch TTS models
      const modelsRes = await fetchWithAuth('/v1/tts/models');
      if (modelsRes.ok) {
        const mData = await modelsRes.json();
        if (mData.models && mData.models.length > 0) {
          setInstalledModels(mData.models);
          setSelectedModel(mData.models[0].id);
        }
      }

      // 2. Fetch extended voices with gender
      const voicesRes = await fetchWithAuth('/v1/voices');
      if (voicesRes.ok) {
        const vData = await voicesRes.json();
        const vList: Voice[] = vData.voices || [];
        if (vList.length > 0) {
          setVoices(vList);
          setVoiceId((prev) => (prev && vList.some((v) => v.voice_id === prev) ? prev : vList[0].voice_id));
        } else {
          // Resident fallback default
          const defaultVoice: Voice = {
            voice_id: 'af_heart',
            name: 'Heart (Resident)',
            category: 'female',
            gender: 'female',
            settings: { speed: 1.0 },
          };
          setVoices([defaultVoice]);
          setVoiceId('af_heart');
        }
      } else {
        const defaultVoice: Voice = {
          voice_id: 'af_heart',
          name: 'Heart (Resident)',
          category: 'female',
          gender: 'female',
          settings: { speed: 1.0 },
        };
        setVoices([defaultVoice]);
        setVoiceId('af_heart');
      }
    } catch (err: any) {
      console.error('Error fetching TTS options:', err);
      const defaultVoice: Voice = {
        voice_id: 'af_heart',
        name: 'Heart (Resident)',
        category: 'female',
        gender: 'female',
        settings: { speed: 1.0 },
      };
      setVoices([defaultVoice]);
      setVoiceId('af_heart');
    }
  };

  const handlePreviewVoice = async (vId: string) => {
    const targetVid = (vId || 'af_heart').trim();
    try {
      setPreviewingId(targetVid);
      setError(null);
      const res = await fetchWithAuth(`/v1/voices/${encodeURIComponent(targetVid)}/preview`, {
        method: 'POST',
      });
      if (!res.ok) {
        let errMsg = `Preview synthesis failed (HTTP ${res.status})`;
        try {
          const contentType = res.headers.get('content-type') || '';
          if (contentType.includes('application/json')) {
            const errJson = await res.json();
            errMsg = errJson.error?.message || errJson.detail || errJson.message || errMsg;
          } else {
            const textResp = await res.text();
            if (textResp && !textResp.includes('<!DOCTYPE') && !textResp.includes('<html')) {
              errMsg = textResp.slice(0, 160);
            } else {
              errMsg = `LocalTTS engine unreachable (HTTP ${res.status}). Verify local-tts process.`;
            }
          }
        } catch {}
        throw new Error(errMsg);
      }
      const contentType = res.headers.get('content-type') || '';
      if (contentType.includes('text/html')) {
        throw new Error('Received unexpected HTML response instead of audio stream.');
      }
      const rawPcm = await res.arrayBuffer();
      player.feed(rawPcm);
    } catch (err: any) {
      setError(err.message);
    } finally {
      setPreviewingId(null);
    }
  };

  const handleSynthesize = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!text.trim() || loading) return;

    const activeVoice = (voiceId || (voices.length > 0 ? voices[0].voice_id : 'af_heart')).trim() || 'af_heart';

    try {
      setLoading(true);
      setError(null);

      const res = await fetchWithAuth(`/v1/text-to-speech/${encodeURIComponent(activeVoice)}/stream`, {
        method: 'POST',
        body: JSON.stringify({
          text,
          voice_settings: { speed },
        }),
      });

      if (!res.ok) {
        let errMsg = `Synthesis failed (HTTP ${res.status})`;
        try {
          const contentType = res.headers.get('content-type') || '';
          if (contentType.includes('application/json')) {
            const errJson = await res.json();
            errMsg = errJson.error?.message || errJson.detail || errJson.message || errMsg;
          } else {
            const textResp = await res.text();
            if (textResp && !textResp.includes('<!DOCTYPE') && !textResp.includes('<html')) {
              errMsg = textResp.slice(0, 200);
            } else {
              errMsg = `LocalTTS engine unreachable (HTTP ${res.status}). Ensure local-tts is running on port 8880.`;
            }
          }
        } catch {
          // fallback
        }
        throw new Error(errMsg);
      }

      const contentType = res.headers.get('content-type') || '';
      if (contentType.includes('text/html')) {
        throw new Error('Received unexpected HTML response instead of audio stream. Check gateway routing.');
      }

      const rawPcm = await res.arrayBuffer();
      player.feed(rawPcm);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error generating speech');
    } finally {
      setLoading(false);
    }
  };

  const handleStop = () => {
    player.stop();
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

  const currentVoice = voices.find((v) => v.voice_id === voiceId);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-100 flex items-center gap-2.5">
          <Volume2 className="w-7 h-7 text-emerald-400" />
          Text-to-Speech Playground
        </h1>
        <p className="text-sm text-slate-400 mt-1">
          Synthesize signed 16-bit 24kHz PCM audio with voice picker and gender metadata.
        </p>
      </div>

      {error && (
        <div className="p-3.5 bg-rose-500/10 border border-rose-500/30 rounded-xl flex items-center gap-2.5 text-sm text-rose-300">
          <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {installedModels.length === 0 && (
        <div className="p-4 bg-amber-500/10 border border-amber-500/30 rounded-xl flex items-center justify-between gap-3 text-xs text-amber-300">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
            <span>No text-to-speech models installed. Install Kokoro from the Model Registry to enable speech synthesis.</span>
          </div>
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Left Column: Voice Picker & Model Select */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-5 space-y-4">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Voice Configuration
          </h2>

          {/* Installed TTS Model Selector */}
          <div>
            <label className="text-xs text-slate-300 font-medium block mb-1.5">
              Installed TTS Model
            </label>
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              disabled={installedModels.length === 0}
              className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
            >
              {installedModels.length === 0 ? (
                <option value="" disabled>No TTS models installed</option>
              ) : (
                installedModels.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name || m.id}
                  </option>
                ))
              )}
            </select>
          </div>

          {/* Voice Picker */}
          <div>
            <label className="text-xs text-slate-300 font-medium block mb-1.5">
              Select Voice ({voices.length})
            </label>
            <div className="space-y-1.5 max-h-56 overflow-y-auto pr-1 scrollbar-thin">
              {voices.length > 0 ? (
                voices.map((v) => {
                  const isSelected = v.voice_id === voiceId;
                  return (
                    <div
                      key={v.voice_id}
                      onClick={() => setVoiceId(v.voice_id)}
                      className={`p-2.5 rounded-lg border text-xs cursor-pointer flex items-center justify-between transition ${
                        isSelected
                          ? 'bg-emerald-950/30 border-emerald-500/40 text-emerald-300'
                          : 'bg-slate-900/60 border-slate-800/80 hover:bg-slate-900 text-slate-300'
                      }`}
                    >
                      <div className="flex items-center gap-2 truncate">
                        <span className="font-semibold truncate">{v.name}</span>
                        {getGenderBadge(v.gender)}
                      </div>
                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handlePreviewVoice(v.voice_id);
                        }}
                        disabled={previewingId === v.voice_id}
                        className="p-1 hover:bg-slate-800 rounded text-slate-400 hover:text-slate-100 shrink-0"
                        title="Preview sample"
                      >
                        {previewingId === v.voice_id ? (
                          <RefreshCw className="w-3.5 h-3.5 animate-spin" />
                        ) : (
                          <Play className="w-3.5 h-3.5 fill-current" />
                        )}
                      </button>
                    </div>
                  );
                })
              ) : (
                <div className="text-xs text-slate-500 py-3 text-center">Loading voices...</div>
              )}
            </div>
          </div>

          {/* Speed Slider */}
          <div>
            <div className="flex justify-between text-xs text-slate-300 mb-1.5">
              <span>Speaking Speed</span>
              <span className="font-mono text-emerald-400">{speed}x</span>
            </div>
            <input
              type="range"
              min="0.5"
              max="2.0"
              step="0.05"
              value={speed}
              onChange={(e) => setSpeed(parseFloat(e.target.value))}
              className="w-full accent-emerald-500"
            />
          </div>
        </div>

        {/* Right Column: Text & Synthesizer */}
        <div className="md:col-span-2 bg-slate-950 border border-slate-800 rounded-xl p-5 flex flex-col justify-between space-y-4">
          <form onSubmit={handleSynthesize} className="space-y-4">
            <div>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs text-slate-300 font-medium">Text Input</label>
                {currentVoice && (
                  <span className="text-[11px] text-slate-400 flex items-center gap-1.5">
                    Active: <strong className="text-slate-200">{currentVoice.name}</strong> (
                    {currentVoice.voice_id})
                  </span>
                )}
              </div>
              <textarea
                rows={6}
                value={text}
                onChange={(e) => setText(e.target.value)}
                className="w-full bg-slate-900 border border-slate-800 rounded-lg p-3.5 text-sm text-slate-100 focus:outline-none focus:border-emerald-500 resize-none leading-relaxed"
                placeholder="Enter text to synthesize..."
              />
            </div>

            <div className="flex items-center gap-3">
              <button
                type="submit"
                disabled={loading || !text.trim()}
                className="flex items-center gap-2 bg-emerald-600 hover:bg-emerald-500 disabled:opacity-50 text-white px-5 py-2.5 rounded-lg text-sm font-medium transition shadow-sm"
              >
                {loading ? (
                  <>
                    <RefreshCw className="w-4 h-4 animate-spin" /> Synthesizing...
                  </>
                ) : (
                  <>
                    <Play className="w-4 h-4 fill-current" /> Generate Speech
                  </>
                )}
              </button>

              <button
                type="button"
                onClick={handleStop}
                className="flex items-center gap-2 bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-300 px-4 py-2.5 rounded-lg text-sm font-medium transition"
              >
                <Square className="w-4 h-4" /> Stop Audio
              </button>
            </div>
          </form>

          <div className="p-3 bg-slate-900/40 border border-slate-800/80 rounded-lg text-[11px] text-slate-500 flex items-center justify-between">
            <span>Audio Format: 24kHz Mono 16-bit Signed PCM</span>
            <span>Stream latency: &lt;180ms</span>
          </div>
        </div>
      </div>
    </div>
  );
};
