import React from 'react';
import { useHealth } from '../hooks/useHealth';
import { Server, Activity, Volume2 } from 'lucide-react';

export const SystemHealth: React.FC = () => {
  const { health, isBackendOnline } = useHealth();

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-100">System & Provider Health</h1>
        <p className="text-sm text-slate-400">Live readiness and connectivity across FastAPI, LocalAI, and local-tts</p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
        {/* Gateway */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4">
          <div className="flex items-center justify-between">
            <Server className="w-5 h-5 text-indigo-400" />
            <span
              className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                isBackendOnline ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-rose-500/10 text-rose-400 border border-rose-500/20'
              }`}
            >
              {isBackendOnline ? 'ONLINE' : 'OFFLINE'}
            </span>
          </div>
          <div>
            <h3 className="font-semibold text-slate-200">FastAPI Gateway</h3>
            <p className="text-xs text-slate-500 font-mono">
              {typeof window !== 'undefined' && window.location.origin
                ? window.location.origin
                : 'http://127.0.0.1:8000'}
            </p>
          </div>
          <p className="text-xs text-slate-400">
            Handles API-key authentication, WebSocket connection tickets, session management, and voice turn streaming.
          </p>
        </div>

        {/* LocalAI */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4">
          <div className="flex items-center justify-between">
            <Activity className="w-5 h-5 text-indigo-400" />
            <span
              className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                health?.providers.localai.reachable ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
              }`}
            >
              {health?.providers.localai.reachable ? 'REACHABLE' : 'STANDBY'}
            </span>
          </div>
          <div>
            <h3 className="font-semibold text-slate-200">LocalAI Service</h3>
            <p className="text-xs text-slate-500 font-mono">{health?.providers.localai.url || 'http://127.0.0.1:8080'}</p>
          </div>
          <p className="text-xs text-slate-400">
            Dedicated runtime for Whisper STT and LLM chat completions with SSE streaming.
          </p>
        </div>

        {/* local-tts */}
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-4">
          <div className="flex items-center justify-between">
            <Volume2 className="w-5 h-5 text-indigo-400" />
            <span
              className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                health?.providers.localtts.reachable ? 'bg-emerald-500/10 text-emerald-400 border border-emerald-500/20' : 'bg-amber-500/10 text-amber-400 border border-amber-500/20'
              }`}
            >
              {health?.providers.localtts.reachable ? 'REACHABLE' : 'STANDBY'}
            </span>
          </div>
          <div>
            <h3 className="font-semibold text-slate-200">local-tts Service</h3>
            <p className="text-xs text-slate-500 font-mono">{health?.providers.localtts.url || 'http://127.0.0.1:8880'}</p>
          </div>
          <p className="text-xs text-slate-400">
            Dedicated runtime for speech synthesis (Kokoro/Pocket) emitting raw 24kHz signed 16-bit PCM.
          </p>
        </div>
      </div>
    </div>
  );
};
