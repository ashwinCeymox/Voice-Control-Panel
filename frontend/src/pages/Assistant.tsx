import React, { useState, useEffect, useRef } from 'react';
import {
  Mic,
  Square,
  AlertCircle,
  Headphones,
  Laptop,
  Bot,
  Sparkles,
} from 'lucide-react';
import { AudioOrb, OrbState } from '../components/AudioOrb';
import { StreamingPCMPlayer } from '../audio/pcmPlayer';
import { BrowserVAD } from '../audio/vad';
import { ServerEvent, Agent } from '../types/api';

const API_BASE = 'http://127.0.0.1:8000';
const WS_BASE = 'ws://127.0.0.1:8000';
const DEFAULT_KEY = 'dev-voice-key-12345';

type AssistantState = 'idle' | 'listening' | 'transcribing' | 'thinking' | 'speaking';

interface TurnRecord {
  id: string;
  transcript: string;
  response: string;
  timing?: string;
}

export const AssistantView: React.FC = () => {
  const [state, setState] = useState<AssistantState>('idle');
  const [transcript, setTranscript] = useState<string>('');
  const [responseDelta, setResponseDelta] = useState<string>('');
  const [turns, setTurns] = useState<TurnRecord[]>([]);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Active agent info
  const [activeAgent, setActiveAgent] = useState<Agent | null>(null);
  const [audioMode, setAudioMode] = useState<'laptop_speakers' | 'headphones'>('laptop_speakers');

  const wsRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const audioCtxRef = useRef<AudioContext | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);
  const pcmPlayerRef = useRef<StreamingPCMPlayer>(new StreamingPCMPlayer(24000));
  const vadRef = useRef<BrowserVAD | null>(null);
  const activeTurnIdRef = useRef<string | null>(null);

  useEffect(() => {
    fetchActiveAgent();

    // Spacebar listener for interrupt or toggle
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        e.code === 'Space' &&
        (e.target as HTMLElement).tagName !== 'INPUT' &&
        (e.target as HTMLElement).tagName !== 'TEXTAREA'
      ) {
        e.preventDefault();
        if (state === 'speaking') {
          handleInterrupt();
        } else if (state === 'idle') {
          startListening();
        } else if (state === 'listening') {
          stopListening();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
      cleanupAudio();
    };
  }, [state]);

  const fetchActiveAgent = async () => {
    try {
      const res = await fetch(`${API_BASE}/v1/agents/active`, {
        headers: { Authorization: `Bearer ${DEFAULT_KEY}` },
      });
      if (res.ok) {
        const data = await res.json();
        if (data.active_agent) {
          setActiveAgent(data.active_agent);
          if (data.active_agent.audio?.echo_mode) {
            setAudioMode(data.active_agent.audio.echo_mode as any);
          }
        }
      }
    } catch {}
  };

  const cleanupAudio = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop();
      } catch {}
      mediaRecorderRef.current = null;
    }
    if (vadRef.current) {
      vadRef.current.stop();
      vadRef.current = null;
    }
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach((t) => t.stop());
      audioStreamRef.current = null;
    }
    if (audioCtxRef.current && audioCtxRef.current.state !== 'closed') {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    micAnalyserRef.current = null;
    pcmPlayerRef.current.stop();
  };

  // Initialize and clean up WebSocket connection
  const connectWebSocket = async (): Promise<WebSocket> => {
    // 1. Fetch short-lived ticket
    const ticketRes = await fetch(`${API_BASE}/v1/auth/ticket`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${DEFAULT_KEY}` },
    });
    if (!ticketRes.ok) throw new Error('Failed to acquire WebSocket ticket');
    const { ticket } = await ticketRes.json();

    const ws = new WebSocket(`${WS_BASE}/v1/voice/stream?ticket=${ticket}`);
    ws.binaryType = 'arraybuffer';

    ws.onmessage = (event) => {
      if (typeof event.data === 'string') {
        const payload: ServerEvent = JSON.parse(event.data);
        handleServerEvent(payload);
      } else if (event.data instanceof ArrayBuffer) {
        // Raw PCM chunk from server
        pcmPlayerRef.current.feed(event.data);
        if (vadRef.current) vadRef.current.setPlaybackState(true);
        setState('speaking');
      }
    };

    ws.onerror = (e) => {
      console.error('WS Error:', e);
      setErrorMsg('WebSocket connection error');
      setState('idle');
      if (vadRef.current) vadRef.current.setPlaybackState(false);
    };

    ws.onclose = () => {
      setState('idle');
      if (vadRef.current) vadRef.current.setPlaybackState(false);
    };

    wsRef.current = ws;
    return ws;
  };

  const handleServerEvent = (event: ServerEvent) => {
    switch (event.type) {
      case 'session.created':
        if (event.agent_name && activeAgent) {
          setActiveAgent((prev) => (prev ? { ...prev, name: event.agent_name! } : null));
        }
        break;
      case 'transcript.completed':
        setTranscript(event.text);
        setState('thinking');
        break;
      case 'response.text.delta':
        setResponseDelta((prev) => prev + event.delta);
        break;
      case 'audio.started':
        setState('speaking');
        if (vadRef.current) vadRef.current.setPlaybackState(true);
        break;
      case 'audio.completed':
        setState('idle');
        if (vadRef.current) vadRef.current.setPlaybackState(false);
        break;
      case 'turn.completed':
        setTurns((prev) => [
          ...prev,
          {
            id: event.turn_id,
            transcript: transcript,
            response: responseDelta,
          },
        ]);
        setState('idle');
        if (vadRef.current) vadRef.current.setPlaybackState(false);
        activeTurnIdRef.current = null;
        break;
      case 'turn.cancelled':
        pcmPlayerRef.current.stop();
        setState('idle');
        if (vadRef.current) vadRef.current.setPlaybackState(false);
        activeTurnIdRef.current = null;
        break;
      case 'error':
        setErrorMsg(`[${event.code}] ${event.message}`);
        window.dispatchEvent(
          new CustomEvent('voice-error', {
            detail: { code: event.code, message: event.message },
          })
        );
        setState('idle');
        if (vadRef.current) vadRef.current.setPlaybackState(false);
        break;
    }
  };

  // Start Voice Capture with VAD and Barge-In
  const startListening = async () => {
    try {
      setErrorMsg(null);
      setTranscript('');
      setResponseDelta('');

      // Cleanup any dangling audio/mic resources from previous turns
      cleanupAudio();

      let ws = wsRef.current;
      if (!ws || ws.readyState !== WebSocket.OPEN) {
        ws = await connectWebSocket();
      }

      let stream: MediaStream;
      try {
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
      } catch (err: unknown) {
        if (err instanceof DOMException && (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError')) {
          setErrorMsg('Microphone access denied. Please grant microphone permission in your browser.');
        } else if (err instanceof DOMException && (err.name === 'NotFoundError' || err.name === 'DevicesNotFoundError')) {
          setErrorMsg('No microphone found. Please connect a microphone and try again.');
        } else {
          setErrorMsg('Unable to access microphone. Please check your system audio permissions.');
        }
        setState('idle');
        return;
      }

      audioStreamRef.current = stream;

      const AudioCtxClass =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const audioCtx = new AudioCtxClass();
      audioCtxRef.current = audioCtx;
      const micSource = audioCtx.createMediaStreamSource(stream);

      const analyser = audioCtx.createAnalyser();
      analyser.fftSize = 64;
      analyser.smoothingTimeConstant = 0.8;
      micSource.connect(analyser);
      micAnalyserRef.current = analyser;

      const vad = new BrowserVAD(
        audioCtx,
        micSource,
        {
          onSpeechStart: () => {
            if (state === 'idle') setState('listening');
          },
          onSpeechEnd: () => {
            stopListening();
          },
          onBargeIn: () => {
            handleInterrupt();
          },
        },
        {
          silenceMs: activeAgent?.vad?.min_silence_ms || 650,
          idleThreshold: activeAgent?.vad?.threshold || 0.045,
          bargeInThreshold: audioMode === 'headphones' ? 0.06 : 0.14,
        }
      );
      vad.start();
      vadRef.current = vad;

      ws.send(
        JSON.stringify({
          type: 'session.start',
          agent_id: activeAgent?.id,
          input_audio: { container: 'webm', codec: 'opus' },
          config: { echo_mode: audioMode, barge_in: true },
        })
      );

      const mediaRecorder = new MediaRecorder(stream, { mimeType: 'audio/webm' });
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0 && ws.readyState === WebSocket.OPEN) {
          e.data.arrayBuffer().then((buf) => {
            ws.send(buf);
          });
        }
      };

      mediaRecorder.start(100);
      setState('listening');
    } catch (err: unknown) {
      console.error('Audio initialization error:', err);
      cleanupAudio();
      setErrorMsg('Audio initialization failed. Please reload and try again.');
      setState('idle');
    }
  };

  const stopListening = () => {
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.stop();
      } catch {}
      mediaRecorderRef.current = null;
    }
    if (vadRef.current) {
      vadRef.current.stop();
      vadRef.current = null;
    }
    if (audioStreamRef.current) {
      audioStreamRef.current.getTracks().forEach((t) => t.stop());
      audioStreamRef.current = null;
    }
    if (audioCtxRef.current && audioCtxRef.current.state !== 'closed') {
      audioCtxRef.current.close().catch(() => {});
      audioCtxRef.current = null;
    }
    micAnalyserRef.current = null;

    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: 'audio.commit' }));
      setState('transcribing');
    }
  };

  const handleInterrupt = () => {
    pcmPlayerRef.current.stop();
    if (vadRef.current) vadRef.current.setPlaybackState(false);
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: 'turn.cancel',
          turn_id: activeTurnIdRef.current,
        })
      );
    }
    setState('idle');
  };

  const getOrbState = (): OrbState => {
    switch (state) {
      case 'listening':
        return 'listening';
      case 'transcribing':
      case 'thinking':
        return 'thinking';
      case 'speaking':
        return 'speaking';
      default:
        return 'idle';
    }
  };

  const getOrbAnalyser = (): AnalyserNode | null => {
    if (state === 'speaking') {
      return pcmPlayerRef.current.getAnalyserNode();
    }
    if (state === 'listening') {
      return micAnalyserRef.current;
    }
    return null;
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Top Agent Bar */}
      <div className="flex flex-wrap items-center justify-between gap-4 p-4 bg-slate-950 border border-slate-800 rounded-xl">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-lg bg-indigo-500/10 border border-indigo-500/20 text-indigo-400">
            <Bot className="w-5 h-5" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="font-semibold text-slate-100 text-sm">
                {activeAgent?.name || 'Default Voice Assistant'}
              </h2>
              <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-mono">
                Active Agent
              </span>
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              {activeAgent?.description || 'Concise voice assistant optimized for 8GB Mac'}
            </p>
          </div>
        </div>

        {/* Audio Mode Selector */}
        <div className="flex items-center gap-2">
          <div className="flex bg-slate-900 border border-slate-800 rounded-lg p-1 text-xs">
            <button
              onClick={() => setAudioMode('laptop_speakers')}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md transition ${
                audioMode === 'laptop_speakers'
                  ? 'bg-indigo-600 text-white font-medium shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Laptop className="w-3.5 h-3.5" />
              Speakers (AEC)
            </button>
            <button
              onClick={() => setAudioMode('headphones')}
              className={`flex items-center gap-1.5 px-2.5 py-1 rounded-md transition ${
                audioMode === 'headphones'
                  ? 'bg-indigo-600 text-white font-medium shadow-sm'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Headphones className="w-3.5 h-3.5" />
              Headphones
            </button>
          </div>
        </div>
      </div>

      {errorMsg && (
        <div className="p-4 bg-rose-500/10 border border-rose-500/30 rounded-xl flex items-center justify-between text-rose-300 text-sm">
          <div className="flex items-center gap-2.5">
            <AlertCircle className="w-4 h-4 text-rose-400 shrink-0" />
            <span>{errorMsg}</span>
          </div>
          <button
            onClick={() => setErrorMsg(null)}
            className="text-xs text-rose-400 hover:text-rose-200 underline"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Main Orb Stage */}
      <div className="flex flex-col items-center justify-center py-10 bg-slate-950 border border-slate-800 rounded-2xl relative overflow-hidden shadow-xl">
        <div className="absolute inset-0 bg-radial-gradient from-indigo-500/5 via-transparent to-transparent pointer-events-none" />

        <AudioOrb
          state={getOrbState()}
          analyser={getOrbAnalyser()}
          size={300}
          onClick={() => {
            if (state === 'speaking') handleInterrupt();
            else if (state === 'idle') startListening();
            else if (state === 'listening') stopListening();
          }}
        />

        {/* State Pill */}
        <div className="mt-6 flex flex-col items-center gap-2">
          <div className="px-4 py-1.5 rounded-full bg-slate-900 border border-slate-800 text-xs font-mono uppercase tracking-wider flex items-center gap-2">
            <span
              className={`w-2 h-2 rounded-full ${
                state === 'listening'
                  ? 'bg-sky-400 animate-ping'
                  : state === 'thinking'
                  ? 'bg-purple-400 animate-pulse'
                  : state === 'speaking'
                  ? 'bg-emerald-400 animate-pulse'
                  : 'bg-indigo-400'
              }`}
            />
            <span className="text-slate-300 font-semibold">{state}</span>
          </div>
          <span className="text-[11px] text-slate-500">
            {state === 'speaking'
              ? 'Click orb or press Spacebar to interrupt'
              : state === 'listening'
              ? 'Speak naturally (VAD automatically commits on pause)'
              : 'Click orb or press Spacebar to begin'}
          </span>
        </div>

        {/* Action Controls */}
        <div className="mt-6 flex items-center gap-3">
          {state === 'idle' ? (
            <button
              onClick={startListening}
              className="flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-full text-sm font-semibold transition shadow-lg hover:shadow-indigo-500/25"
            >
              <Mic className="w-4 h-4" /> Start Speaking
            </button>
          ) : state === 'listening' ? (
            <button
              onClick={stopListening}
              className="flex items-center gap-2 px-6 py-2.5 bg-sky-600 hover:bg-sky-500 text-white rounded-full text-sm font-semibold transition shadow-lg"
            >
              <Square className="w-4 h-4 fill-current" /> Done Speaking
            </button>
          ) : (
            <button
              onClick={handleInterrupt}
              className="flex items-center gap-2 px-6 py-2.5 bg-rose-600 hover:bg-rose-500 text-white rounded-full text-sm font-semibold transition shadow-lg"
            >
              <Square className="w-4 h-4 fill-current" /> Interrupt Assistant
            </button>
          )}
        </div>
      </div>

      {/* Live Transcript & Response Stream */}
      {(transcript || responseDelta) && (
        <div className="p-5 bg-slate-950 border border-slate-800 rounded-xl space-y-4">
          {transcript && (
            <div>
              <div className="text-[11px] uppercase tracking-wider text-slate-500 font-semibold mb-1">
                You said:
              </div>
              <p className="text-sm text-slate-200 bg-slate-900/60 p-3 rounded-lg border border-slate-800/80">
                {transcript}
              </p>
            </div>
          )}
          {responseDelta && (
            <div>
              <div className="text-[11px] uppercase tracking-wider text-indigo-400 font-semibold mb-1 flex items-center gap-1.5">
                <Sparkles className="w-3.5 h-3.5" />
                {activeAgent?.name || 'Assistant'} replied:
              </div>
              <p className="text-sm text-slate-200 bg-indigo-950/20 p-3 rounded-lg border border-indigo-900/40 leading-relaxed">
                {responseDelta}
              </p>
            </div>
          )}
        </div>
      )}

      {/* Conversation Turn History */}
      {turns.length > 0 && (
        <div className="p-5 bg-slate-950 border border-slate-800 rounded-xl space-y-3">
          <h3 className="text-xs font-semibold text-slate-400 uppercase tracking-wider">
            Turn History ({turns.length})
          </h3>
          <div className="space-y-3 max-h-72 overflow-y-auto pr-2 scrollbar-thin">
            {turns.slice().reverse().map((t, i) => (
              <div
                key={t.id || i}
                className="p-3 bg-slate-900/50 border border-slate-800/60 rounded-lg text-xs space-y-1.5"
              >
                <div className="text-slate-400">
                  <span className="font-semibold text-slate-300">User:</span> {t.transcript}
                </div>
                <div className="text-slate-300">
                  <span className="font-semibold text-indigo-400">Assistant:</span> {t.response}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
