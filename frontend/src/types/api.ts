/**
 * TypeScript API contracts matching docs/contracts/openapi.yaml, ws-events.md, and app/schemas/
 */

export interface AudioFormat {
  sample_rate: number;
  sample_format: string; // 's16le'
  channels: number;      // 1
}

export interface InputAudioConfig {
  container: 'webm' | 'wav' | 'ogg';
  codec: 'opus' | 'pcm';
  sample_rate?: number;
}

export interface TranscriptResponse {
  text: string;
  language?: string;
  duration?: number;
  model?: string;
  model_used?: string;
  runtime?: string;
  fallback?: boolean;
}

export interface ChatMessage {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface ChatCompletionRequest {
  model?: string;
  messages: ChatMessage[];
  stream?: boolean;
  temperature?: number;
  max_tokens?: number;
}

export interface ChatCompletionResponse {
  id: string;
  object: string;
  created: number;
  model: string;
  choices: Array<{
    index: number;
    message: {
      role: string;
      content: string;
    };
    finish_reason: string;
  }>;
}

export interface VoiceSettings {
  speed: number;
}

export interface Voice {
  voice_id: string;
  name: string;
  gender?: 'male' | 'female' | 'unknown';
  language?: string;
  engine?: string;
  source?: 'metadata' | 'inferred' | 'manual';
  category?: string;
  settings?: VoiceSettings;
}

export interface VoiceListResponse {
  voices: Voice[];
}

export interface ModelInfo {
  id: string;
  name?: string;
  object: string;
  owned_by: string;
  type?: 'llm' | 'stt' | 'tts';
  backend?: string;
  size_mb?: number;
  installed?: boolean;
}

export interface ModelListResponse {
  object: string;
  data: ModelInfo[];
}

export interface TTSModelListResponse {
  models: Array<{ id: string; name: string }>;
}

export interface TTSRequest {
  text: string;
  model_id?: string;
  voice_id?: string;
  voice_settings?: VoiceSettings;
}

export interface VoiceTurnMetrics {
  stt_ms?: number;
  llm_ms?: number;
  tts_ms?: number;
  total_ms?: number;
}

export interface VoiceTurnResponse {
  turn_id: string;
  session_id: string;
  request_id: string;
  transcript: string;
  response_text: string;
  audio_base64: string;
  audio_format: AudioFormat;
  metrics?: VoiceTurnMetrics;
}

export interface TicketResponse {
  ticket: string;
  expires_in: number;
}

export interface ProviderStatus {
  reachable: boolean;
  url?: string;
  configured?: boolean;
  default_llm_model?: string;
  default_stt_model?: string;
  default_model?: string;
  default_voice_id?: string;
}

export interface ReadyResponse {
  status: 'ready' | 'degraded';
  providers: {
    localai: ProviderStatus;
    localtts: ProviderStatus;
  };
}

// ============================================================================
// Agent Configurations
// ============================================================================

export interface LLMConfig {
  model: string;
  temperature: number;
  max_tokens: number;
}

export interface STTConfig {
  model: string;
  kind?: 'batch' | 'streaming';
  streaming_mode?: 'native' | 'simulated' | 'none';
  transports?: string[];
  fallback_model?: string | null;
  language: string;
}

export interface TTSConfig {
  engine: string;
  voice_id: string;
  speed: number;
}

export interface VADConfig {
  engine?: 'silero' | 'legacy';
  positive_speech_threshold?: number;
  negative_speech_threshold?: number;
  redemption_ms?: number;
  pre_speech_pad_ms?: number;
  min_speech_ms?: number;
  min_rms?: number;
  noise_suppression?: string;
  barge_in?: boolean;
  barge_in_confirm_ms?: number;
  playback_threshold_boost?: number;
  mode?: 'energy' | 'silero' | 'push_to_talk';
  threshold?: number;
  min_silence_ms?: number;
}

export interface AudioConfig {
  echo_mode: 'laptop_speakers' | 'headphones' | 'half_duplex';
  barge_in: boolean;
}

export interface Agent {
  id: string;
  name: string;
  description?: string;
  system_prompt: string;
  llm: LLMConfig;
  stt: STTConfig;
  tts: TTSConfig;
  vad: VADConfig;
  audio: AudioConfig;
  is_active?: boolean;
  created_at?: string;
  updated_at?: string;
  last_used_at?: string | null;
  schema_version?: number;
}

export interface AgentActiveState {
  status: 'idle' | 'loading' | 'ready' | 'error';
  active_agent_id: string;
  active_agent?: Agent | null;
}

// ============================================================================
// System Resources & Model Gallery
// ============================================================================

export interface SystemResources {
  host_os: string;
  memory: {
    total_mb: number;
    available_mb: number;
    used_mb: number;
    percent_used: number;
  };
  hardware: {
    has_metal_gpu: boolean;
    mps_available: boolean;
    chip?: string;
  };
  disk: {
    free_gb: number;
    total_gb: number;
  };
}

export interface GalleryModel {
  id: string;
  name: string;
  description: string;
  provider: string;        // e.g. "localai" | "local-tts"
  category: 'llm' | 'stt' | 'tts';
  kind?: 'batch' | 'streaming';
  streaming_mode?: 'native' | 'simulated' | 'none';
  transports?: string[];
  size_mb: number;
  ram_required_mb: number;
  license: string;
  quantization?: string;
  tags?: string[];
  installed?: boolean;
  fits_budget: boolean;
  fit_score: number;
}

export interface InstallJob {
  job_id: string;
  model_id: string;
  status: 'queued' | 'downloading' | 'verifying' | 'registering' | 'completed' | 'failed';
  progress_percent: number;      // API field name
  downloaded_bytes?: number;
  total_bytes?: number;
  speed_mbps?: number;
  eta_seconds?: number;
  error_message?: string | null;
  created_at: number;
}

// ============================================================================
// WebSocket Event Types (WS /v1/voice/stream)
// ============================================================================

export type ClientEvent =
  | {
      type: 'session.start';
      session_id?: string | null;
      agent_id?: string | null;
      stt_kind?: 'batch' | 'streaming';
      input_audio: InputAudioConfig;
      config?: {
        echo_mode?: string;
        barge_in?: boolean;
        [key: string]: any;
      };
    }
  | { type: 'audio.commit' }
  | { type: 'turn.cancel'; turn_id?: string }
  | { type: 'session.end' };

export type ServerEvent =
  | { type: 'session.created'; session_id: string; agent_name?: string; agent_id?: string }
  | { type: 'transcript.partial'; turn_id?: string; seq: number; confirmed: string; tentative: string }
  | { type: 'transcript.completed'; turn_id: string; text: string }
  | { type: 'stt.fallback'; turn_id?: string; reason: string }
  | { type: 'response.text.delta'; turn_id: string; delta: string }
  | { type: 'audio.started'; turn_id: string; sample_rate: number; sample_format: string; channels: number }
  | { type: 'audio.completed'; turn_id: string }
  | { type: 'turn.completed'; turn_id: string }
  | { type: 'turn.cancelled'; turn_id: string }
  | { type: 'error'; code: string; message: string; turn_id?: string | null; request_id?: string };
