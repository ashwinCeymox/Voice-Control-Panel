import React, { useState, useEffect, useRef } from 'react';
import {
  Bot,
  Plus,
  Copy,
  Trash2,
  CheckCircle2,
  Play,
  RotateCcw,
  Sparkles,
  Send,
  Loader2,
  Sliders,
  Volume2,
  AlertCircle,
} from 'lucide-react';
import { Agent, Voice } from '../types/api';

const API_BASE = 'http://127.0.0.1:8000';
const DEFAULT_KEY = 'dev-voice-key-12345';
const AUTH_HEADERS = { Authorization: `Bearer ${DEFAULT_KEY}` };

export const AgentsPage: React.FC = () => {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [activeAgentId, setActiveAgentId] = useState<string>('');
  const [selectedAgent, setSelectedAgent] = useState<Agent | null>(null);
  const [loading, setLoading] = useState<boolean>(true);
  const [activatingId, setActivatingId] = useState<string | null>(null);
  const [saving, setSaving] = useState<boolean>(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  // Installed models & voices
  const [installedModels, setInstalledModels] = useState<{
    llm: string[];
    stt: string[];
    tts: string[];
  }>({
    llm: ['phi-2-chat', 'qwen2.5-1.5b-instruct'],
    stt: ['whisper-base', 'whisper-small'],
    tts: ['kokoro'],
  });
  const [voices, setVoices] = useState<Voice[]>([]);

  // Test-chat state
  const [chatMessages, setChatMessages] = useState<Array<{ role: 'user' | 'assistant'; content: string }>>([]);
  const [chatInput, setChatInput] = useState<string>('');
  const [isChatStreaming, setIsChatStreaming] = useState<boolean>(false);
  const chatScrollRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    fetchInitialData();
  }, []);

  useEffect(() => {
    if (chatScrollRef.current) {
      chatScrollRef.current.scrollTop = chatScrollRef.current.scrollHeight;
    }
  }, [chatMessages]);

  const fetchInitialData = async () => {
    try {
      setLoading(true);
      // Fetch agents
      const res = await fetch(`${API_BASE}/v1/agents`, { headers: AUTH_HEADERS });
      if (res.ok) {
        const data = await res.json();
        const list: Agent[] = data.agents || [];
        setAgents(list);

        // Fetch active agent state
        const actRes = await fetch(`${API_BASE}/v1/agents/active`, { headers: AUTH_HEADERS });
        if (actRes.ok) {
          const actData = await actRes.json();
          const actId = actData.active_agent_id || (list[0] ? list[0].id : '');
          setActiveAgentId(actId);
          const found = list.find((a) => a.id === actId) || list[0] || null;
          setSelectedAgent(found ? JSON.parse(JSON.stringify(found)) : null);
        } else if (list.length > 0) {
          setSelectedAgent(JSON.parse(JSON.stringify(list[0])));
        }
      }

      // Fetch installed models
      try {
        const modRes = await fetch(`${API_BASE}/v1/models/installed`, { headers: AUTH_HEADERS });
        if (modRes.ok) {
          const modData = await modRes.json();
          if (modData.models) {
            const llms = modData.models.filter((m: any) => m.category === 'llm').map((m: any) => m.id);
            const stts = modData.models.filter((m: any) => m.category === 'stt').map((m: any) => m.id);
            if (llms.length > 0) setInstalledModels((prev) => ({ ...prev, llm: llms }));
            if (stts.length > 0) setInstalledModels((prev) => ({ ...prev, stt: stts }));
          }
        }
      } catch {}

      // Fetch voices
      try {
        const vRes = await fetch(`${API_BASE}/v1/voices`, { headers: AUTH_HEADERS });
        if (vRes.ok) {
          const vData = await vRes.json();
          setVoices(vData.voices || []);
        }
      } catch {}
    } catch (err: any) {
      setErrorMsg(`Failed to connect to gateway: ${err.message}`);
    } finally {
      setLoading(false);
    }
  };

  const handleSelectAgent = (agent: Agent) => {
    setSelectedAgent(JSON.parse(JSON.stringify(agent)));
    setChatMessages([]);
    setErrorMsg(null);
    setSuccessMsg(null);
  };

  const handleActivate = async (agentId: string) => {
    setActivatingId(agentId);
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      const res = await fetch(`${API_BASE}/v1/agents/${agentId}/load`, {
        method: 'POST',
        headers: AUTH_HEADERS,
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Failed to activate agent');
      }
      setActiveAgentId(agentId);
      setSuccessMsg('Agent successfully loaded and memory verified!');
      setTimeout(() => setSuccessMsg(null), 4000);
    } catch (err: any) {
      setErrorMsg(err.message);
    } finally {
      setActivatingId(null);
    }
  };

  const handleSave = async () => {
    if (!selectedAgent) return;
    setSaving(true);
    setErrorMsg(null);
    setSuccessMsg(null);
    try {
      const isNew = !agents.some((a) => a.id === selectedAgent.id);
      const url = isNew ? `${API_BASE}/v1/agents` : `${API_BASE}/v1/agents/${selectedAgent.id}`;
      const method = isNew ? 'POST' : 'PUT';

      const res = await fetch(url, {
        method,
        headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
        body: JSON.stringify(selectedAgent),
      });

      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Save failed');
      }

      const savedAgent = await res.json();
      setSuccessMsg('Agent saved successfully!');
      setTimeout(() => setSuccessMsg(null), 3000);

      // Refresh list
      const listRes = await fetch(`${API_BASE}/v1/agents`, { headers: AUTH_HEADERS });
      if (listRes.ok) {
        const listData = await listRes.json();
        setAgents(listData.agents || []);
      }
      setSelectedAgent(savedAgent);
    } catch (err: any) {
      setErrorMsg(err.message);
    } finally {
      setSaving(false);
    }
  };

  const handleDuplicate = async (agentId: string) => {
    try {
      const res = await fetch(`${API_BASE}/v1/agents/${agentId}/duplicate`, {
        method: 'POST',
        headers: AUTH_HEADERS,
      });
      if (!res.ok) throw new Error('Duplicate failed');
      const duplicated = await res.json();
      setAgents((prev) => [duplicated, ...prev]);
      setSelectedAgent(duplicated);
      setSuccessMsg(`Duplicated as "${duplicated.name}"`);
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err: any) {
      setErrorMsg(err.message);
    }
  };

  const handleDelete = async (agentId: string) => {
    if (agentId === 'agent-default-assistant') {
      setErrorMsg('Default agent is protected and cannot be deleted.');
      return;
    }
    if (!confirm('Are you sure you want to delete this agent?')) return;
    try {
      const res = await fetch(`${API_BASE}/v1/agents/${agentId}`, {
        method: 'DELETE',
        headers: AUTH_HEADERS,
      });
      if (!res.ok) {
        const err = await res.json();
        throw new Error(err.detail || 'Delete failed');
      }
      const updated = agents.filter((a) => a.id !== agentId);
      setAgents(updated);
      if (selectedAgent?.id === agentId) {
        setSelectedAgent(updated[0] || null);
      }
      setSuccessMsg('Agent deleted.');
      setTimeout(() => setSuccessMsg(null), 3000);
    } catch (err: any) {
      setErrorMsg(err.message);
    }
  };

  const handleCreateNew = () => {
    const newAgent: Agent = {
      id: `agent-${Date.now().toString(36)}`,
      name: 'New Custom Assistant',
      description: 'A helpful voice AI agent.',
      system_prompt: 'You are a warm, concise voice AI assistant. Respond in 1-2 natural sentences.',
      llm: { model: installedModels.llm[0] || 'phi-2-chat', temperature: 0.7, max_tokens: 512 },
      stt: { model: installedModels.stt[0] || 'whisper-base', language: 'auto' },
      tts: { engine: 'kokoro', voice_id: 'af_heart', speed: 1.0 },
      vad: { mode: 'energy', threshold: 0.045, min_silence_ms: 650 },
      audio: { echo_mode: 'laptop_speakers', barge_in: true },
      is_active: false,
    };
    setSelectedAgent(newAgent);
    setChatMessages([]);
  };

  const handleSendTestChat = async () => {
    if (!chatInput.trim() || !selectedAgent || isChatStreaming) return;
    const userText = chatInput.trim();
    setChatInput('');
    setChatMessages((prev) => [...prev, { role: 'user', content: userText }]);
    setIsChatStreaming(true);

    try {
      const res = await fetch(`${API_BASE}/v1/agents/${selectedAgent.id}/test-chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...AUTH_HEADERS },
        body: JSON.stringify({ message: userText }),
      });

      if (!res.ok || !res.body) {
        throw new Error('Test chat request failed');
      }

      setChatMessages((prev) => [...prev, { role: 'assistant', content: '' }]);
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let accumulated = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        const text = decoder.decode(value, { stream: true });
        const lines = text.split('\n');

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const raw = line.slice(6).trim();
            if (raw === '[DONE]') break;
            try {
              const parsed = JSON.parse(raw);
              const delta = parsed.choices?.[0]?.delta?.content || '';
              accumulated += delta;
              setChatMessages((prev) => {
                const copy = [...prev];
                if (copy.length > 0) {
                  copy[copy.length - 1] = { role: 'assistant', content: accumulated };
                }
                return copy;
              });
            } catch {}
          }
        }
      }
    } catch (err: any) {
      setChatMessages((prev) => [
        ...prev,
        { role: 'assistant', content: `[Error: ${err.message}]` },
      ]);
    } finally {
      setIsChatStreaming(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100 tracking-tight flex items-center gap-2.5">
            <Bot className="w-7 h-7 text-indigo-400" />
            Agent Configurations
          </h1>
          <p className="text-sm text-slate-400 mt-1">
            Create, tune, load, and test voice personas with custom system prompts, models, and VAD parameters.
          </p>
        </div>
        <button
          onClick={handleCreateNew}
          className="flex items-center gap-2 px-3.5 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition shadow-sm"
        >
          <Plus className="w-4 h-4" />
          Create Agent
        </button>
      </div>

      {errorMsg && (
        <div className="p-3.5 bg-rose-500/10 border border-rose-500/30 rounded-lg flex items-center gap-2.5 text-sm text-rose-300">
          <AlertCircle className="w-4 h-4 shrink-0 text-rose-400" />
          <span>{errorMsg}</span>
        </div>
      )}

      {successMsg && (
        <div className="p-3.5 bg-emerald-500/10 border border-emerald-500/30 rounded-lg flex items-center gap-2.5 text-sm text-emerald-300">
          <CheckCircle2 className="w-4 h-4 shrink-0 text-emerald-400" />
          <span>{successMsg}</span>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-12 gap-6">
        {/* Left column: Agent list */}
        <div className="lg:col-span-4 space-y-3">
          <div className="bg-slate-950 border border-slate-800 rounded-xl p-3">
            <h3 className="text-xs font-semibold uppercase tracking-wider text-slate-500 px-2 py-1">
              Saved Agents ({agents.length})
            </h3>
            <div className="mt-2 space-y-1.5">
              {loading ? (
                <div className="p-6 text-center text-slate-500 text-sm flex items-center justify-center gap-2">
                  <Loader2 className="w-4 h-4 animate-spin" /> Loading agents...
                </div>
              ) : (
                agents.map((agent) => {
                  const isSelected = selectedAgent?.id === agent.id;
                  const isActive = activeAgentId === agent.id;

                  return (
                    <div
                      key={agent.id}
                      onClick={() => handleSelectAgent(agent)}
                      className={`group p-3 rounded-lg border transition cursor-pointer flex items-start justify-between ${
                        isSelected
                          ? 'bg-indigo-950/40 border-indigo-500/40 shadow-sm'
                          : 'bg-slate-900/60 border-slate-800/80 hover:bg-slate-900 hover:border-slate-700'
                      }`}
                    >
                      <div className="min-w-0 pr-2">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-sm text-slate-200 truncate">
                            {agent.name}
                          </span>
                          {isActive && (
                            <span className="text-[10px] px-2 py-0.5 rounded-full bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 font-mono font-medium shrink-0 flex items-center gap-1">
                              <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                              Active
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-slate-400 truncate mt-1">
                          {agent.description || agent.system_prompt}
                        </p>
                        <div className="flex items-center gap-2 mt-2 text-[11px] text-slate-500 font-mono">
                          <span>{agent.llm.model}</span>
                          <span>•</span>
                          <span>{agent.tts.voice_id}</span>
                        </div>
                      </div>
                    </div>
                  );
                })
              )}
            </div>
          </div>
        </div>

        {/* Right column: Editor & Test Chat */}
        {selectedAgent ? (
          <div className="lg:col-span-8 space-y-6">
            <div className="bg-slate-950 border border-slate-800 rounded-xl p-6 space-y-6">
              {/* Agent Title & Top Actions */}
              <div className="flex flex-wrap items-center justify-between gap-4 border-b border-slate-800 pb-4">
                <div>
                  <div className="flex items-center gap-3">
                    <input
                      type="text"
                      value={selectedAgent.name}
                      onChange={(e) =>
                        setSelectedAgent({ ...selectedAgent, name: e.target.value })
                      }
                      className="text-xl font-bold bg-transparent text-slate-100 border-b border-dashed border-slate-700 focus:border-indigo-400 focus:outline-none"
                    />
                    {activeAgentId === selectedAgent.id ? (
                      <span className="px-2.5 py-1 text-xs rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 font-medium">
                        Live Active
                      </span>
                    ) : (
                      <button
                        onClick={() => handleActivate(selectedAgent.id)}
                        disabled={activatingId === selectedAgent.id}
                        className="flex items-center gap-1.5 px-3 py-1 bg-emerald-600 hover:bg-emerald-500 text-white rounded-md text-xs font-medium transition shadow-sm"
                      >
                        {activatingId === selectedAgent.id ? (
                          <>
                            <Loader2 className="w-3.5 h-3.5 animate-spin" /> Verifying RAM...
                          </>
                        ) : (
                          <>
                            <Play className="w-3.5 h-3.5 fill-current" /> Set as Active
                          </>
                        )}
                      </button>
                    )}
                  </div>
                  <input
                    type="text"
                    placeholder="Short description..."
                    value={selectedAgent.description || ''}
                    onChange={(e) =>
                      setSelectedAgent({ ...selectedAgent, description: e.target.value })
                    }
                    className="text-xs text-slate-400 bg-transparent w-full mt-1.5 focus:outline-none"
                  />
                </div>

                <div className="flex items-center gap-2">
                  <button
                    onClick={() => handleDuplicate(selectedAgent.id)}
                    className="p-2 text-slate-400 hover:text-slate-200 hover:bg-slate-900 rounded-lg border border-slate-800"
                    title="Duplicate Agent"
                  >
                    <Copy className="w-4 h-4" />
                  </button>
                  {selectedAgent.id !== 'agent-default-assistant' && (
                    <button
                      onClick={() => handleDelete(selectedAgent.id)}
                      className="p-2 text-rose-400 hover:text-rose-300 hover:bg-rose-950/40 rounded-lg border border-rose-900/50"
                      title="Delete Agent"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  )}
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="flex items-center gap-1.5 px-4 py-2 bg-indigo-600 hover:bg-indigo-500 text-white rounded-lg text-sm font-medium transition shadow-sm ml-2"
                  >
                    {saving ? <Loader2 className="w-4 h-4 animate-spin" /> : 'Save Changes'}
                  </button>
                </div>
              </div>

              {/* System Prompt */}
              <div className="space-y-2">
                <label className="text-xs font-semibold text-slate-300 flex items-center gap-2">
                  <Sparkles className="w-3.5 h-3.5 text-indigo-400" />
                  System Instructions & Persona
                </label>
                <textarea
                  rows={4}
                  value={selectedAgent.system_prompt}
                  onChange={(e) =>
                    setSelectedAgent({ ...selectedAgent, system_prompt: e.target.value })
                  }
                  className="w-full bg-slate-900 border border-slate-800 rounded-lg p-3 text-sm text-slate-200 focus:border-indigo-500 focus:outline-none resize-none leading-relaxed"
                  placeholder="You are a concise voice AI assistant..."
                />
              </div>

              {/* Model Triad Grid */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                {/* LLM Model */}
                <div className="p-4 bg-slate-900/60 border border-slate-800 rounded-lg space-y-3">
                  <span className="text-xs font-semibold text-indigo-300 uppercase tracking-wider">
                    LLM Engine
                  </span>
                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">Model</label>
                    <select
                      value={selectedAgent.llm.model}
                      onChange={(e) =>
                        setSelectedAgent({
                          ...selectedAgent,
                          llm: { ...selectedAgent.llm, model: e.target.value },
                        })
                      }
                      className="w-full bg-slate-950 border border-slate-800 rounded-md p-2 text-xs text-slate-200 focus:outline-none"
                    >
                      {installedModels.llm.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <div className="flex justify-between text-[11px] text-slate-400 mb-1">
                      <span>Temperature</span>
                      <span>{selectedAgent.llm.temperature}</span>
                    </div>
                    <input
                      type="range"
                      min="0.1"
                      max="1.5"
                      step="0.05"
                      value={selectedAgent.llm.temperature}
                      onChange={(e) =>
                        setSelectedAgent({
                          ...selectedAgent,
                          llm: { ...selectedAgent.llm, temperature: parseFloat(e.target.value) },
                        })
                      }
                      className="w-full accent-indigo-500"
                    />
                  </div>
                </div>

                {/* STT Model */}
                <div className="p-4 bg-slate-900/60 border border-slate-800 rounded-lg space-y-3">
                  <span className="text-xs font-semibold text-sky-300 uppercase tracking-wider">
                    STT Engine
                  </span>
                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">Model</label>
                    <select
                      value={selectedAgent.stt.model}
                      onChange={(e) =>
                        setSelectedAgent({
                          ...selectedAgent,
                          stt: { ...selectedAgent.stt, model: e.target.value },
                        })
                      }
                      className="w-full bg-slate-950 border border-slate-800 rounded-md p-2 text-xs text-slate-200 focus:outline-none"
                    >
                      {installedModels.stt.map((m) => (
                        <option key={m} value={m}>
                          {m}
                        </option>
                      ))}
                    </select>
                  </div>
                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">Language</label>
                    <select
                      value={selectedAgent.stt.language}
                      onChange={(e) =>
                        setSelectedAgent({
                          ...selectedAgent,
                          stt: { ...selectedAgent.stt, language: e.target.value },
                        })
                      }
                      className="w-full bg-slate-950 border border-slate-800 rounded-md p-2 text-xs text-slate-200 focus:outline-none"
                    >
                      <option value="auto">Auto-detect</option>
                      <option value="en">English (en)</option>
                      <option value="es">Spanish (es)</option>
                      <option value="fr">French (fr)</option>
                      <option value="de">German (de)</option>
                    </select>
                  </div>
                </div>

                {/* TTS Model & Voice */}
                <div className="p-4 bg-slate-900/60 border border-slate-800 rounded-lg space-y-3">
                  <span className="text-xs font-semibold text-emerald-300 uppercase tracking-wider">
                    TTS Voice
                  </span>
                  <div>
                    <label className="text-[11px] text-slate-400 block mb-1">Voice Picker</label>
                    <select
                      value={selectedAgent.tts.voice_id}
                      onChange={(e) =>
                        setSelectedAgent({
                          ...selectedAgent,
                          tts: { ...selectedAgent.tts, voice_id: e.target.value },
                        })
                      }
                      className="w-full bg-slate-950 border border-slate-800 rounded-md p-2 text-xs text-slate-200 focus:outline-none"
                    >
                      {voices.length > 0 ? (
                        voices.map((v) => (
                          <option key={v.voice_id} value={v.voice_id}>
                            {v.name} ({v.gender || 'neutral'})
                          </option>
                        ))
                      ) : (
                        <>
                          <option value="af_heart">Heart (Female)</option>
                          <option value="am_adam">Adam (Male)</option>
                          <option value="af_bella">Bella (Female)</option>
                        </>
                      )}
                    </select>
                  </div>
                  <div>
                    <div className="flex justify-between text-[11px] text-slate-400 mb-1">
                      <span>Speed</span>
                      <span>{selectedAgent.tts.speed}x</span>
                    </div>
                    <input
                      type="range"
                      min="0.75"
                      max="1.5"
                      step="0.05"
                      value={selectedAgent.tts.speed}
                      onChange={(e) =>
                        setSelectedAgent({
                          ...selectedAgent,
                          tts: { ...selectedAgent.tts, speed: parseFloat(e.target.value) },
                        })
                      }
                      className="w-full accent-emerald-500"
                    />
                  </div>
                </div>
              </div>

              {/* Audio & VAD Settings */}
              <div className="p-4 bg-slate-900/40 border border-slate-800/80 rounded-lg grid grid-cols-1 md:grid-cols-2 gap-4">
                <div>
                  <label className="text-xs font-semibold text-slate-300 flex items-center gap-1.5 mb-2">
                    <Sliders className="w-3.5 h-3.5 text-slate-400" />
                    Audio Echo & Barge-In
                  </label>
                  <select
                    value={selectedAgent.audio?.echo_mode || 'laptop_speakers'}
                    onChange={(e) =>
                      setSelectedAgent({
                        ...selectedAgent,
                        audio: {
                          ...selectedAgent.audio,
                          echo_mode: e.target.value as any,
                          barge_in: true,
                        },
                      })
                    }
                    className="w-full bg-slate-950 border border-slate-800 rounded-md p-2 text-xs text-slate-200 focus:outline-none"
                  >
                    <option value="laptop_speakers">Laptop Speakers (Dual-Threshold Barge-in)</option>
                    <option value="headphones">Headphones Mode (Immediate Threshold)</option>
                    <option value="half_duplex">Half-Duplex (Mute Mic During Playback)</option>
                  </select>
                </div>

                <div>
                  <label className="text-xs font-semibold text-slate-300 flex items-center gap-1.5 mb-2">
                    <Volume2 className="w-3.5 h-3.5 text-slate-400" />
                    VAD Trailing Silence Window
                  </label>
                  <div className="flex items-center gap-3">
                    <input
                      type="range"
                      min="350"
                      max="1200"
                      step="50"
                      value={selectedAgent.vad?.min_silence_ms || 650}
                      onChange={(e) =>
                        setSelectedAgent({
                          ...selectedAgent,
                          vad: {
                            ...selectedAgent.vad,
                            min_silence_ms: parseInt(e.target.value, 10),
                            mode: 'energy',
                            threshold: 0.045,
                          },
                        })
                      }
                      className="w-full accent-indigo-500"
                    />
                    <span className="text-xs font-mono text-slate-400 shrink-0 w-16 text-right">
                      {selectedAgent.vad?.min_silence_ms || 650} ms
                    </span>
                  </div>
                </div>
              </div>
            </div>

            {/* Test Chat Panel */}
            <div className="bg-slate-950 border border-slate-800 rounded-xl p-5 space-y-4">
              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <div className="flex items-center gap-2">
                  <Sparkles className="w-4 h-4 text-indigo-400" />
                  <h3 className="text-sm font-semibold text-slate-200">
                    Test Chat Stream: {selectedAgent.name}
                  </h3>
                </div>
                <button
                  onClick={() => setChatMessages([])}
                  className="text-xs text-slate-400 hover:text-slate-200 flex items-center gap-1"
                >
                  <RotateCcw className="w-3 h-3" /> Clear History
                </button>
              </div>

              {/* Chat messages */}
              <div
                ref={chatScrollRef}
                className="h-56 overflow-y-auto space-y-3 pr-2 scrollbar-thin"
              >
                {chatMessages.length === 0 ? (
                  <div className="h-full flex items-center justify-center text-xs text-slate-500 italic">
                    Send a test prompt to verify response style, conciseness, and persona.
                  </div>
                ) : (
                  chatMessages.map((msg, i) => (
                    <div
                      key={i}
                      className={`flex ${
                        msg.role === 'user' ? 'justify-end' : 'justify-start'
                      }`}
                    >
                      <div
                        className={`max-w-[80%] rounded-lg px-3.5 py-2 text-xs leading-relaxed ${
                          msg.role === 'user'
                            ? 'bg-indigo-600 text-white rounded-br-none'
                            : 'bg-slate-900 border border-slate-800 text-slate-200 rounded-bl-none'
                        }`}
                      >
                        {msg.content || (
                          <span className="animate-pulse flex items-center gap-1">
                            <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
                            <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
                            <span className="w-1.5 h-1.5 rounded-full bg-slate-400" />
                          </span>
                        )}
                      </div>
                    </div>
                  ))
                )}
              </div>

              {/* Chat Input */}
              <div className="flex gap-2">
                <input
                  type="text"
                  placeholder={`Say something to ${selectedAgent.name}...`}
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && handleSendTestChat()}
                  disabled={isChatStreaming}
                  className="flex-1 bg-slate-900 border border-slate-800 rounded-lg px-3.5 py-2 text-xs text-slate-200 focus:border-indigo-500 focus:outline-none"
                />
                <button
                  onClick={handleSendTestChat}
                  disabled={!chatInput.trim() || isChatStreaming}
                  className="px-4 py-2 bg-indigo-600 hover:bg-indigo-500 disabled:opacity-50 text-white rounded-lg text-xs font-medium transition flex items-center gap-1.5"
                >
                  {isChatStreaming ? (
                    <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  ) : (
                    <Send className="w-3.5 h-3.5" />
                  )}
                  Send
                </button>
              </div>
            </div>
          </div>
        ) : (
          <div className="lg:col-span-8 flex items-center justify-center p-12 text-slate-500 text-sm">
            Select an agent from the list or create a new one.
          </div>
        )}
      </div>
    </div>
  );
};
