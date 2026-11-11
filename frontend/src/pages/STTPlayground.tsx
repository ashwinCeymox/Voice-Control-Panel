import React, { useState, useEffect, useRef } from 'react';
import {
  Upload,
  AudioLines,
  RefreshCw,
  CheckCircle2,
  Mic,
  Square,
  AlertCircle,
} from 'lucide-react';
import { TranscriptResponse } from '../types/api';

const API_BASE = 'http://127.0.0.1:8000';
const DEFAULT_KEY = 'dev-voice-key-12345';
const AUTH_HEADERS = { Authorization: `Bearer ${DEFAULT_KEY}` };

export const STTPlayground: React.FC = () => {
  const [loading, setLoading] = useState<boolean>(false);
  const [result, setResult] = useState<TranscriptResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Model & Language options
  const [installedModels, setInstalledModels] = useState<string[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>('');
  const [language, setLanguage] = useState<string>('auto');

  // Mic recording state
  const [isRecording, setIsRecording] = useState<boolean>(false);
  const [recordSeconds, setRecordSeconds] = useState<number>(0);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioChunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<any>(null);

  // Timing telemetry
  const [telemetry, setTelemetry] = useState<{
    latencyMs: number;
    durationSec: number;
    rtf: number;
  } | null>(null);

  useEffect(() => {
    fetchInstalledSTT();
  }, []);

  const fetchInstalledSTT = async () => {
    try {
      const res = await fetch(`${API_BASE}/v1/models/installed`, { headers: AUTH_HEADERS });
      if (res.ok) {
        const data = await res.json();
        const sttList: string[] = Array.isArray(data.stt)
          ? data.stt.map((m: any) => m.id)
          : [];
        setInstalledModels(sttList);
        if (sttList.length > 0) {
          setSelectedModel(sttList[0]);
        } else {
          setSelectedModel('');
        }
      }
    } catch {}
  };

  const handleTranscribeFile = async (blob: Blob, filename = 'recording.webm') => {
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
        headers: { Authorization: `Bearer ${DEFAULT_KEY}` },
        body: formData,
      });

      const latencyMs = Math.round(performance.now() - startTime);

      if (!res.ok) {
        const errData = await res.json();
        throw new Error(errData.error?.message || errData.detail || 'Transcription failed');
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

  const handleFileUpload = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) handleTranscribeFile(file, file.name);
  };

  const startRecording = async () => {
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
        handleTranscribeFile(audioBlob, 'mic-recording.webm');
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

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-100 flex items-center gap-2.5">
          <AudioLines className="w-7 h-7 text-sky-400" />
          Speech-to-Text Playground
        </h1>
        <p className="text-sm text-slate-400 mt-1">
          Transcribe speech with installed Whisper models and inspect latency telemetry.
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
            <span>No speech-to-text models installed. Install Whisper from the Model Registry to enable speech transcription.</span>
          </div>
        </div>
      )}

      {/* Model & Language Bar */}
      <div className="p-4 bg-slate-950 border border-slate-800 rounded-xl grid grid-cols-1 md:grid-cols-2 gap-4">
        <div>
          <label className="text-xs text-slate-300 font-medium block mb-1.5">
            Installed STT Model
          </label>
          <select
            value={selectedModel}
            onChange={(e) => setSelectedModel(e.target.value)}
            disabled={installedModels.length === 0}
            className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-sky-500 disabled:opacity-50"
          >
            {installedModels.length === 0 ? (
              <option value="" disabled>No STT model installed</option>
            ) : (
              installedModels.map((m) => (
                <option key={m} value={m}>
                  {m}
                </option>
              ))
            )}
          </select>
        </div>

        <div>
          <label className="text-xs text-slate-300 font-medium block mb-1.5">Language</label>
          <select
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="w-full bg-slate-900 border border-slate-800 rounded-lg px-3 py-2 text-xs text-slate-200 focus:outline-none focus:border-sky-500"
          >
            <option value="auto">Auto-detect Language</option>
            <option value="en">English (en)</option>
            <option value="es">Spanish (es)</option>
            <option value="fr">French (fr)</option>
            <option value="de">German (de)</option>
            <option value="ja">Japanese (ja)</option>
            <option value="zh">Chinese (zh)</option>
          </select>
        </div>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
        {/* Left: Input options (Mic recording + File upload) */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-5">
          <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
            Audio Input
          </h2>

          {/* Live Mic Recording */}
          <div className="p-5 bg-slate-900/60 border border-slate-800 rounded-xl flex flex-col items-center justify-center space-y-3">
            <button
              onClick={isRecording ? stopRecording : startRecording}
              className={`p-4 rounded-full transition shadow-lg ${
                isRecording
                  ? 'bg-rose-600 hover:bg-rose-500 text-white animate-pulse'
                  : 'bg-sky-600 hover:bg-sky-500 text-white'
              }`}
            >
              {isRecording ? <Square className="w-6 h-6 fill-current" /> : <Mic className="w-6 h-6" />}
            </button>
            <span className="text-xs font-medium text-slate-300">
              {isRecording ? `Recording... ${recordSeconds}s` : 'Record via Microphone'}
            </span>
          </div>

          <div className="relative flex items-center justify-center">
            <div className="border-t border-slate-800 w-full" />
            <span className="bg-slate-950 px-3 text-[11px] uppercase tracking-wider text-slate-500">
              or upload file
            </span>
          </div>

          {/* File Upload Box */}
          <div className="border-2 border-dashed border-slate-800 rounded-xl p-6 text-center hover:border-slate-700 transition">
            <Upload className="w-7 h-7 mx-auto text-slate-500 mb-2" />
            <p className="text-xs text-slate-400 mb-3">WAV, WebM, MP3, FLAC, OGG</p>
            <label className="px-4 py-2 bg-slate-900 hover:bg-slate-800 border border-slate-800 text-slate-200 rounded-lg text-xs font-medium cursor-pointer inline-flex items-center gap-2">
              <input
                type="file"
                accept="audio/*"
                onChange={handleFileUpload}
                disabled={loading || isRecording}
                className="hidden"
              />
              Select Audio File
            </label>
          </div>
        </div>

        {/* Right: Transcript Output & Telemetry */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 flex flex-col justify-between space-y-4">
          <div className="space-y-4">
            <h2 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
              Transcription Output
            </h2>

            {loading ? (
              <div className="p-12 text-center text-slate-500 text-xs flex items-center justify-center gap-2">
                <RefreshCw className="w-4 h-4 animate-spin text-sky-400" /> Transcribing audio
                stream...
              </div>
            ) : result ? (
              <div className="space-y-4">
                <div className="flex items-center gap-2 text-xs text-emerald-400 font-medium">
                  <CheckCircle2 className="w-4 h-4" /> Transcription Complete
                </div>
                <div className="bg-slate-900/80 border border-slate-800 rounded-lg p-4 text-slate-100 text-sm leading-relaxed">
                  {result.text}
                </div>
                <div className="text-xs text-slate-400 flex flex-wrap gap-4">
                  <span>
                    Detected Language: <strong className="text-slate-200">{result.language || 'en'}</strong>
                  </span>
                </div>
              </div>
            ) : (
              <p className="text-xs text-slate-500 py-8 text-center italic">
                Record audio or upload a sample to test speech recognition.
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
