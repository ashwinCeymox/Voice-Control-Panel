import React, { useState, useEffect, useRef } from 'react';
import {
  Mic,
  MicOff,
  PhoneOff,
  AlertCircle,
  Headphones,
  Laptop,
  Bot,
  Sparkles,
  Volume2,
} from 'lucide-react';
import { AudioOrb, OrbState } from '../components/AudioOrb';
import { StreamingPCMPlayer } from '../audio/pcmPlayer';
import { BrowserVAD } from '../audio/vad';
import { ServerEvent, Agent } from '../types/api';

const host = typeof window !== 'undefined' && window.location.hostname ? window.location.hostname : '127.0.0.1';
const API_BASE = `http://${host}:8000`;
const WS_BASE = `ws://${host}:8000`;
const DEFAULT_KEY = 'dev-voice-key-12345';
const AUTH_HEADERS = { Authorization: `Bearer ${DEFAULT_KEY}` };

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
  const [isMuted, setIsMuted] = useState<boolean>(false);
  const [isSpeakingVAD, setIsSpeakingVAD] = useState<boolean>(false);

  // Active and available agents
  const [activeAgent, setActiveAgent] = useState<Agent | null>(null);
  const [availableAgents, setAvailableAgents] = useState<Agent[]>([]);
  const [isSwitchingAgent, setIsSwitchingAgent] = useState<boolean>(false);
  const [audioMode, setAudioMode] = useState<'laptop_speakers' | 'headphones'>('laptop_speakers');

  const wsRef = useRef<WebSocket | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const audioStreamRef = useRef<MediaStream | null>(null);
  const currentTranscriptRef = useRef<string>('');
  const currentResponseRef = useRef<string>('');
  const audioCtxRef = useRef<AudioContext | null>(null);
  const micAnalyserRef = useRef<AnalyserNode | null>(null);
  const pcmPlayerRef = useRef<StreamingPCMPlayer>(new StreamingPCMPlayer(24000));
  const vadRef = useRef<BrowserVAD | null>(null);
  const activeTurnIdRef = useRef<string | null>(null);
  const mimeTypeRef = useRef<string>('');
  const containerRef = useRef<'webm' | 'mp4' | 'ogg'>('webm');

  const stateRef = useRef<AssistantState>(state);
  useEffect(() => {
    stateRef.current = state;
  }, [state]);

  const activeAgentRef = useRef<Agent | null>(activeAgent);
  useEffect(() => {
    activeAgentRef.current = activeAgent;
  }, [activeAgent]);

  // Initial load: fetch active and all agents
  useEffect(() => {
    fetchActiveAgent();
    fetchAllAgents();
  }, []);

  // Spacebar listener for interruption or session start
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (
        e.code === 'Space' &&
        (e.target as HTMLElement).tagName !== 'INPUT' &&
        (e.target as HTMLElement).tagName !== 'TEXTAREA'
      ) {
        e.preventDefault();
        if (stateRef.current === 'speaking') {
          handleInterrupt();
        } else if (stateRef.current === 'idle') {
          startSession();
        }
      }
    };

    window.addEventListener('keydown', handleKeyDown);
    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, []);

  // Cleanup on component unmount ONLY
  useEffect(() => {
    return () => {
      cleanupAudio();
      if (wsRef.current) {
        try {
          wsRef.current.send(JSON.stringify({ type: 'session.end' }));
          wsRef.current.close();
        } catch {}
        wsRef.current = null;
      }
    };
  }, []);

  const fetchActiveAgent = async () => {
    try {
      const res = await fetch(`${API_BASE}/v1/agents/active`, {
        headers: AUTH_HEADERS,
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

  const fetchAllAgents = async () => {
    try {
      const res = await fetch(`${API_BASE}/v1/agents`, {
        headers: AUTH_HEADERS,
      });
      if (res.ok) {
        const data = await res.json();
        const list: Agent[] = data.agents || [];
        setAvailableAgents(list);
        setActiveAgent((prev) => prev || list.find((a) => a.is_active) || list[0] || null);
      }
    } catch {}
  };

  const handleSwitchAgent = async (agentId: string) => {
    if (!agentId || agentId === activeAgent?.id) return;
    try {
      setIsSwitchingAgent(true);
      setErrorMsg(null);
      const res = await fetch(`${API_BASE}/v1/agents/${agentId}/load`, {
        method: 'POST',
        headers: AUTH_HEADERS,
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to switch agent');
      }
      const switched = availableAgents.find((a) => a.id === agentId);
      if (switched) setActiveAgent(switched);

      // If session is active, notify backend of switch
      if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({
            type: 'session.start',
            agent_id: agentId,
            input_audio: { container: 'webm', codec: 'opus' },
            config: { echo_mode: audioMode, barge_in: true },
          })
        );
      }
    } catch (err: any) {
      setErrorMsg(err.message);
    } finally {
      setIsSwitchingAgent(false);
    }
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

  // Connect WebSocket
  const connectWebSocket = async (): Promise<WebSocket> => {
    const ticketRes = await fetch(`${API_BASE}/v1/auth/ticket`, {
      method: 'POST',
      headers: AUTH_HEADERS,
    });
    if (!ticketRes.ok) throw new Error('Failed to acquire WebSocket ticket');
    const { ticket } = await ticketRes.json();

    return new Promise((resolve, reject) => {
      let settled = false;
      const ws = new WebSocket(`${WS_BASE}/v1/voice/stream?ticket=${ticket}`);
      ws.binaryType = 'arraybuffer';

      const timeout = setTimeout(() => {
        if (!settled) {
          settled = true;
          try { ws.close(); } catch {}
          reject(new Error('WebSocket connection timed out'));
        }
      }, 8000);

      ws.onopen = () => {
        if (!settled) {
          settled = true;
          clearTimeout(timeout);
          wsRef.current = ws;
          resolve(ws);
        }
      };

      ws.onmessage = (event) => {
        if (typeof event.data === 'string') {
          try {
            const payload: ServerEvent = JSON.parse(event.data);
            handleServerEvent(payload);
          } catch (err) {
            console.error('Failed to parse WS text message:', err);
          }
        } else if (event.data instanceof ArrayBuffer) {
          // Raw PCM chunk from server
          pcmPlayerRef.current.feed(event.data);
          if (vadRef.current) vadRef.current.setPlaybackState(true);
          setState('speaking');
        }
      };

      ws.onerror = (e) => {
        console.error('WS Error:', e);
        if (!settled) {
          settled = true;
          clearTimeout(timeout);
          reject(new Error('WebSocket connection error'));
        }
        setErrorMsg('WebSocket connection error. Please verify backend is running on port 8000.');
        endSession();
      };

      ws.onclose = () => {
        setState('idle');
        if (vadRef.current) vadRef.current.setPlaybackState(false);
      };
    });
  };

  const handleServerEvent = (event: ServerEvent) => {
    if ('turn_id' in event && (event as any).turn_id) {
      activeTurnIdRef.current = (event as any).turn_id;
    }

    switch (event.type) {
      case 'session.created':
        if (event.agent_name) {
          setActiveAgent((prev) => (prev ? { ...prev, name: event.agent_name! } : null));
        }
        break;
      case 'transcript.completed':
        currentTranscriptRef.current = event.text;
        setTranscript(event.text);
        setState('thinking');
        break;
      case 'response.text.delta':
        currentResponseRef.current += event.delta;
        setResponseDelta((prev) => prev + event.delta);
        break;
      case 'audio.started':
        setState('speaking');
        if (vadRef.current) vadRef.current.setPlaybackState(true);
        break;
      case 'audio.completed':
        setState('listening');
        if (vadRef.current) vadRef.current.setPlaybackState(false);
        startRecording();
        break;
      case 'turn.completed':
        {
          const savedTranscript = currentTranscriptRef.current || transcript;
          const savedResponse = currentResponseRef.current || responseDelta;
          if (savedTranscript || savedResponse) {
            setTurns((prev) => [
              ...prev,
              {
                id: event.turn_id,
                transcript: savedTranscript,
                response: savedResponse,
              },
            ]);
          }
        }
        currentTranscriptRef.current = '';
        currentResponseRef.current = '';
        setState('listening');
        if (vadRef.current) vadRef.current.setPlaybackState(false);
        activeTurnIdRef.current = null;
        startRecording();
        break;
      case 'turn.cancelled':
        pcmPlayerRef.current.stop();
        currentTranscriptRef.current = '';
        currentResponseRef.current = '';
        setState('listening');
        if (vadRef.current) vadRef.current.setPlaybackState(false);
        activeTurnIdRef.current = null;
        startRecording();
        break;
      case 'error':
        setErrorMsg(`[${event.code}] ${event.message}`);
        currentTranscriptRef.current = '';
        currentResponseRef.current = '';
        setState('listening');
        if (vadRef.current) vadRef.current.setPlaybackState(false);
        startRecording();
        break;
    }
  };

  // Start fresh recorder instance with clean container header per turn
  const startRecording = () => {
    if (!audioStreamRef.current) return;
    if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
      return;
    }
    try {
      const recorderOptions = mimeTypeRef.current ? { mimeType: mimeTypeRef.current } : undefined;
      const mediaRecorder = recorderOptions
        ? new MediaRecorder(audioStreamRef.current, recorderOptions)
        : new MediaRecorder(audioStreamRef.current);
      mediaRecorderRef.current = mediaRecorder;

      mediaRecorder.ondataavailable = (e) => {
        if (e.data.size > 0 && wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
          e.data.arrayBuffer().then((buf) => {
            if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
              wsRef.current.send(buf);
            }
          });
        }
      };

      mediaRecorder.start(100);
    } catch (err) {
      console.error('Failed to start MediaRecorder:', err);
    }
  };

  // Autonomous VAD turn commit
  const commitTurn = () => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      currentTranscriptRef.current = '';
      currentResponseRef.current = '';
      setTranscript('');
      setResponseDelta('');
      if (mediaRecorderRef.current && mediaRecorderRef.current.state === 'recording') {
        mediaRecorderRef.current.onstop = () => {
          if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
            wsRef.current.send(JSON.stringify({ type: 'audio.commit' }));
          }
        };
        try {
          mediaRecorderRef.current.stop();
        } catch {
          wsRef.current.send(JSON.stringify({ type: 'audio.commit' }));
        }
      } else {
        wsRef.current.send(JSON.stringify({ type: 'audio.commit' }));
      }
      setState('thinking');
    }
  };

  // Start continuous conversational session
  const startSession = async () => {
    try {
      setErrorMsg(null);
      setTranscript('');
      setResponseDelta('');
      setIsMuted(false);

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
          setErrorMsg('Unable to access microphone. Please check system audio permissions.');
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

      const currentAgent = activeAgentRef.current;

      const vad = new BrowserVAD(
        audioCtx,
        micSource,
        {
          onSpeechStart: () => {
            setIsSpeakingVAD(true);
            if (stateRef.current !== 'speaking') {
              setState('listening');
            }
          },
          onSpeechEnd: () => {
            setIsSpeakingVAD(false);
            if (stateRef.current === 'listening') {
              commitTurn();
            }
          },
          onBargeIn: () => {
            setIsSpeakingVAD(true);
            handleInterrupt();
          },
        },
        {
          silenceMs: currentAgent?.vad?.min_silence_ms || 650,
          idleThreshold: currentAgent?.vad?.threshold || 0.045,
          bargeInThreshold: audioMode === 'headphones' ? 0.06 : 0.14,
        }
      );
      vad.start();
      vadRef.current = vad;

      let mimeType = '';
      let container: 'webm' | 'mp4' | 'ogg' = 'webm';
      if (typeof MediaRecorder !== 'undefined' && typeof MediaRecorder.isTypeSupported === 'function') {
        if (MediaRecorder.isTypeSupported('audio/webm;codecs=opus')) {
          mimeType = 'audio/webm;codecs=opus';
          container = 'webm';
        } else if (MediaRecorder.isTypeSupported('audio/webm')) {
          mimeType = 'audio/webm';
          container = 'webm';
        } else if (MediaRecorder.isTypeSupported('audio/mp4')) {
          mimeType = 'audio/mp4';
          container = 'mp4';
        } else if (MediaRecorder.isTypeSupported('audio/ogg;codecs=opus')) {
          mimeType = 'audio/ogg;codecs=opus';
          container = 'ogg';
        }
      }

      mimeTypeRef.current = mimeType;
      containerRef.current = container;

      ws.send(
        JSON.stringify({
          type: 'session.start',
          agent_id: currentAgent?.id,
          input_audio: { container, codec: 'opus' },
          config: { echo_mode: audioMode, barge_in: true },
        })
      );

      startRecording();
      setState('listening');
    } catch (err: any) {
      console.error('Session initialization error:', err);
      cleanupAudio();
      setErrorMsg(err?.message || 'Voice session initialization failed. Please try again.');
      setState('idle');
    }
  };

  const endSession = () => {
    cleanupAudio();
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) {
      try {
        wsRef.current.send(JSON.stringify({ type: 'session.end' }));
        wsRef.current.close();
      } catch {}
      wsRef.current = null;
    }
    setState('idle');
    setIsSpeakingVAD(false);
  };

  const toggleMute = () => {
    if (audioStreamRef.current) {
      const nextMuted = !isMuted;
      audioStreamRef.current.getAudioTracks().forEach((track) => {
        track.enabled = !nextMuted;
      });
      setIsMuted(nextMuted);
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
    setResponseDelta('');
    setState('listening');
    startRecording();
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

  const getOrbAnalyser = () => {
    if (state === 'speaking') {
      return pcmPlayerRef.current.getAnalyserNode();
    }
    return micAnalyserRef.current;
  };

  return (
    <div className="max-w-4xl mx-auto space-y-6">
      {/* Top Active Agent Header & Switcher */}
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
            <p className="text-xs text-slate-400 mt-0.5 font-mono flex items-center gap-2">
              <span>LLM: {activeAgent?.llm?.model || 'llama-3.2-1b-instruct'}</span>
              <span>•</span>
              <span>STT: {activeAgent?.stt?.model || 'whisper-base'}</span>
              <span>•</span>
              <span>Voice: {activeAgent?.tts?.voice_id || 'af_heart'}</span>
            </p>
          </div>
        </div>

        {/* Controls: Agent Switcher + Audio Mode */}
        <div className="flex flex-wrap items-center gap-3">
          {/* Switch Agent Dropdown */}
          <div className="flex items-center gap-1.5 text-xs text-slate-400">
            <span>Switch:</span>
            <select
              value={activeAgent?.id || ''}
              onChange={(e) => handleSwitchAgent(e.target.value)}
              disabled={isSwitchingAgent}
              className="bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1 text-xs text-slate-200 focus:outline-none focus:border-indigo-500 disabled:opacity-50"
            >
              {availableAgents.map((a) => (
                <option key={a.id} value={a.id}>
                  {a.name}
                </option>
              ))}
            </select>
          </div>

          {/* Audio Output Mode */}
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
              Speakers
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

      {/* Main Animated Orb Stage */}
      <div className="flex flex-col items-center justify-center py-10 bg-slate-950 border border-slate-800 rounded-2xl relative overflow-hidden shadow-xl">
        <div className="absolute inset-0 bg-radial-gradient from-indigo-500/5 via-transparent to-transparent pointer-events-none" />

        <AudioOrb
          state={getOrbState()}
          analyser={getOrbAnalyser()}
          size={300}
          onClick={() => {
            if (state === 'speaking') handleInterrupt();
            else if (state === 'idle') startSession();
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
              ? 'Click orb or speak to interrupt (barge-in enabled)'
              : state === 'listening'
              ? isSpeakingVAD
                ? 'Speech detected — speak naturally'
                : 'Listening... (VAD automatically endpoints on silence)'
              : state === 'thinking'
              ? 'Processing speech...'
              : 'Click Start Conversation to begin hands-free dialogue'}
          </span>
        </div>

        {/* Action Controls: Start/End Session + Mute Toggle */}
        <div className="mt-6 flex items-center gap-3">
          {state === 'idle' ? (
            <button
              onClick={startSession}
              className="flex items-center gap-2 px-6 py-2.5 bg-indigo-600 hover:bg-indigo-500 text-white rounded-full text-sm font-semibold transition shadow-lg hover:shadow-indigo-500/25"
            >
              <Mic className="w-4 h-4" /> Start Conversation
            </button>
          ) : (
            <div className="flex items-center gap-3">
              <button
                onClick={toggleMute}
                className={`p-3 rounded-full transition shadow-md border ${
                  isMuted
                    ? 'bg-rose-500/20 text-rose-400 border-rose-500/30'
                    : 'bg-slate-900 hover:bg-slate-800 text-slate-300 border-slate-800'
                }`}
                title={isMuted ? 'Unmute microphone' : 'Mute microphone'}
              >
                {isMuted ? <MicOff className="w-4 h-4" /> : <Mic className="w-4 h-4" />}
              </button>

              <button
                onClick={endSession}
                className="flex items-center gap-2 px-6 py-2.5 bg-rose-600 hover:bg-rose-500 text-white rounded-full text-sm font-semibold transition shadow-lg"
              >
                <PhoneOff className="w-4 h-4" /> End Conversation
              </button>
            </div>
          )}
        </div>
      </div>

      {/* Live Transcript & Realtime Streaming Response */}
      {(state !== 'idle' || transcript || responseDelta) && (
        <div className="p-5 bg-slate-950 border border-slate-800 rounded-xl space-y-4">
          <div className="flex items-center justify-between border-b border-slate-900 pb-2.5">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-400 flex items-center gap-2">
              <Volume2 className="w-4 h-4 text-indigo-400" />
              Real-time Conversation
            </h3>
            {isSpeakingVAD && (
              <span className="text-[11px] text-sky-400 font-medium animate-pulse flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-sky-400" />
                Listening to speech...
              </span>
            )}
          </div>

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
                className="bg-slate-900/40 border border-slate-800/60 rounded-lg p-3 text-xs space-y-1.5"
              >
                <div className="flex items-center justify-between text-slate-500 text-[10px] font-mono">
                  <span>Turn {turns.length - i}</span>
                  <span>{t.id}</span>
                </div>
                <div className="text-slate-300">
                  <span className="text-slate-500 font-semibold mr-1.5">You:</span>
                  {t.transcript}
                </div>
                <div className="text-indigo-200">
                  <span className="text-indigo-400 font-semibold mr-1.5">
                    {activeAgent?.name || 'Assistant'}:
                  </span>
                  {t.response}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
};
