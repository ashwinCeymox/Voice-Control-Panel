import React, { useState, useEffect, useRef } from 'react';
import { useHealth } from '../../hooks/useHealth';
import { useAuth } from '../../auth/AuthContext';
import { ChangePassword } from '../../pages/ChangePassword';
import { Activity, Server, Radio, Volume2, User, KeyRound, LogOut, ChevronDown, X } from 'lucide-react';

export const TopBar: React.FC = () => {
  const { health, isBackendOnline, loading } = useHealth();
  const { user, logout } = useAuth();
  const [localAIError, setLocalAIError] = useState<boolean>(false);
  const [ttsError, setTtsError] = useState<boolean>(false);
  const [menuOpen, setMenuOpen] = useState<boolean>(false);
  const [showPasswordModal, setShowPasswordModal] = useState<boolean>(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    };
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

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

        {/* User Account Menu */}
        {user && (
          <div className="relative ml-2" ref={menuRef}>
            <button
              onClick={() => setMenuOpen(!menuOpen)}
              className="flex items-center gap-2 bg-slate-900 hover:bg-slate-800 border border-slate-800 px-3 py-1.5 rounded-md text-slate-200 transition-colors"
            >
              <div className="w-5 h-5 rounded-full bg-indigo-500/20 text-indigo-400 flex items-center justify-center">
                <User className="w-3 h-3" />
              </div>
              <span className="font-medium max-w-[140px] truncate">{user.email}</span>
              <ChevronDown className="w-3 h-3 text-slate-400" />
            </button>

            {menuOpen && (
              <div className="absolute right-0 mt-2 w-48 bg-slate-900 border border-slate-800 rounded-lg shadow-xl py-1 z-50">
                <div className="px-3 py-2 border-b border-slate-800 text-[11px] text-slate-400">
                  Signed in as <span className="text-slate-200 block truncate">{user.email}</span>
                </div>
                <button
                  onClick={() => {
                    setMenuOpen(false);
                    setShowPasswordModal(true);
                  }}
                  className="w-full text-left px-3 py-2 text-xs text-slate-300 hover:bg-slate-800 flex items-center gap-2 transition-colors"
                >
                  <KeyRound className="w-3.5 h-3.5 text-amber-400" />
                  <span>Change password</span>
                </button>
                <button
                  onClick={() => {
                    setMenuOpen(false);
                    logout();
                  }}
                  className="w-full text-left px-3 py-2 text-xs text-rose-400 hover:bg-rose-500/10 flex items-center gap-2 transition-colors"
                >
                  <LogOut className="w-3.5 h-3.5" />
                  <span>Log out</span>
                </button>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Change Password Modal */}
      {showPasswordModal && (
        <div className="fixed inset-0 bg-slate-950/80 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="relative w-full max-w-md">
            <button
              onClick={() => setShowPasswordModal(false)}
              className="absolute -top-3 -right-3 w-8 h-8 rounded-full bg-slate-800 border border-slate-700 text-slate-400 hover:text-slate-200 flex items-center justify-center z-10"
              aria-label="Close modal"
            >
              <X className="w-4 h-4" />
            </button>
            <ChangePassword isModal={true} onSuccess={() => setShowPasswordModal(false)} />
          </div>
        </div>
      )}
    </header>
  );
};
