import React, { useState, useEffect } from 'react';
import { useHealth } from '../../hooks/useHealth';
import { Activity, Server, Radio, Volume2 } from 'lucide-react';

export const TopBar: React.FC = () => {
  const { health, isBackendOnline, loading } = useHealth();
  const [localAIError, setLocalAIError] = useState<boolean>(false);
  const [ttsError, setTtsError] = useState<boolean>(false);

  useEffect(() => {
    const handleVoiceError = (event: CustomEvent<{ code: string; message: string }>) => {
      const code = event.detail?.code || '';
      if (code.includes('STT') || code.includes('LLM') || code.includes('LOCALAI')) {
        setLocalAIError(true);
        setTimeout(() => setLocalAIError(false), 8000);
      }
      if (code.includes('TTS') || code.includes('LOCAL_TTS')) {
        setTtsError(true);
        setTimeout(() => setTtsError(false), 8000);
      }
    };

    window.addEventListener('voice-error' as any, handleVoiceError);
    return () => window.removeEventListener('voice-error' as any, handleVoiceError);
  }, []);

  const isLocalAIOk = Boolean(health?.providers.localai?.reachable && !localAIError);
  const isTtsOk = Boolean(health?.providers.localtts?.reachable && !ttsError);

  return (
    <header className="h-14 border-b border-slate-800 bg-slate-950/80 backdrop-blur px-6 flex items-center justify-between z-10 sticky top-0">
      <div className="flex items-center gap-3">
        <Radio className="w-5 h-5 text-indigo-400 animate-pulse" />
        <span className="font-semibold text-slate-100 tracking-wide">Voice AI Control Panel</span>
        <span className="text-xs bg-indigo-500/10 text-indigo-400 border border-indigo-500/20 px-2 py-0.5 rounded-full font-mono">
          Self-Hosted
        </span>
      </div>

      <div className="flex items-center gap-4 text-xs">
        {/* Backend status */}
        <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 px-2.5 py-1.5 rounded-md">
          <Server className="w-3.5 h-3.5 text-slate-400" />
          <span className="text-slate-300">FastAPI Gateway</span>
          <span
            className={`w-2 h-2 rounded-full ${
              loading
                ? 'bg-amber-400 animate-ping'
                : isBackendOnline
                ? 'bg-emerald-400'
                : 'bg-rose-500'
            }`}
            title={isBackendOnline ? 'Online (Port 8000)' : 'Offline'}
          />
        </div>

        {/* LocalAI status - real health derived */}
        <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 px-2.5 py-1.5 rounded-md">
          <Activity className="w-3.5 h-3.5 text-slate-400" />
          <span className="text-slate-300">LocalAI (STT/LLM)</span>
          <span
            className={`w-2 h-2 rounded-full ${
              isLocalAIOk
                ? 'bg-emerald-400'
                : 'bg-rose-500 animate-pulse'
            }`}
            title={isLocalAIOk ? 'Healthy (Port 8080)' : 'Unreachable or Error (Port 8080)'}
          />
        </div>

        {/* local-tts status - real health derived */}
        <div className="flex items-center gap-2 bg-slate-900 border border-slate-800 px-2.5 py-1.5 rounded-md">
          <Volume2 className="w-3.5 h-3.5 text-slate-400" />
          <span className="text-slate-300">local-tts</span>
          <span
            className={`w-2 h-2 rounded-full ${
              isTtsOk
                ? 'bg-emerald-400'
                : 'bg-rose-500 animate-pulse'
            }`}
            title={isTtsOk ? 'Healthy (Port 8880)' : 'Unreachable or Error (Port 8880)'}
          />
        </div>
      </div>
    </header>
  );
};
