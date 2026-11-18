import React, { useState, useEffect } from 'react';
import { Copy, Check, Play, Code2, Cpu, Volume2 } from 'lucide-react';
import { fetchAdminJson, getAdminHeaders } from './api';

type EndpointId = 'llm_chat' | 'stt_batch' | 'stt_stream' | 'tts_stream' | 'voice_stream';
type LangId = 'python' | 'curl' | 'javascript' | 'openai';

interface InstalledModel {
  id: string;
  name: string;
  category: string;
  kind?: string | null;
}

interface VoiceItem {
  id: string;
  name: string;
}

export const DocsSnippets: React.FC = () => {
  const [endpoint, setEndpoint] = useState<EndpointId>('llm_chat');
  const [lang, setLang] = useState<LangId>('python');
  const defaultUrl = typeof window !== 'undefined' && window.location.hostname && window.location.hostname !== 'localhost' && window.location.hostname !== '127.0.0.1'
    ? window.location.origin
    : 'http://127.0.0.1:8000';
  const [baseUrl, setBaseUrl] = useState<string>(defaultUrl);
  const [apiKey, setApiKey] = useState<string>('va_live_your_api_key_here');

  const [models, setModels] = useState<InstalledModel[]>([]);
  const [selectedModel, setSelectedModel] = useState<string>('llama-3.2-1b-instruct');
  const [voices, setVoices] = useState<VoiceItem[]>([]);
  const [selectedVoice, setSelectedVoice] = useState<string>('af_bella');

  const [copied, setCopied] = useState<boolean>(false);
  const [testing, setTesting] = useState<boolean>(false);
  const [testOutput, setTestOutput] = useState<string | null>(null);

  useEffect(() => {
    // Load dashboard base url
    fetchAdminJson<any>('/v1/admin/settings/dashboard')
      .then((data) => {
        if (data.base_url) setBaseUrl(data.base_url);
      })
      .catch(() => {});
  }, []);

  // Load endpoint-specific models from the unified model registry
  useEffect(() => {
    let query = '';
    if (endpoint === 'llm_chat' || endpoint === 'voice_stream') {
      query = '?task=llm&available=true';
    } else if (endpoint === 'stt_batch') {
      query = '?task=stt&kind=batch&available=true';
    } else if (endpoint === 'stt_stream') {
      query = '?task=stt&kind=streaming&available=true';
    } else if (endpoint === 'tts_stream') {
      query = '?task=tts&available=true';
    }

    fetchAdminJson<any>(`/v1/models${query}`)
      .then((data) => {
        const rawList = data.models || (Array.isArray(data) ? data : []);
        const mods = rawList.map((m: any) => ({
          id: m.id,
          name: m.display_name || m.name || m.id,
          category: m.task || m.category || '',
          kind: m.kind,
        }));
        setModels(mods);
        if (mods.length > 0) {
          if (!mods.some((m: any) => m.id === selectedModel)) {
            setSelectedModel(mods[0].id);
          }
        } else {
          setSelectedModel('');
        }
      })
      .catch(() => {});
  }, [endpoint]);

  // Load voices for chosen TTS engine (cascading engine -> voices)
  useEffect(() => {
    if (endpoint !== 'tts_stream') return;
    const engineQuery = selectedModel ? `?engine=${encodeURIComponent(selectedModel)}` : '';
    fetch(`/v1/voices${engineQuery}`, { headers: getAdminHeaders() })
      .then((r) => r.json())
      .then((data) => {
        const vList = (data.voices || []).map((v: any) => ({
          id: v.voice_id || v.id,
          name: v.name || v.voice_id || v.id,
        }));
        setVoices(vList);
        if (vList.length > 0) {
          if (!vList.some((v: any) => v.id === selectedVoice)) {
            setSelectedVoice(vList[0].id);
          }
        } else {
          setSelectedVoice('');
        }
      })
      .catch(() => {});
  }, [endpoint, selectedModel]);

  // Generate code snippet text
  const renderSnippet = (): string => {
    const bUrl = baseUrl.replace(/\/+$/, '');
    const key = apiKey || 'va_live_••••••••';
    const mod = selectedModel || 'llama-3.2-1b-instruct';
    const vc = selectedVoice || 'af_bella';

    if (endpoint === 'llm_chat') {
      if (lang === 'openai') {
        return `from openai import OpenAI

# Compatible with OpenAI official SDK by pointing base_url to local gateway
client = OpenAI(
    base_url="${bUrl}/v1",
    api_key="${key}",
)

response = client.chat.completions.create(
    model="${mod}",
    messages=[{"role": "user", "content": "Hello! Introduce yourself."}],
    stream=True,
)

for chunk in response:
    content = chunk.choices[0].delta.content or ""
    print(content, end="", flush=True)
print()`;
      }

      if (lang === 'python') {
        return `import json
import httpx

url = "${bUrl}/v1/chat/completions"
headers = {"Authorization": f"Bearer ${key}"}
payload = {
    "model": "${mod}",
    "messages": [{"role": "user", "content": "Hello! Introduce yourself."}],
    "stream": True,
}

with httpx.stream("POST", url, json=payload, headers=headers, timeout=120) as r:
    r.raise_for_status()
    for line in r.iter_lines():
        if line.startswith("data: ") and line != "data: [DONE]":
            chunk = json.loads(line[6:])
            print(chunk["choices"][0]["delta"].get("content", ""), end="", flush=True)
print()`;
      }

      if (lang === 'curl') {
        return `curl -N -X POST "${bUrl}/v1/chat/completions" \\
  -H "Authorization: Bearer ${key}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "model": "${mod}",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": true
  }'`;
      }

      if (lang === 'javascript') {
        return `const response = await fetch("${bUrl}/v1/chat/completions", {
  method: "POST",
  headers: {
    "Authorization": "Bearer ${key}",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    model: "${mod}",
    messages: [{ role: "user", content: "Hello!" }],
    stream: true,
  }),
});

const reader = response.body.getReader();
const decoder = new TextDecoder();
while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  console.log(decoder.decode(value));
}`;
      }
    }

    if (endpoint === 'stt_batch') {
      if (lang === 'python') {
        return `import httpx

url = "${bUrl}/v1/audio/transcriptions"
headers = {"Authorization": f"Bearer ${key}"}

with open("audio.wav", "rb") as f:
    resp = httpx.post(
        url,
        headers=headers,
        files={"file": ("audio.wav", f, "audio/wav")},
        data={"model": "${mod}", "temperature": "0"},
        timeout=120,
    )
resp.raise_for_status()
print("Transcript:", resp.json()["text"])`;
      }

      if (lang === 'curl') {
        return `curl -X POST "${bUrl}/v1/audio/transcriptions" \\
  -H "Authorization: Bearer ${key}" \\
  -F "file=@audio.wav" \\
  -F "model=${mod}"`;
      }

      if (lang === 'javascript') {
        return `const formData = new FormData();
formData.append("file", audioBlob, "audio.wav");
formData.append("model", "${mod}");

const res = await fetch("${bUrl}/v1/audio/transcriptions", {
  method: "POST",
  headers: { "Authorization": "Bearer ${key}" },
  body: formData,
});
const data = await res.json();
console.log("Transcript:", data.text);`;
      }
    }

    if (endpoint === 'tts_stream') {
      if (lang === 'python') {
        return `import httpx

url = "${bUrl}/v1/text-to-speech/${vc}/stream"
headers = {"Authorization": f"Bearer ${key}"}
payload = {
    "text": "Hello! Welcome to the local voice AI gateway running fully offline.",
    "model_id": "${mod}",
    "voice_settings": {"speed": 1.0},
}

with httpx.stream("POST", url, json=payload, headers=headers, timeout=60) as resp:
    resp.raise_for_status()
    with open("output.pcm", "wb") as f:
        for chunk in resp.iter_bytes():
            f.write(chunk)

print("Saved output.pcm (24 kHz, 16-bit PCM mono)")
# Play command: ffplay -f s16le -ar 24000 -ac 1 output.pcm`;
      }

      if (lang === 'curl') {
        return `curl -X POST "${bUrl}/v1/text-to-speech/${vc}/stream" \\
  -H "Authorization: Bearer ${key}" \\
  -H "Content-Type: application/json" \\
  -d '{
    "text": "Hello world from offline speech synthesis.",
    "model_id": "${mod}"
  }' \\
  --output output.pcm`;
      }

      if (lang === 'javascript') {
        return `const res = await fetch("${bUrl}/v1/text-to-speech/${vc}/stream", {
  method: "POST",
  headers: {
    "Authorization": "Bearer ${key}",
    "Content-Type": "application/json",
  },
  body: JSON.stringify({
    text: "Hello from local voice agent.",
    model_id: "${mod}",
  }),
});

const audioData = await res.arrayBuffer();
console.log("Received PCM bytes:", audioData.byteLength);`;
      }
    }

    if (endpoint === 'stt_stream') {
      return `import asyncio
import websockets
import json

ws_url = "${bUrl.replace(/^http/, 'ws')}/v1/audio/transcriptions/stream?model=${mod}"
headers = {"Authorization": f"Bearer ${key}"}

async def stream_audio():
    async with websockets.connect(ws_url, extra_headers=headers) as ws:
        # 1. Send start event
        await ws.send(json.dumps({
            "type": "start",
            "sample_rate": 16000,
            "channels": 1,
            "language": "en"
        }))
        
        # 2. Stream raw 16-bit PCM chunks as binary
        with open("stream.raw", "rb") as f:
            while chunk := f.read(3200):  # 100ms chunks
                await ws.send(chunk)
                await asyncio.sleep(0.1)

        # 3. Listen for real-time partial & final events
        async for msg in ws:
            event = json.loads(msg)
            print("STT Event:", event)

asyncio.run(stream_audio())`;
    }

    if (endpoint === 'voice_stream') {
      return `// Connect to bidirectional conversational voice WebSocket
const wsUrl = "${bUrl.replace(/^http/, 'ws')}/v1/voice/stream";
const ws = new WebSocket(wsUrl);

ws.onopen = () => {
  // Direct API Key authentication handshake
  ws.send(JSON.stringify({
    type: "auth",
    api_key: "${key}",
  }));
};

ws.onmessage = (event) => {
  if (typeof event.data === "string") {
    const msg = JSON.parse(event.data);
    console.log("Voice Event:", msg.type, msg);
  } else {
    // Binary chunk: 24 kHz 16-bit PCM audio from TTS
    console.log("Audio PCM chunk:", event.data.byteLength);
  }
};`;
    }

    return '# Snippet template ready';
  };

  const handleCopy = () => {
    navigator.clipboard.writeText(renderSnippet());
    setCopied(true);
    setTimeout(() => setCopied(false), 3000);
  };

  const handleTryIt = async () => {
    setTesting(true);
    setTestOutput(null);

    try {
      if (endpoint === 'llm_chat') {
        const res = await fetch('/v1/chat/completions', {
          method: 'POST',
          headers: getAdminHeaders(),
          body: JSON.stringify({
            model: selectedModel,
            messages: [{ role: 'user', content: 'Say hello in 5 words.' }],
            stream: false,
          }),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setTestOutput(`Error (HTTP ${res.status}): ${data.detail || data.error?.message || JSON.stringify(data)}`);
        } else {
          setTestOutput(JSON.stringify(data, null, 2));
        }
      } else if (endpoint === 'tts_stream') {
        const res = await fetch(`/v1/text-to-speech/${selectedVoice}/stream`, {
          method: 'POST',
          headers: getAdminHeaders(),
          body: JSON.stringify({
            text: 'Testing text to speech synthesis.',
            model_id: selectedModel,
          }),
        });
        if (!res.ok) {
          const errData = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
          setTestOutput(`Error (HTTP ${res.status}): ${errData.detail || JSON.stringify(errData)}`);
          return;
        }
        const buf = await res.arrayBuffer();
        setTestOutput(`Success! Received ${buf.byteLength} bytes of 24 kHz 16-bit PCM mono audio.`);
      } else if (endpoint === 'stt_batch') {
        setTestOutput('STT Batch requires uploading a WAV file. Use curl or Python snippet with your audio file.');
      } else {
        setTestOutput('WebSocket endpoints can be connected directly via your client or Python script.');
      }
    } catch (err: any) {
      setTestOutput(`Error executing call: ${err.message}`);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="max-w-6xl mx-auto space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-slate-100">API Documentation & Code Snippets</h1>
        <p className="text-sm text-slate-400">
          Copy production-ready integration code pointing to your local gateway on port 8000
        </p>
      </div>

      {/* Endpoint Selector Tabs */}
      <div className="flex bg-slate-950 border border-slate-800 rounded-xl p-1 overflow-x-auto text-xs font-medium">
        <button
          onClick={() => setEndpoint('llm_chat')}
          className={`flex-1 py-2 px-3 rounded-lg whitespace-nowrap transition-all ${
            endpoint === 'llm_chat' ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          LLM Chat (Streaming)
        </button>
        <button
          onClick={() => setEndpoint('tts_stream')}
          className={`flex-1 py-2 px-3 rounded-lg whitespace-nowrap transition-all ${
            endpoint === 'tts_stream' ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          TTS Audio Stream
        </button>
        <button
          onClick={() => setEndpoint('stt_batch')}
          className={`flex-1 py-2 px-3 rounded-lg whitespace-nowrap transition-all ${
            endpoint === 'stt_batch' ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          STT Batch
        </button>
        <button
          onClick={() => setEndpoint('stt_stream')}
          className={`flex-1 py-2 px-3 rounded-lg whitespace-nowrap transition-all ${
            endpoint === 'stt_stream' ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          STT Stream (WS)
        </button>
        <button
          onClick={() => setEndpoint('voice_stream')}
          className={`flex-1 py-2 px-3 rounded-lg whitespace-nowrap transition-all ${
            endpoint === 'voice_stream' ? 'bg-indigo-600 text-white shadow-sm' : 'text-slate-400 hover:text-slate-200'
          }`}
        >
          Voice Pipeline (WS)
        </button>
      </div>

      {/* Controls Bar: Model, Voice, Language & Key Customization */}
      <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 flex flex-wrap items-center justify-between gap-4 text-xs">
        <div className="flex flex-wrap items-center gap-4">
          {/* Model picker */}
          <div className="flex items-center gap-2">
            <Cpu className="w-3.5 h-3.5 text-indigo-400" />
            <span className="text-slate-400 font-medium">Model:</span>
            <select
              value={selectedModel}
              onChange={(e) => setSelectedModel(e.target.value)}
              className="bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-slate-200 outline-none font-mono"
            >
              {models.length === 0 ? (
                <option value="">No models available for this task</option>
              ) : (
                models.map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.name} {m.kind ? `(${m.kind})` : ''}
                  </option>
                ))
              )}
            </select>
          </div>

          {/* Voice picker for TTS */}
          {endpoint === 'tts_stream' && (
            <div className="flex items-center gap-2">
              <Volume2 className="w-3.5 h-3.5 text-indigo-400" />
              <span className="text-slate-400 font-medium">Voice:</span>
              <select
                value={selectedVoice}
                onChange={(e) => setSelectedVoice(e.target.value)}
                className="bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-slate-200 outline-none font-mono"
              >
                {voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {v.name}
                  </option>
                ))}
              </select>
            </div>
          )}

          {/* Key placeholder input */}
          <div className="flex items-center gap-2">
            <span className="text-slate-400 font-medium">Test Key:</span>
            <input
              type="text"
              value={apiKey}
              onChange={(e) => setApiKey(e.target.value)}
              placeholder="va_live_••••••"
              className="bg-slate-900 border border-slate-800 rounded-lg px-2.5 py-1.5 text-slate-300 font-mono text-[11px] w-36 outline-none"
            />
          </div>
        </div>

        {/* Language Tabs */}
        <div className="flex bg-slate-900 border border-slate-800 rounded-lg p-0.5">
          <button
            onClick={() => setLang('python')}
            className={`px-3 py-1 rounded text-xs font-medium transition-all ${
              lang === 'python' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            Python
          </button>
          <button
            onClick={() => setLang('curl')}
            className={`px-3 py-1 rounded text-xs font-medium transition-all ${
              lang === 'curl' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            curl
          </button>
          <button
            onClick={() => setLang('javascript')}
            className={`px-3 py-1 rounded text-xs font-medium transition-all ${
              lang === 'javascript' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
            }`}
          >
            JavaScript
          </button>
          {endpoint === 'llm_chat' && (
            <button
              onClick={() => setLang('openai')}
              className={`px-3 py-1 rounded text-xs font-medium transition-all ${
                lang === 'openai' ? 'bg-indigo-600 text-white' : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              OpenAI SDK
            </button>
          )}
        </div>
      </div>

      {/* Snippet Code Window */}
      <div className="bg-slate-950 border border-slate-800 rounded-xl overflow-hidden shadow-sm">
        <div className="flex items-center justify-between px-4 py-2.5 bg-slate-900/90 border-b border-slate-800 text-xs">
          <div className="flex items-center gap-2 text-slate-400 font-mono text-[11px]">
            <Code2 className="w-3.5 h-3.5 text-indigo-400" />
            <span>
              {endpoint} · {lang}
            </span>
          </div>

          <div className="flex items-center gap-2">
            <button
              onClick={handleTryIt}
              disabled={testing}
              className="flex items-center gap-1.5 px-3 py-1 bg-emerald-600/15 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-600/25 rounded-md font-semibold text-[11px] transition-all"
            >
              <Play className="w-3 h-3 fill-current" />
              <span>{testing ? 'Executing...' : 'Try It (Admin Session)'}</span>
            </button>

            <button
              onClick={handleCopy}
              className="flex items-center gap-1.5 px-3 py-1 bg-slate-800 hover:bg-slate-700 text-slate-200 rounded-md font-medium text-[11px] transition-all"
            >
              {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
              <span>{copied ? 'Copied!' : 'Copy Code'}</span>
            </button>
          </div>
        </div>

        <pre className="p-4 text-xs font-mono text-slate-200 overflow-x-auto leading-relaxed bg-slate-950/60">
          <code>{renderSnippet()}</code>
        </pre>
      </div>

      {/* Try It Output Box */}
      {testOutput && (
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-4 space-y-2 text-xs">
          <div className="flex items-center justify-between text-slate-400 font-semibold">
            <span>Execution Response</span>
            <button onClick={() => setTestOutput(null)} className="text-[10px] text-slate-500 hover:text-slate-300">
              Clear
            </button>
          </div>
          <pre className="p-3 bg-slate-900 border border-slate-800 rounded-lg font-mono text-slate-300 overflow-x-auto text-[11px]">
            {testOutput}
          </pre>
        </div>
      )}

      {/* Endpoint Specifications & Error Codes Table */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-6 text-xs">
        <div className="bg-slate-950 border border-slate-800 rounded-xl p-5 space-y-3">
          <h3 className="font-semibold text-slate-200 text-sm">Response Specs & Encodings</h3>
          <ul className="space-y-2 text-slate-400">
            <li className="flex justify-between py-1 border-b border-slate-900">
              <span>TTS Stream Audio Format:</span>
              <span className="text-slate-200 font-mono">24,000 Hz, 16-bit mono PCM</span>
            </li>
            <li className="flex justify-between py-1 border-b border-slate-900">
              <span>STT Input Format:</span>
              <span className="text-slate-200 font-mono">16,000 Hz mono WAV recommended</span>
            </li>
            <li className="flex justify-between py-1 border-b border-slate-900">
              <span>Chat Protocol:</span>
              <span className="text-slate-200 font-mono">Server-Sent Events (SSE) data: [DONE]</span>
            </li>
            <li className="flex justify-between py-1">
              <span>Gateway Port:</span>
              <span className="text-indigo-400 font-mono">8000 (Internal engines unreachable)</span>
            </li>
          </ul>
        </div>

        <div className="bg-slate-950 border border-slate-800 rounded-xl p-5 space-y-3">
          <h3 className="font-semibold text-slate-200 text-sm">Common Error Codes</h3>
          <div className="space-y-1.5 text-slate-400 font-mono text-[11px]">
            <div className="flex items-center justify-between">
              <span className="text-amber-400">401 UNAUTHORIZED</span>
              <span className="font-sans text-slate-400 text-[10px]">Missing, invalid, or expired key</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-amber-400">403 FORBIDDEN</span>
              <span className="font-sans text-slate-400 text-[10px]">Model not in key allowlist or missing scope</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-amber-400">429 QUOTA_EXCEEDED</span>
              <span className="font-sans text-slate-400 text-[10px]">Rate limit or daily quota cap reached</span>
            </div>
            <div className="flex items-center justify-between">
              <span className="text-rose-400">503 SERVICE_UNAVAILABLE</span>
              <span className="font-sans text-slate-400 text-[10px]">Backend concurrency queue full</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};
