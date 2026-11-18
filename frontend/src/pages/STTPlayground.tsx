import React, { useState, useEffect, useRef } from 'react';
import {
  Upload,
  AudioLines,
  RefreshCw,
  CheckCircle2,
  Mic,
  Square,
  AlertCircle,
  Zap,
  Radio,
  Sparkles,
} from 'lucide-react';
import { TranscriptResponse } from '../types/api';
import { getApiBase, getApiKey, getWsBase } from '../api/client';
import { float32ToPCM16 } from '../audio/vadController';
import { AudioResampler, StreamingAudioChunker } from '../audio/resampler';

const API_BASE = getApiBase();
const AUTH_HEADERS = {
  get Authorization() {
    return `Bearer ${getApiKey()}`;
  },
};

interface STTModelItem {
  id: string;
  name: string;
  kind: 'batch' | 'streaming';
  provider?: string;
  available?: boolean;
  unavailable_reason?: string;
}

export const STTPlayground: React.FC = () => {
  const [mode, setMode] = useState<'batch' | 'streaming'>('streaming');
  const [loading, setLoading] = useState<boolean>(false);
  const [result, setResult] = useState<TranscriptResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Model & Language options
  const [installedModels, setInstalledModels] = useState<STTModelItem[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [language, setLanguage] = useState<string>('en'); // Lock default to English
  const [chunkSizeMs, setChunkSizeMs] = useState<number>(560); // 560ms optimal lookahead context
  const [hardwareRate, setHardwareRate] = useState<number>(16000);

  // Mic recording state (Batch)
  const [isRecording, setIsRecording] = useState<boolean>(false);
  const [recordSeconds, setRecordSeconds] = useState<number>(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<any>(null);

  // Live Streaming state (Streaming)
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [liveConfirmed, setLiveConfirmed] = useState<string>('');
  const [liveTentative, setLiveTentative] = useState<string>('');
  const wsStreamRef = useRef<WebSocket | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const scriptProcessorRef = useRef<ScriptProcessorNode | null>(null);
  const chunkerRef = useRef<StreamingAudioChunker | null>(null);
  const streamStartTimeRef = useRef<number>(0);

  // Timing telemetry
  const [telemetry, setTelemetry] = useState<{
    latencyMs: number;
    durationSec: number;
    rtf: number;
  } | null>(null);
  const [reclassifying, setReclassifying] = useState<boolean>(false);
  const [fallbackNotice, setFallbackNotice] = useState<string | null>(null);
  const [streamModelTelemetry, setStreamModelTelemetry] = useState<{
    modelUsed?: string;
    runtime?: string;
    fallback?: boolean;
  } | null>(null);

  const [fetchingModels, setFetchingModels] = useState<boolean>(false);

  useEffect(() => {
    fetchInstalledSTT();
    const onFocus = () => fetchInstalledSTT();
    window.addEventListener('focus', onFocus);
    return () => window.removeEventListener('focus', onFocus);
  }, []);

  const fetchInstalledSTT = async () => {
    try {
      setFetchingModels(true);
      const res = await fetch(`${API_BASE}/v1/models?task=stt&available=true`, { headers: AUTH_HEADERS });
      if (res.ok) {
        const data = await res.json();
        const rawList = data.data || data.models || (Array.isArray(data) ? data : []);
        const sttList: STTModelItem[] = rawList.map((m: any) => ({
          id: m.id,
          name: m.display_name || m.name || m.id,
          kind: m.kind === 'streaming' ? 'streaming' : 'batch',
          provider: m.engine,
          available: m.available !== false,
          unavailable_reason: m.unavailable_reason,
        }));
        setInstalledModels(sttList);
      }
    } catch (err) {
      console.warn('Failed to fetch installed models:', err);
    } finally {
      setFetchingModels(false);
    }
  };

  const handleToggleClassification = async (modelId: string) => {
    if (!modelId) return;
    const targetKind: 'batch' | 'streaming' = mode === 'streaming' ? 'batch' : 'streaming';
    try {
      setReclassifying(true);
      setError(null);

      // Optimistic update of installedModels
      setInstalledModels((prev) =>
        prev.map((m) => (m.id === modelId ? { ...m, kind: targetKind } : m))
      );

      const res = await fetch(`${API_BASE}/v1/models/${encodeURIComponent(modelId)}/classification`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
        body: JSON.stringify({
          task: 'stt',
          kind: targetKind,
        }),
      });

      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        throw new Error(err.detail || err.error?.message || 'Failed to update model classification');
      }

      setMode(targetKind);
      setSelectedModel(modelId);
    } catch (err: any) {
      console.error('Classification toggle error:', err);
      setError(err?.message || 'Failed to reclassify model');
      fetchInstalledSTT();
    } finally {
      setReclassifying(false);
    }
  };

  // Sync selectedModel whenever mode or installedModels change
  useEffect(() => {
    const available = installedModels.filter((m) => m.kind === mode);
    if (available.length > 0) {
      const exists = available.some((m) => m.id === selectedModel);
      if (!exists) {
        setSelectedModel(available[0].id);
      }
    } else {
      setSelectedModel('');
    }
  }, [mode, installedModels]);

  // Clean up streaming / recording on unmount
  useEffect(() => {
    return () => {
      stopRecording();
      stopLiveStreaming();
    };
  }, []);

  // =========================================================================
  // Batch Mode Handlers
  // =========================================================================
  const handleTranscribeBatchFile = async (blob: Blob, filename = 'recording.webm') => {
    try {
      setLoading(true);
      setError(null);
      setResult(null);

      const startTime = performance.now();
      const formData = new FormData();
      formData.append('file', blob, filename);
      if (selectedModel) formData.append('model', selectedModel);
      if (language !== 'auto') formData.append('language', language);

      const res = await fetch(`${API_BASE}/v1/audio/transcriptions`, {
        method: 'POST',
        headers: AUTH_HEADERS,
        body: formData,
      });

      const latencyMs = Math.round(performance.now() - startTime);

      if (!res.ok) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData?.error?.message || errData?.detail || 'Transcription failed');
      }

      const data: TranscriptResponse = await res.json();
      setResult(data);

      const dur = data.duration || recordSeconds || 2.5;
      const rtf = (latencyMs / 1000) / (dur || 1);
      setTelemetry({
        latencyMs,
        durationSec: dur,
        rtf: parseFloat(rtf.toFixed(2)),
      });
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error transcribing audio');
    } finally {
      setLoading(false);
    }
  };

  const startBatchRecording = async () => {
    try {
      setError(null);
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      audioChunksRef.current = [];
      const mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0) audioChunksRef.current.push(e.data);
      };

      mediaRecorder.onstop = () => {
        stream.getTracks().forEach((t) => t.stop());
        const audioBlob = new Blob(audioChunksRef.current, { type: 'audio/webm' });
        handleTranscribeBatchFile(audioBlob, 'mic-recording.webm');
      };

      mediaRecorder.start();
      setIsRecording(true);
      setRecordSeconds(0);
      timerRef.current = setInterval(() => {
        setRecordSeconds((prev) => prev + 1);
      }, 1000);
    } catch (err: any) {
      setError(`Microphone access error: ${err.message}`);
    }
  };

  const stopRecording = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      mediaRecorderRef.current.stop();
    }
    clearInterval(timerRef.current);
    setIsRecording(false);
  };

  // =========================================================================
  // Streaming Mode Handlers
  // =========================================================================
  const startLiveStreaming = async () => {
    try {
      setError(null);
      setFallbackNotice(null);
      setStreamModelTelemetry(null);
      setResult(null);
      setLiveConfirmed('');
      setLiveTentative('');
      setTelemetry(null);

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: 16000,
          sampleSize: 16,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      mediaStreamRef.current = stream;

      const AudioContextClass = window.AudioContext || (window as any).webkitAudioContext;
      const audioCtx = new AudioContextClass();
      audioContextRef.current = audioCtx;
      setHardwareRate(audioCtx.sampleRate);

      // Dedicated real-time linear resampler from macOS mic rate (44.1k/48k) to 16kHz
      const resampler = new AudioResampler(audioCtx.sampleRate, 16000);
      // Lookahead streaming chunk buffer (560ms–1120ms)
      const chunker = new StreamingAudioChunker(16000, chunkSizeMs);
      chunkerRef.current = chunker;

      const source = audioCtx.createMediaStreamSource(stream);
      const processor = audioCtx.createScriptProcessor(4096, 1, 1);
      scriptProcessorRef.current = processor;

      const wsUrl = `${getWsBase()}/v1/audio/transcriptions/stream?api_key=${encodeURIComponent(getApiKey())}`;
      const ws = new WebSocket(wsUrl);
      wsStreamRef.current = ws;

      streamStartTimeRef.current = performance.now();
      setIsStreaming(true);
      setRecordSeconds(0);
      timerRef.current = setInterval(() => {
        setRecordSeconds((prev) => prev + 1);
      }, 1000);

      ws.onopen = () => {
        // Send stream handshake with explicit language lock and context window
        const streamModelId =
          selectedModel ||
          installedModels.find((m) => m.kind === 'streaming' && m.available !== false)?.id ||
          installedModels.find((m) => m.available !== false)?.id ||
          '';
        ws.send(
          JSON.stringify({
            type: 'stream.start',
            model: streamModelId,
            sample_rate: 16000,
            format: 'pcm_s16le',
            language: language || 'en',
            chunk_ms: chunkSizeMs,
          })
        );

        processor.onaudioprocess = (e) => {
          if (ws.readyState === WebSocket.OPEN) {
            const inputData = e.inputBuffer.getChannelData(0);
            // 1. Properly resample live mic input to 16kHz
            const resampled16k = resampler.resample(inputData);
            if (resampled16k.length > 0) {
              // 2. Accumulate in 560ms–1120ms context window before sending
              const chunks = chunker.push(resampled16k);
              for (const chunk of chunks) {
                const pcmBytes = float32ToPCM16(chunk);
                ws.send(pcmBytes.buffer);
              }
            }
          }
        };

        source.connect(processor);
        processor.connect(audioCtx.destination);
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'transcript.partial') {
            setLiveConfirmed(data.confirmed || '');
            setLiveTentative(data.tentative || '');
          } else if (data.type === 'transcript.completed' || data.type === 'transcript.final') {
            const finalText = data.text || '';
            setResult({ text: finalText, language: data.language || 'en', model: data.model_used || data.model });
            setLiveConfirmed(finalText);
            setLiveTentative('');
            setStreamModelTelemetry({
              modelUsed: data.model_used || data.model,
              runtime: data.runtime || 'sherpa-onnx',
              fallback: Boolean(data.fallback),
            });
            if (data.fallback) {
              setFallbackNotice(`STT Fallback: Model '${data.model_used || data.model}' substituted`);
            }
            const latencyMs = Math.round(performance.now() - streamStartTimeRef.current);
            const dur = Math.max(1, (performance.now() - streamStartTimeRef.current) / 1000);
            const rtf = (latencyMs / 1000) / (dur || 1);
            setTelemetry({
              latencyMs,
              durationSec: parseFloat(dur.toFixed(1)),
              rtf: parseFloat(rtf.toFixed(2)),
            });
          } else if (data.type === 'stt.fallback') {
            setFallbackNotice(`STT Fallback Triggered: ${data.reason || 'Service substituted model'}`);
          } else if (data.type === 'error') {
            setError(data.code ? `[${data.code}] ${data.message}` : (data.message || 'Streaming STT error'));
          }
        } catch (err) {
          console.error('Error parsing stream event:', err);
        }
      };

      ws.onerror = (err) => {
        console.error('WebSocket streaming error:', err);
        setError('WebSocket streaming error. Ensure streaming STT service is online.');
      };

      ws.onclose = () => {
        setIsStreaming(false);
      };
    } catch (err: any) {
      setError(`Failed to start streaming: ${err.message}`);
      stopLiveStreaming();
    }
  };

  const stopLiveStreaming = () => {
    clearInterval(timerRef.current);

    if (scriptProcessorRef.current) {
      try {
        scriptProcessorRef.current.disconnect();
      } catch {}
      scriptProcessorRef.current = null;
    }

    if (audioContextRef.current) {
      try {
        audioContextRef.current.close();
      } catch {}
      audioContextRef.current = null;
    }

    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }

    if (wsStreamRef.current && wsStreamRef.current.readyState === WebSocket.OPEN) {
      // Flush residual 16kHz lookahead buffer
      if (chunkerRef.current) {
        const remaining = chunkerRef.current.flush();
        if (remaining && remaining.length > 0) {
          const pcmBytes = float32ToPCM16(remaining);
          wsStreamRef.current.send(pcmBytes.buffer);
        }
      }
      wsStreamRef.current.send(JSON.stringify({ type: 'audio.commit' }));
      setTimeout(() => {
        if (wsStreamRef.current && wsStreamRef.current.readyState === WebSocket.OPEN) {
          wsStreamRef.current.close();
        }
        wsStreamRef.current = null;
      }, 400);
    }

    setIsStreaming(false);
  };

  const streamAudioFile = async (file: File) => {
    try {
      setLoading(true);
      setError(null);
      setResult(null);
      setLiveConfirmed('');
      setLiveTentative('');
      setTelemetry(null);

      const arrayBuffer = await file.arrayBuffer();
      const audioCtx = new (window.AudioContext || (window as any).webkitAudioContext)();
      const audioBuffer = await audioCtx.decodeAudioData(arrayBuffer);

      // Resample to 16kHz mono Float32Array using OfflineAudioContext
      const offlineCtx = new OfflineAudioContext(1, Math.ceil(audioBuffer.duration * 16000), 16000);
      const src = offlineCtx.createBufferSource();
      src.buffer = audioBuffer;
      src.connect(offlineCtx.destination);
      src.start();
      const resampled = await offlineCtx.startRendering();
      const channelData = resampled.getChannelData(0);
      const pcmData = float32ToPCM16(channelData);

      const wsUrl = `${getWsBase()}/v1/audio/transcriptions/stream?api_key=${encodeURIComponent(getApiKey())}`;
      const ws = new WebSocket(wsUrl);
      wsStreamRef.current = ws;

      const startTime = performance.now();
      setIsStreaming(true);

      ws.onopen = () => {
        const streamModelId =
          selectedModel ||
          installedModels.find((m) => m.kind === 'streaming' && m.available !== false)?.id ||
          installedModels.find((m) => m.available !== false)?.id ||
          '';
        ws.send(
          JSON.stringify({
            type: 'stream.start',
            model: streamModelId,
            sample_rate: 16000,
            format: 'pcm_s16le',
            language: language || 'en',
            chunk_ms: chunkSizeMs,
          })
        );

        // Stream chunks with chosen lookahead context (e.g. 560ms = 17,920 bytes)
        const chunkByteSize = Math.round((16000 * (chunkSizeMs / 1000.0)) * 2);
        let offset = 0;
        const interval = setInterval(() => {
          if (offset >= pcmData.byteLength || ws.readyState !== WebSocket.OPEN) {
            clearInterval(interval);
            if (ws.readyState === WebSocket.OPEN) {
              ws.send(JSON.stringify({ type: 'audio.commit' }));
            }
            return;
          }
          const slice = pcmData.subarray(offset, Math.min(offset + chunkByteSize, pcmData.byteLength));
          ws.send(slice.buffer);
          offset += chunkByteSize;
        }, Math.max(100, Math.round(chunkSizeMs * 0.4)));
      };

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data);
          if (data.type === 'transcript.partial') {
            setLiveConfirmed(data.confirmed || '');
            setLiveTentative(data.tentative || '');
          } else if (data.type === 'transcript.completed' || data.type === 'transcript.final') {
            const finalText = data.text || '';
            setResult({ text: finalText, language: data.language || 'en', model: data.model_used || data.model });
            setLiveConfirmed(finalText);
            setLiveTentative('');
            setStreamModelTelemetry({
              modelUsed: data.model_used || data.model,
              runtime: data.runtime || 'sherpa-onnx',
              fallback: Boolean(data.fallback),
            });
            if (data.fallback) {
              setFallbackNotice(`STT Fallback: Model '${data.model_used || data.model}' substituted`);
            }
            const latencyMs = Math.round(performance.now() - startTime);
            const dur = audioBuffer.duration;
            const rtf = (latencyMs / 1000) / (dur || 1);
            setTelemetry({
              latencyMs,
              durationSec: dur,
              rtf: parseFloat(rtf.toFixed(2)),
            });
            setIsStreaming(false);
            setLoading(false);
            ws.close();
          } else if (data.type === 'stt.fallback') {
            setFallbackNotice(`STT Fallback Triggered: ${data.reason || 'Service substituted model'}`);
          } else if (data.type === 'error') {
            setError(data.code ? `[${data.code}] ${data.message}` : (data.message || 'Streaming STT error'));
            setIsStreaming(false);
            setLoading(false);
          }
        } catch (err) {
          console.error('Error in file stream:', err);
        }
      };

      ws.onerror = () => {
        setError('WebSocket error during file streaming');
        setIsStreaming(false);
        setLoading(false);
      };
    } catch (err: any) {
      setError(`Failed to stream audio file: ${err.message}`);
      setIsStreaming(false);
      setLoading(false);
    }
  };

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    if (mode === 'streaming') {
      streamAudioFile(file);
    } else {
      handleTranscribeBatchFile(file, file.name);
    }
  };

  const currentModels = installedModels.filter((m) => m.kind === mode && m.available !== false);

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Header and Mode Toggle */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-slate-100 flex items-center gap-2.5">
            <AudioLines className="w-7 h-7 text-sky-400" />
            Speech-to-Text Playground
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Test and benchmark real-time streaming STT (Sherpa-ONNX Zipformer) and batch STT (Whisper) models.
          </p>
        </div>

        {/* Mode Selector */}
        <div className="inline-flex rounded-xl p-1 bg-slate-950 border border-slate-800 text-xs shadow-inner">
          <button
            type="button"
            onClick={() => {
              if (isRecording) stopRecording();
              if (isStreaming) stopLiveStreaming();
              setMode('streaming');
            }}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg font-medium transition ${
              mode === 'streaming'
                ? 'bg-sky-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Zap className="w-3.5 h-3.5" />
            <span>Streaming STT</span>
          </button>
          <button
            type="button"
            onClick={() => {
              if (isRecording) stopRecording();
              if (isStreaming) stopLiveStreaming();
              setMode('batch');
            }}
            className={`flex items-center gap-2 px-3.5 py-1.5 rounded-lg font-medium transition ${
              mode === 'batch'
                ? 'bg-sky-600 text-white shadow-sm'
                : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            <Radio className="w-3.5 h-3.5" />
            <span>Batch STT (Whisper)</span>
          </button>
        </div>
      </div>

      {error && (
        <div className="p-3.5 bg-rose-500/10 border border-rose-500/30 rounded-xl flex items-center gap-2.5 text-sm text-rose-300">
          <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {currentModels.length === 0 && (
        <div className="p-4 bg-amber-500/10 border border-amber-500/30 rounded-xl flex items-center justify-between gap-3 text-xs text-amber-300">
          <div className="flex items-center gap-2">
            <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
            <span>
              No {mode} speech-to-text models found. Check the Model Registry to install {mode === 'streaming' ? 'Sherpa-ONNX Zipformer' : 'Whisper'}.
            </span>
          </div>
        </div>
      )}

      {/* Model & Language Controls Bar */}
      <div className="p-4 bg-slate-950 border border-slate-800 rounded-xl grid grid-cols-1 md:grid-cols-3 gap-4">
        <div>
          <label className="text-xs text-slate-300 font-medium block mb-1.5 flex items-center justify-between">
            <span className="flex items-center gap-1.5">
              <span>{mode === 'streaming' ? 'Streaming Model' : 'Batch Model'}</span>
              <button
                type="button"
                onClick={fetchInstalledSTT}
                disabled={fetchingModels || isStreaming || isRecording}
                title="Refresh installed models"
                className="text-slate-400 hover:text-sky-400 transition p-0.5 rounded"
              >
                <RefreshCw className={`w-3 h-3 ${fetchingModels ? 'animate-spin text-sky-400' : ''}`} />
              </button>
            </span>
            <span className="text-[10px] text-sky-400 uppercase font-mono px-1.5 py-0.5 rounded bg-sky-950/60 border border-sky-800/40">
              {mode}
            </span>
          </label>
          <select
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            disabled={currentModels.length === 0 || isStreaming || isRecording}
            className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-sky-500 disabled:opacity-50"
          >
            {currentModels.length === 0 ? (
              <option value="" disabled>No {mode} model installed</option>
            ) : (
              currentModels.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.name !== m.id ? `${m.name} (${m.id})` : m.id}
                </option>
              ))
            )}
          </select>

          {selectedModel && (
            <div className="flex items-center justify-between mt-2 pt-2 border-t border-slate-900 text-[11px]">
              <span className="text-slate-400">
                Assigned: <strong className="text-slate-200 uppercase font-mono">{mode}</strong>
              </span>
              <button
                type="button"
                disabled={isStreaming || isRecording || reclassifying}
                onClick={() => handleToggleClassification(selectedModel)}
                className="text-xs text-sky-400 hover:text-sky-300 font-medium flex items-center gap-1 hover:underline disabled:opacity-50"
                title={`Move ${selectedModel} to ${mode === 'streaming' ? 'Batch' : 'Streaming'} STT mode`}
              >
                {reclassifying && <RefreshCw className="w-3 h-3 animate-spin text-sky-400" />}
                Move to {mode === 'streaming' ? 'Batch' : 'Streaming'}
              </button>
            </div>
          )}
        </div>

        <div>
          <label className="text-xs text-slate-300 font-medium block mb-1.5 flex items-center justify-between">
            <span>Target Language</span>
            <span className="text-[10px] text-emerald-400 uppercase font-mono px-1.5 py-0.5 rounded bg-emerald-950/60 border border-emerald-800/40">
              {language === 'auto' ? 'Auto-Detect' : 'Locked'}
            </span>
          </label>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            disabled={isStreaming || isRecording}
            className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-sky-500 disabled:opacity-50"
          >
            <option value="en">English (en) - Recommended Lock</option>
            <option value="es">Spanish (es)</option>
            <option value="fr">French (fr)</option>
            <option value="de">German (de)</option>
            <option value="ja">Japanese (ja)</option>
            <option value="zh">Chinese (zh)</option>
            <option value="auto">Auto-detect Language</option>
          </select>
          <span className="text-[10px] text-slate-500 mt-1 block">
            {language !== 'auto'
              ? 'Explicit locale lock active (avoids burst auto-detection misfires).'
              : 'Auto-detection may misclassify short streaming bursts.'}
          </span>
        </div>

        <div>
          <label className="text-xs text-slate-300 font-medium block mb-1.5 flex items-center justify-between">
            <span>Streaming Context Window</span>
            <span className="text-[10px] text-indigo-400 uppercase font-mono px-1.5 py-0.5 rounded bg-indigo-950/60 border border-indigo-800/40">
              Lookahead
            </span>
          </label>
          <select
            value={chunkSizeMs}
            onChange={(e) => setChunkSizeMs(Number(e.target.value))}
            disabled={isStreaming || isRecording}
            className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-sky-500 disabled:opacity-50"
          >
            <option value={560}>560 ms (Recommended - 8.9k samples)</option>
            <option value={1120}>1120 ms (High Acoustic Context)</option>
            <option value={280}>280 ms (Ultra Low Latency)</option>
          </select>
          <span className="text-[10px] text-slate-500 mt-1 block">
            Accumulates sufficient right-context frames to prevent mid-phoneme truncation.
          </span>
        </div>
      </div>

      {/* Audio Pipeline Diagnostics Banner */}
      <div className="p-3 bg-slate-950 border border-slate-800/90 rounded-xl flex flex-wrap items-center justify-between gap-3 text-xs">
        <div className="flex items-center gap-2 font-mono">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
          <span className="text-slate-400">Audio Pipeline:</span>
          <span className="text-emerald-400 font-semibold">16kHz 16-bit Mono PCM</span>
          {hardwareRate !== 16000 && (
            <span className="text-[10px] text-sky-400 bg-sky-950/80 border border-sky-800/80 px-1.5 py-0.5 rounded font-sans">
              Live Resampler ({hardwareRate}Hz → 16kHz)
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-400 font-mono">
          <span>Language: <strong className="text-slate-200 uppercase">{language}</strong></span>
          <span>Chunk Window: <strong className="text-sky-400">{chunkSizeMs}ms</strong></span>
          <span>Engine Weights: <strong className="text-indigo-400 font-semibold">FP32/F16</strong></span>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Left: Input options (Mic recording + File upload) */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-5">
          <div className="flex items-center justify-between">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Audio Input ({mode === 'streaming' ? 'Real-Time Stream' : 'Batch File'})
            </h2>
            {isStreaming && (
              <span className="inline-flex items-center gap-1.5 text-[11px] font-semibold text-emerald-400 animate-pulse bg-emerald-500/10 border border-emerald-500/20 px-2.5 py-0.5 rounded-full">
                <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                LIVE STREAMING
              </span>
            )}
          </div>

          {/* Mic Action Button */}
          <div className="p-5 bg-slate-900/60 border border-slate-800 rounded-xl flex flex-col items-center justify-center space-y-3">
            <button
              onClick={() => {
                if (mode === 'streaming') {
                  if (isStreaming) stopLiveStreaming();
                  else startLiveStreaming();
                } else {
                  if (isRecording) stopRecording();
                  else startBatchRecording();
                }
              }}
              className={`p-4 rounded-full transition shadow-lg ${
                (isRecording || isStreaming)
                  ? 'bg-rose-600 hover:bg-rose-500 text-white animate-pulse'
                  : mode === 'streaming'
                  ? 'bg-indigo-600 hover:bg-indigo-500 text-white'
                  : 'bg-sky-600 hover:bg-sky-500 text-white'
              }`}
            >
              {(isRecording || isStreaming) ? (
                <Square className="w-6 h-6 fill-current" />
              ) : (
                <Mic className="w-6 h-6" />
              )}
            </button>
            <span className="text-xs font-medium text-slate-300">
              {mode === 'streaming'
                ? isStreaming
                  ? `Streaming live audio... ${recordSeconds}s (Click to finish)`
                  : 'Speak Live (Real-Time Streaming)'
                : isRecording
                ? `Recording... ${recordSeconds}s (Click to stop)`
                : 'Record via Microphone'}
            </span>
          </div>

          <div className="relative flex items-center justify-center">
            <div className="border-t border-slate-800 w-full" />
            <span className="bg-slate-950 px-3 text-[11px] uppercase tracking-wider text-slate-500">
              or upload audio file
            </span>
          </div>

          {/* File Upload Box */}
          <div className="border-2 border-dashed border-slate-800 rounded-xl p-6 text-center hover:border-slate-700 transition">
            <Upload className="w-7 h-7 mx-auto text-slate-500 mb-2" />
            <p className="text-xs text-slate-400 mb-1">WAV, WebM, MP3, FLAC, OGG</p>
            <p className="text-[11px] text-slate-500 mb-3">
              {mode === 'streaming'
                ? 'Streams file frames over WebSocket with real-time recognition'
                : 'Uploads entire file for batch transcription'}
            </p>
            <label className="px-4 py-2 bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-200 rounded-lg text-xs font-medium cursor-pointer inline-flex items-center gap-2">
              <input
                type="file"
                accept="audio/*"
                onChange={handleFileUpload}
                disabled={loading || isRecording || isStreaming}
                className="hidden"
              />
              {mode === 'streaming' ? 'Stream Audio File' : 'Select Audio File'}
            </label>
          </div>
        </div>

        {/* Right: Transcript Output & Telemetry */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 flex flex-col justify-between space-y-4">
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                Transcription Output
              </h2>
              {mode === 'streaming' && isStreaming && (
                <span className="text-[11px] text-sky-400 flex items-center gap-1 font-mono">
                  <Sparkles className="w-3.5 h-3.5 animate-spin" /> Live Transcribing
                </span>
              )}
            </div>

            {fallbackNotice && (
              <div className="p-3 bg-amber-500/10 border border-amber-500/30 rounded-lg text-xs text-amber-300 flex items-center gap-2">
                <AlertCircle className="w-4 h-4 text-amber-400 shrink-0" />
                <span>{fallbackNotice}</span>
              </div>
            )}

            {loading ? (
              <div className="p-12 text-center text-slate-500 text-xs flex items-center justify-center gap-2">
                <RefreshCw className="w-4 h-4 animate-spin text-sky-400" />
                {mode === 'streaming' ? 'Streaming audio frames...' : 'Transcribing audio file...'}
              </div>
            ) : isStreaming ? (
              /* Live Partial Transcripts */
              <div className="space-y-3">
                <div className="flex items-center gap-2 text-xs text-sky-400 font-medium">
                  <Radio className="w-3.5 h-3.5 animate-pulse" /> Live Captions:
                </div>
                <div className="bg-slate-900/90 border border-sky-500/30 rounded-lg p-4 text-sm leading-relaxed min-h-[90px]">
                  <span className="text-slate-100 font-medium">{liveConfirmed}</span>
                  {liveTentative && (
                    <span className="text-sky-400 italic ml-1 underline decoration-dotted">
                      {liveTentative}
                    </span>
                  )}
                  {!liveConfirmed && !liveTentative && (
                    <span className="text-slate-500 italic">Listening for speech...</span>
                  )}
                </div>
                <p className="text-[11px] text-slate-500 italic">
                  Solid words are confirmed. Highlighted tail updates dynamically in real-time.
                </p>
              </div>
            ) : result ? (
              /* Completed Output */
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-xs text-emerald-400 font-medium">
                  <CheckCircle2 className="w-4 h-4" /> Transcription Complete
                </div>
                <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-4 text-slate-100 text-sm leading-relaxed min-h-[90px]">
                  {result.text || <span className="text-slate-500 italic">(No speech detected)</span>}
                </div>
                <div className="text-xs text-slate-400 flex flex-wrap gap-4 pt-2 border-t border-slate-900">
                  <span>
                    Detected Language: <strong className="text-slate-200">{result.language || 'en'}</strong>
                  </span>
                  <span>
                    Model Used: <strong className="text-slate-200">{streamModelTelemetry?.modelUsed || result.model || selectedModel}</strong>
                  </span>
                  <span>
                    Runtime: <strong className="text-slate-200">{streamModelTelemetry?.runtime || (mode === 'streaming' ? 'sherpa-onnx' : 'localai')}</strong>
                  </span>
                  {streamModelTelemetry?.fallback && (
                    <span className="text-amber-400 font-semibold flex items-center gap-1">
                      <AlertCircle className="w-3.5 h-3.5" /> Fallback Substituted
                    </span>
                  )}
                </div>
              </div>
            ) : (
              <p className="text-xs text-slate-500 py-12 text-center italic">
                {mode === 'streaming'
                  ? 'Click "Speak Live" or upload a file to experience instant real-time speech recognition.'
                  : 'Record audio or upload a sample to test speech recognition with Whisper.'}
              </p>
            )}
          </div>

          {/* Timing Telemetry Readout */}
          {telemetry && (
            <div className="p-3 bg-slate-900/60 border border-slate-800/80 rounded-lg grid grid-cols-3 gap-2 text-center text-xs">
              <div>
                <span className="text-[10px] text-slate-500 block uppercase font-mono">Latency</span>
                <span className="font-mono text-sky-300 font-semibold">{telemetry.latencyMs} ms</span>
              </div>
              <div>
                <span className="text-[10px] text-slate-500 block uppercase font-mono">Duration</span>
                <span className="font-mono text-slate-300 font-semibold">{telemetry.durationSec.toFixed(1)} s</span>
              </div>
              <div>
                <span className="text-[10px] text-slate-500 block uppercase font-mono">RTF Ratio</span>
                <span className="font-mono text-emerald-400 font-semibold">{telemetry.rtf}x</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
