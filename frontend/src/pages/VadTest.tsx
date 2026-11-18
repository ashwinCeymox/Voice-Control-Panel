import React, { useState, useEffect, useRef } from 'react';
import { Mic, Play, Pause, Download, Volume2, AlertCircle, RefreshCw, CheckCircle2 } from 'lucide-react';
import { MicVAD, type RealTimeVADOptions } from '@ricky0123/vad-web';

interface CapturedSegment {
  id: string;
  timestamp: string;
  audio: Float32Array;
  durationMs: number;
  rms: number;
  samples: number;
}

interface VadEventLog {
  id: string;
  time: string;
  type: 'speech_start' | 'speech_real_start' | 'speech_end' | 'misfire' | 'error';
  detail: string;
}

export const VadTest: React.FC = () => {
  const [isActive, setIsActive] = useState<boolean>(false);
  const [modelType, setModelType] = useState<'v5' | 'v6' | 'legacy'>('v5');
  const [speechProb, setSpeechProb] = useState<number>(0);
  const [isSpeaking, setIsSpeaking] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // VAD Parameters
  const [posThreshold, setPosThreshold] = useState<number>(0.45);
  const [negThreshold, setNegThreshold] = useState<number>(0.30);
  const [redemptionMs, setRedemptionMs] = useState<number>(800);
  const [preSpeechPadMs, setPreSpeechPadMs] = useState<number>(200);
  const [minSpeechMs, setMinSpeechMs] = useState<number>(250);
  const [minRms, setMinRms] = useState<number>(0.015);

  // Event log & segments
  const [events, setEvents] = useState<VadEventLog[]>([]);
  const [segments, setSegments] = useState<CapturedSegment[]>([]);

  const vadRef = useRef<MicVAD | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);

  const addEvent = (type: VadEventLog['type'], detail: string) => {
    const entry: VadEventLog = {
      id: Math.random().toString(36).substring(2, 9),
      time: new Date().toLocaleTimeString(),
      type,
      detail,
    };
    setEvents((prev) => [entry, ...prev.slice(0, 49)]);
  };

  const calculateRMS = (samples: Float32Array): number => {
    let sum = 0;
    for (let i = 0; i < samples.length; i++) {
      sum += samples[i] * samples[i];
    }
    return Math.sqrt(sum / samples.length);
  };

  const startVAD = async () => {
    try {
      setErrorMsg(null);
      if (vadRef.current) {
        await vadRef.current.destroy();
        vadRef.current = null;
      }

      addEvent('speech_start', `Initializing Silero VAD (${modelType}) from /vad/...`);

      const vadBase = `${import.meta.env.BASE_URL}vad/`.replace(/\/+/g, '/');
      const vad = await MicVAD.new({
        model: modelType,
        baseAssetPath: vadBase,
        onnxWASMBasePath: vadBase,
        positiveSpeechThreshold: posThreshold,
        negativeSpeechThreshold: negThreshold,
        redemptionMs: redemptionMs,
        preSpeechPadMs: preSpeechPadMs,
        minSpeechMs: minSpeechMs,
        startOnLoad: true,
        ortConfig: (ort: any) => {
          ort.env.logLevel = 'error';
          if (ort.env.wasm) {
            ort.env.wasm.numThreads = 1;
            ort.env.wasm.simd = true;
          }
        },
        onFrameProcessed: (probs: any) => {
          const prob = probs.isSpeech ?? (probs as any)[1] ?? 0;
          setSpeechProb(prob);
        },
        onSpeechStart: () => {
          setIsSpeaking(true);
          addEvent('speech_start', 'Speech started (positiveSpeechThreshold exceeded)');
        },
        onSpeechRealStart: () => {
          addEvent('speech_real_start', 'Real speech confirmed (not a momentary blip)');
        },
        onVADMisfire: () => {
          setIsSpeaking(false);
          addEvent('misfire', `Misfire: sound was shorter than minSpeechMs (${minSpeechMs}ms)`);
        },
        onSpeechEnd: (audio: Float32Array) => {
          setIsSpeaking(false);
          const duration = Math.round((audio.length / 16000) * 1000);
          const rms = calculateRMS(audio);
          const isGated = rms < minRms;

          addEvent(
            'speech_end',
            `Speech ended: ${duration}ms (${audio.length} samples), RMS: ${rms.toFixed(4)}${
              isGated ? ' [DROPPED by min_rms gate]' : ' [ACCEPTED]'
            }`
          );

          if (!isGated) {
            const seg: CapturedSegment = {
              id: Math.random().toString(36).substring(2, 9),
              timestamp: new Date().toLocaleTimeString(),
              audio,
              durationMs: duration,
              rms,
              samples: audio.length,
            };
            setSegments((prev) => [seg, ...prev.slice(0, 19)]);
          }
        },
      } as Partial<RealTimeVADOptions>);

      vadRef.current = vad;
      setIsActive(true);
      addEvent('speech_real_start', 'Silero VAD running locally without internet.');
    } catch (err: any) {
      console.error('Failed to init Silero VAD:', err);
      setErrorMsg(err.message || 'Failed to start Silero VAD. Verify assets in /vad/.');
      setIsActive(false);
    }
  };

  const stopVAD = async () => {
    if (vadRef.current) {
      try {
        await vadRef.current.destroy();
      } catch {}
      vadRef.current = null;
    }
    setIsActive(false);
    setIsSpeaking(false);
    setSpeechProb(0);
    addEvent('speech_end', 'Silero VAD stopped.');
  };

  // Update options live if VAD is running
  useEffect(() => {
    if (vadRef.current && isActive) {
      vadRef.current.setOptions({
        positiveSpeechThreshold: posThreshold,
        negativeSpeechThreshold: negThreshold,
        redemptionMs,
        preSpeechPadMs,
        minSpeechMs,
      });
    }
  }, [posThreshold, negThreshold, redemptionMs, preSpeechPadMs, minSpeechMs, isActive]);

  useEffect(() => {
    return () => {
      if (vadRef.current) {
        vadRef.current.destroy().catch(() => {});
      }
    };
  }, []);

  const playSegment = (samples: Float32Array) => {
    try {
      if (!audioCtxRef.current || audioCtxRef.current.state === 'closed') {
        const AudioCtx = window.AudioContext || (window as any).webkitAudioContext;
        audioCtxRef.current = new AudioCtx({ sampleRate: 16000 });
      }
      const ctx = audioCtxRef.current;
      const buffer = ctx.createBuffer(1, samples.length, 16000);
      buffer.copyToChannel(new Float32Array(samples), 0);
      const source = ctx.createBufferSource();
      source.buffer = buffer;
      source.connect(ctx.destination);
      source.start();
    } catch (e) {
      console.error('Playback error:', e);
    }
  };

  const downloadWav = (samples: Float32Array, filename: string) => {
    // 16-bit PCM WAV encoding at 16000Hz
    const numChannels = 1;
    const sampleRate = 16000;
    const bytesPerSample = 2;
    const blockAlign = numChannels * bytesPerSample;
    const byteRate = sampleRate * blockAlign;
    const dataSize = samples.length * bytesPerSample;

    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    // RIFF header
    const writeString = (offset: number, str: string) => {
      for (let i = 0; i < str.length; i++) {
        view.setUint8(offset + i, str.charCodeAt(i));
      }
    };

    writeString(0, 'RIFF');
    view.setUint32(4, 36 + dataSize, true);
    writeString(8, 'WAVE');
    writeString(12, 'fmt ');
    view.setUint32(16, 16, true); // Subchunk1Size (16 for PCM)
    view.setUint16(20, 1, true); // AudioFormat (1 for PCM)
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, 16, true); // BitsPerSample
    writeString(36, 'data');
    view.setUint32(40, dataSize, true);

    // PCM samples
    let offset = 44;
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
      offset += 2;
    }

    const blob = new Blob([buffer], { type: 'audio/wav' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 pb-4">
        <div>
          <h1 className="text-2xl font-bold text-white flex items-center gap-3">
            <Mic className="w-6 h-6 text-sky-400" />
            Silero VAD Spike Lab (Phase 1)
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Test browser-side Silero neural voice activity detection with offline local ONNX assets.
          </p>
        </div>

        <div className="flex items-center gap-3">
          <select
            value={modelType}
            disabled={isActive}
            onChange={(e) => setModelType(e.target.value as any)}
            className="bg-slate-900 border border-slate-700 text-slate-200 text-sm rounded-lg px-3 py-2"
          >
            <option value="v5">Silero VAD v5 (Recommended)</option>
            <option value="v6">Silero VAD v6</option>
            <option value="legacy">Silero VAD Legacy</option>
          </select>

          {isActive ? (
            <button
              onClick={stopVAD}
              className="flex items-center gap-2 px-4 py-2 bg-red-600 hover:bg-red-500 text-white rounded-lg text-sm font-semibold shadow"
            >
              <Pause className="w-4 h-4" /> Stop VAD
            </button>
          ) : (
            <button
              onClick={startVAD}
              className="flex items-center gap-2 px-5 py-2 bg-sky-600 hover:bg-sky-500 text-white rounded-lg text-sm font-semibold shadow"
            >
              <Mic className="w-4 h-4" /> Start VAD
            </button>
          )}
        </div>
      </div>

      {errorMsg && (
        <div className="bg-red-950/60 border border-red-800 text-red-200 p-4 rounded-xl text-sm flex items-center gap-3">
          <AlertCircle className="w-5 h-5 text-red-400 shrink-0" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Live Speech Indicator & Meter */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 flex flex-col justify-between">
          <span className="text-xs font-mono uppercase tracking-wider text-slate-400">VAD State</span>
          <div className="flex items-center gap-3 mt-3">
            <span
              className={`w-4 h-4 rounded-full ${
                !isActive
                  ? 'bg-slate-600'
                  : isSpeaking
                  ? 'bg-emerald-400 animate-ping'
                  : 'bg-sky-400 animate-pulse'
              }`}
            />
            <span className="text-xl font-bold text-white">
              {!isActive ? 'IDLE' : isSpeaking ? 'SPEECH DETECTED' : 'LISTENING (SILENCE)'}
            </span>
          </div>
          <span className="text-xs text-slate-500 mt-2">
            Sampling: 16 kHz Float32 | Assets: /vad/
          </span>
        </div>

        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 md:col-span-2 space-y-3">
          <div className="flex justify-between items-center">
            <span className="text-xs font-mono uppercase tracking-wider text-slate-400">
              Live Speech Probability
            </span>
            <span className="text-sm font-mono font-bold text-sky-400">
              {(speechProb * 100).toFixed(1)}%
            </span>
          </div>
          <div className="w-full bg-slate-800 rounded-full h-4 overflow-hidden border border-slate-700">
            <div
              className={`h-full transition-all duration-75 ${
                speechProb > posThreshold
                  ? 'bg-emerald-500'
                  : speechProb > negThreshold
                  ? 'bg-yellow-500'
                  : 'bg-slate-600'
              }`}
              style={{ width: `${Math.min(100, Math.max(0, speechProb * 100))}%` }}
            />
          </div>
          <div className="flex justify-between text-[11px] font-mono text-slate-500">
            <span>0.0 (Silence)</span>
            <span className="text-yellow-400">Neg: {negThreshold}</span>
            <span className="text-emerald-400">Pos: {posThreshold}</span>
            <span>1.0 (Definite Speech)</span>
          </div>
        </div>
      </div>

      {/* Live Parameter Tuning Sliders */}
      <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-6 space-y-5">
        <h2 className="text-base font-semibold text-white flex items-center gap-2">
          <Volume2 className="w-5 h-5 text-indigo-400" />
          VAD Real-Time Parameters (Live Update)
        </h2>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 text-sm">
          {/* Positive Threshold */}
          <div className="space-y-2">
            <div className="flex justify-between font-mono text-xs text-slate-300">
              <span>positiveSpeechThreshold</span>
              <span className="text-emerald-400 font-bold">{posThreshold.toFixed(2)}</span>
            </div>
            <input
              type="range"
              min="0.10"
              max="0.90"
              step="0.05"
              value={posThreshold}
              onChange={(e) => setPosThreshold(parseFloat(e.target.value))}
              className="w-full accent-emerald-500"
            />
            <p className="text-xs text-slate-500">
              Probability frame must exceed to declare speech start.
            </p>
          </div>

          {/* Negative Threshold */}
          <div className="space-y-2">
            <div className="flex justify-between font-mono text-xs text-slate-300">
              <span>negativeSpeechThreshold</span>
              <span className="text-yellow-400 font-bold">{negThreshold.toFixed(2)}</span>
            </div>
            <input
              type="range"
              min="0.05"
              max="0.80"
              step="0.05"
              value={negThreshold}
              onChange={(e) => setNegThreshold(parseFloat(e.target.value))}
              className="w-full accent-yellow-500"
            />
            <p className="text-xs text-slate-500">
              Probability frames must drop below to initiate silence countdown.
            </p>
          </div>

          {/* Redemption Ms */}
          <div className="space-y-2">
            <div className="flex justify-between font-mono text-xs text-slate-300">
              <span>redemptionMs (Silence wait)</span>
              <span className="text-sky-400 font-bold">{redemptionMs} ms</span>
            </div>
            <input
              type="range"
              min="300"
              max="2000"
              step="50"
              value={redemptionMs}
              onChange={(e) => setRedemptionMs(parseInt(e.target.value))}
              className="w-full accent-sky-500"
            />
            <p className="text-xs text-slate-500">
              Duration of silence before speech is officially committed.
            </p>
          </div>

          {/* Pre-speech padding */}
          <div className="space-y-2">
            <div className="flex justify-between font-mono text-xs text-slate-300">
              <span>preSpeechPadMs</span>
              <span className="text-purple-400 font-bold">{preSpeechPadMs} ms</span>
            </div>
            <input
              type="range"
              min="50"
              max="1000"
              step="50"
              value={preSpeechPadMs}
              onChange={(e) => setPreSpeechPadMs(parseInt(e.target.value))}
              className="w-full accent-purple-500"
            />
            <p className="text-xs text-slate-500">
              Audio prepended before start to avoid clipping first consonant.
            </p>
          </div>

          {/* Min speech ms */}
          <div className="space-y-2">
            <div className="flex justify-between font-mono text-xs text-slate-300">
              <span>minSpeechMs (Misfire filter)</span>
              <span className="text-pink-400 font-bold">{minSpeechMs} ms</span>
            </div>
            <input
              type="range"
              min="100"
              max="800"
              step="50"
              value={minSpeechMs}
              onChange={(e) => setMinSpeechMs(parseInt(e.target.value))}
              className="w-full accent-pink-500"
            />
            <p className="text-xs text-slate-500">
              Shorter sounds (coughs, clicks) trigger onVADMisfire.
            </p>
          </div>

          {/* Min RMS volume gate */}
          <div className="space-y-2">
            <div className="flex justify-between font-mono text-xs text-slate-300">
              <span>minRms (Volume gate)</span>
              <span className="text-cyan-400 font-bold">{minRms.toFixed(3)}</span>
            </div>
            <input
              type="range"
              min="0.005"
              max="0.080"
              step="0.005"
              value={minRms}
              onChange={(e) => setMinRms(parseFloat(e.target.value))}
              className="w-full accent-cyan-500"
            />
            <p className="text-xs text-slate-500">
              Drops segment if overall energy is below threshold (ambient breath/hum).
            </p>
          </div>
        </div>
      </div>

      {/* Captured Speech Segments & Event Log */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Segments */}
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
              Captured Speech Segments ({segments.length})
            </h3>
            {segments.length > 0 && (
              <button
                onClick={() => setSegments([])}
                className="text-xs text-slate-400 hover:text-slate-200"
              >
                Clear
              </button>
            )}
          </div>

          {segments.length === 0 ? (
            <div className="text-xs text-slate-500 text-center py-10">
              No speech segments committed yet. Start VAD and speak into the mic.
            </div>
          ) : (
            <div className="space-y-3 max-h-96 overflow-y-auto pr-1">
              {segments.map((seg) => (
                <div
                  key={seg.id}
                  className="bg-slate-950 border border-slate-800 p-3 rounded-lg flex items-center justify-between"
                >
                  <div className="space-y-1">
                    <div className="flex items-center gap-2">
                      <span className="text-xs font-mono text-emerald-400 font-semibold">
                        {seg.durationMs}ms
                      </span>
                      <span className="text-[11px] text-slate-500">{seg.timestamp}</span>
                    </div>
                    <div className="text-[11px] font-mono text-slate-400">
                      RMS: {seg.rms.toFixed(4)} | {seg.samples} samples (16kHz)
                    </div>
                  </div>

                  <div className="flex items-center gap-2">
                    <button
                      onClick={() => playSegment(seg.audio)}
                      className="p-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg transition"
                      title="Play Audio"
                    >
                      <Play className="w-3.5 h-3.5" />
                    </button>
                    <button
                      onClick={() => downloadWav(seg.audio, `vad-segment-${seg.id}.wav`)}
                      className="p-2 bg-slate-800 hover:bg-slate-700 text-slate-300 rounded-lg transition"
                      title="Download WAV"
                    >
                      <Download className="w-3.5 h-3.5" />
                    </button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Event Log */}
        <div className="bg-slate-900/80 border border-slate-800 rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between">
            <h3 className="text-sm font-semibold text-slate-200 flex items-center gap-2">
              <RefreshCw className="w-4 h-4 text-sky-400" />
              Live VAD Event Stream ({events.length})
            </h3>
            {events.length > 0 && (
              <button
                onClick={() => setEvents([])}
                className="text-xs text-slate-400 hover:text-slate-200"
              >
                Clear
              </button>
            )}
          </div>

          <div className="space-y-2 max-h-96 overflow-y-auto font-mono text-xs pr-1">
            {events.length === 0 ? (
              <div className="text-slate-500 text-center py-10">Waiting for VAD events...</div>
            ) : (
              events.map((ev) => (
                <div
                  key={ev.id}
                  className={`p-2 rounded border ${
                    ev.type === 'speech_start'
                      ? 'bg-emerald-950/40 border-emerald-800/60 text-emerald-300'
                      : ev.type === 'speech_real_start'
                      ? 'bg-sky-950/40 border-sky-800/60 text-sky-300'
                      : ev.type === 'misfire'
                      ? 'bg-yellow-950/40 border-yellow-800/60 text-yellow-300'
                      : ev.type === 'error'
                      ? 'bg-red-950/40 border-red-800/60 text-red-300'
                      : 'bg-slate-950 border-slate-800 text-slate-300'
                  }`}
                >
                  <span className="text-[10px] text-slate-500 mr-2">{ev.time}</span>
                  <span className="font-semibold uppercase text-[10px] mr-2">[{ev.type}]</span>
                  <span>{ev.detail}</span>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
};
