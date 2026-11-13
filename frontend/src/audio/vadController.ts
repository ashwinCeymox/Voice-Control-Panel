/**
 * Universal VAD Controller
 * Supports neural Silero VAD (`@ricky0123/vad-web`) with offline local ONNX assets,
 * with seamless fallback to legacy Web Audio AnalyserNode RMS gate.
 */
import { MicVAD, type RealTimeVADOptions } from '@ricky0123/vad-web';
import { BrowserVAD } from './vad';

export interface VadControllerConfig {
  engine?: 'silero' | 'legacy';
  model?: 'v5' | 'v6' | 'legacy';
  positiveSpeechThreshold?: number;
  negativeSpeechThreshold?: number;
  redemptionMs?: number;
  preSpeechPadMs?: number;
  minSpeechMs?: number;
  minRms?: number;
  bargeIn?: boolean;
  bargeInConfirmMs?: number;
  playbackThresholdBoost?: number;
  audioMode?: 'laptop_speakers' | 'headphones';
}

export interface VadControllerCallbacks {
  onSpeechStart?: () => void;
  onSpeechRealStart?: () => void;
  onSpeechEnd?: (wavBytes: Uint8Array, durationMs: number, rms: number) => void;
  onAudioFrame?: (pcmChunk: Uint8Array) => void;
  onVADMisfire?: () => void;
  onBargeIn?: () => void;
  onFrameProcessed?: (prob: number) => void;
  onError?: (err: Error | string) => void;
}

/**
 * Convert 16kHz Float32Array audio samples into 16-bit little-endian PCM bytes.
 */
export function float32ToPCM16(samples: Float32Array): Uint8Array {
  const buffer = new ArrayBuffer(samples.length * 2);
  const view = new DataView(buffer);
  let offset = 0;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }
  return new Uint8Array(buffer);
}

/**
 * Encode 16kHz mono Float32Array samples into standard 16-bit PCM WAV.
 */
export function encodeWAV(samples: Float32Array, sampleRate = 16000): Uint8Array {
  const numChannels = 1;
  const bytesPerSample = 2;
  const blockAlign = numChannels * bytesPerSample;
  const byteRate = sampleRate * blockAlign;
  const dataSize = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataSize);
  const view = new DataView(buffer);

  // RIFF Chunk
  view.setUint8(0, 0x52); // 'R'
  view.setUint8(1, 0x49); // 'I'
  view.setUint8(2, 0x46); // 'F'
  view.setUint8(3, 0x46); // 'F'
  view.setUint32(4, 36 + dataSize, true);
  view.setUint8(8, 0x57);  // 'W'
  view.setUint8(9, 0x41);  // 'A'
  view.setUint8(10, 0x56); // 'V'
  view.setUint8(11, 0x45); // 'E'

  // fmt Subchunk
  view.setUint8(12, 0x66); // 'f'
  view.setUint8(13, 0x6d); // 'm'
  view.setUint8(14, 0x74); // 't'
  view.setUint8(15, 0x20); // ' '
  view.setUint32(16, 16, true); // Subchunk1Size (16 for PCM)
  view.setUint16(20, 1, true);  // AudioFormat (1 for PCM)
  view.setUint16(22, numChannels, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, byteRate, true);
  view.setUint16(32, blockAlign, true);
  view.setUint16(34, 16, true); // BitsPerSample

  // data Subchunk
  view.setUint8(36, 0x64); // 'd'
  view.setUint8(37, 0x61); // 'a'
  view.setUint8(38, 0x74); // 't'
  view.setUint8(39, 0x61); // 'a'
  view.setUint32(40, dataSize, true);

  // Write PCM samples
  let offset = 44;
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    offset += 2;
  }

  return new Uint8Array(buffer);
}

export function calculateRMS(samples: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < samples.length; i++) {
    sum += samples[i] * samples[i];
  }
  return Math.sqrt(sum / samples.length);
}

export class VadController {
  private config: Required<VadControllerConfig>;
  private callbacks: VadControllerCallbacks;

  private micVad: MicVAD | null = null;
  private legacyVad: BrowserVAD | null = null;
  private isPlayingBack = false;
  private isSpeaking = false;
  private bargeInTimer: ReturnType<typeof setTimeout> | null = null;
  private basePositiveThreshold: number;
  private preSpeechRingBuffer: Uint8Array[] = [];
  private readonly maxPreSpeechFrames = 4;
  private hasFlushedPreSpeech = false;

  constructor(callbacks: VadControllerCallbacks, config: VadControllerConfig = {}) {
    this.callbacks = callbacks;
    this.config = {
      engine: config.engine ?? 'legacy', // Default 'legacy' until P7 cutover
      model: config.model ?? 'v5',
      positiveSpeechThreshold: config.positiveSpeechThreshold ?? 0.45,
      negativeSpeechThreshold: config.negativeSpeechThreshold ?? 0.30,
      redemptionMs: config.redemptionMs ?? 800,
      preSpeechPadMs: config.preSpeechPadMs ?? 200,
      minSpeechMs: config.minSpeechMs ?? 250,
      minRms: config.minRms ?? 0.015,
      bargeIn: config.bargeIn ?? true,
      bargeInConfirmMs: config.bargeInConfirmMs ?? 250,
      playbackThresholdBoost: config.playbackThresholdBoost ?? 0.15,
      audioMode: config.audioMode ?? 'laptop_speakers',
    };
    this.basePositiveThreshold = this.config.positiveSpeechThreshold;
  }

  public get engine(): 'silero' | 'legacy' {
    return this.config.engine;
  }

  public async start(audioContext?: AudioContext, sourceNode?: MediaStreamAudioSourceNode | AudioNode): Promise<void> {
    if (this.config.engine === 'silero') {
      try {
        const vad = await MicVAD.new({
          model: this.config.model,
          baseAssetPath: '/vad/',
          onnxWASMBasePath: '/vad/',
          positiveSpeechThreshold: this.config.positiveSpeechThreshold,
          negativeSpeechThreshold: this.config.negativeSpeechThreshold,
          redemptionMs: this.config.redemptionMs,
          preSpeechPadMs: this.config.preSpeechPadMs,
          minSpeechMs: this.config.minSpeechMs,
          startOnLoad: true,
          ortConfig: (ort: any) => {
            ort.env.logLevel = 'error';
            if (ort.env.wasm) {
              ort.env.wasm.numThreads = 1;
              ort.env.wasm.simd = true;
            }
          },
          onFrameProcessed: (probs: any, frame?: Float32Array) => {
            const prob = probs.isSpeech ?? (probs as any)[1] ?? 0;
            this.callbacks.onFrameProcessed?.(prob);

            if (frame && frame.length > 0) {
              const pcm = float32ToPCM16(frame);
              if (this.isSpeaking) {
                if (!this.hasFlushedPreSpeech) {
                  for (const chunk of this.preSpeechRingBuffer) {
                    this.callbacks.onAudioFrame?.(chunk);
                  }
                  this.preSpeechRingBuffer = [];
                  this.hasFlushedPreSpeech = true;
                }
                this.callbacks.onAudioFrame?.(pcm);
              } else {
                this.preSpeechRingBuffer.push(pcm);
                if (this.preSpeechRingBuffer.length > this.maxPreSpeechFrames) {
                  this.preSpeechRingBuffer.shift();
                }
              }
            }
          },
          onSpeechStart: () => {
            this.isSpeaking = true;
            this.callbacks.onSpeechStart?.();

            if (!this.hasFlushedPreSpeech) {
              for (const chunk of this.preSpeechRingBuffer) {
                this.callbacks.onAudioFrame?.(chunk);
              }
              this.preSpeechRingBuffer = [];
              this.hasFlushedPreSpeech = true;
            }

            // If assistant is actively speaking, start barge-in confirmation timer
            if (this.isPlayingBack && this.config.bargeIn) {
              if (this.bargeInTimer) clearTimeout(this.bargeInTimer);
              this.bargeInTimer = setTimeout(() => {
                if (this.isSpeaking && this.isPlayingBack) {
                  this.callbacks.onBargeIn?.();
                }
              }, this.config.bargeInConfirmMs);
            }
          },
          onSpeechRealStart: () => {
            this.callbacks.onSpeechRealStart?.();
          },
          onVADMisfire: () => {
            this.isSpeaking = false;
            this.hasFlushedPreSpeech = false;
            this.preSpeechRingBuffer = [];
            if (this.bargeInTimer) {
              clearTimeout(this.bargeInTimer);
              this.bargeInTimer = null;
            }
            this.callbacks.onVADMisfire?.();
          },
          onSpeechEnd: (audio: Float32Array) => {
            this.isSpeaking = false;
            this.hasFlushedPreSpeech = false;
            this.preSpeechRingBuffer = [];
            if (this.bargeInTimer) {
              clearTimeout(this.bargeInTimer);
              this.bargeInTimer = null;
            }

            const durationMs = Math.round((audio.length / 16000) * 1000);
            const rms = calculateRMS(audio);

            // Volume Gate: Drop segment if below minRms
            if (rms < this.config.minRms) {
              this.callbacks.onVADMisfire?.();
              return;
            }

            const wavBytes = encodeWAV(audio, 16000);
            this.callbacks.onSpeechEnd?.(wavBytes, durationMs, rms);
          },
        } as Partial<RealTimeVADOptions>);

        this.micVad = vad;
      } catch (err: any) {
        console.error('Silero VAD init error, falling back to legacy VAD:', err);
        this.callbacks.onError?.(err.message || 'Silero VAD initialization failed');
        // Fallback to legacy
        if (audioContext && sourceNode) {
          this._initLegacy(audioContext, sourceNode);
        }
      }
    } else {
      if (audioContext && sourceNode) {
        this._initLegacy(audioContext, sourceNode);
      }
    }
  }

  private _initLegacy(audioContext: AudioContext, sourceNode: MediaStreamAudioSourceNode | AudioNode): void {
    this.legacyVad = new BrowserVAD(
      audioContext,
      sourceNode,
      {
        onSpeechStart: () => this.callbacks.onSpeechStart?.(),
        onSpeechEnd: () => this.callbacks.onSpeechEnd?.(new Uint8Array(0), 0, 0),
        onBargeIn: () => this.callbacks.onBargeIn?.(),
      },
      {
        silenceMs: this.config.redemptionMs,
        idleThreshold: 0.045,
        bargeInThreshold: this.config.audioMode === 'headphones' ? 0.045 : 0.065,
        bargeInHoldMs: 100,
      }
    );
    this.legacyVad.start();
  }

  public setPlaybackState(isPlaying: boolean): void {
    this.isPlayingBack = isPlaying;
    if (this.legacyVad) {
      this.legacyVad.setPlaybackState(isPlaying);
    }
    if (this.micVad) {
      if (isPlaying) {
        // Boost threshold during assistant speech to prevent laptop speaker bleed
        const boosted = Math.min(0.90, this.basePositiveThreshold + this.config.playbackThresholdBoost);
        this.micVad.setOptions({
          positiveSpeechThreshold: boosted,
        });
      } else {
        // Restore normal threshold
        if (this.bargeInTimer) {
          clearTimeout(this.bargeInTimer);
          this.bargeInTimer = null;
        }
        this.micVad.setOptions({
          positiveSpeechThreshold: this.basePositiveThreshold,
        });
      }
    }
  }

  public setOptions(update: Partial<VadControllerConfig>): void {
    this.config = { ...this.config, ...update };
    if (update.positiveSpeechThreshold !== undefined) {
      this.basePositiveThreshold = update.positiveSpeechThreshold;
    }
    if (this.micVad) {
      this.micVad.setOptions({
        positiveSpeechThreshold: this.config.positiveSpeechThreshold,
        negativeSpeechThreshold: this.config.negativeSpeechThreshold,
        redemptionMs: this.config.redemptionMs,
        preSpeechPadMs: this.config.preSpeechPadMs,
        minSpeechMs: this.config.minSpeechMs,
      });
    }
    if (this.legacyVad) {
      this.legacyVad.updateConfig({
        silenceMs: this.config.redemptionMs,
        bargeInThreshold: this.config.audioMode === 'headphones' ? 0.045 : 0.065,
      });
    }
  }

  public async pause(): Promise<void> {
    if (this.micVad) await this.micVad.pause();
    if (this.legacyVad) this.legacyVad.stop();
  }

  public async destroy(): Promise<void> {
    if (this.bargeInTimer) {
      clearTimeout(this.bargeInTimer);
      this.bargeInTimer = null;
    }
    if (this.micVad) {
      try {
        await this.micVad.destroy();
      } catch {}
      this.micVad = null;
    }
    if (this.legacyVad) {
      this.legacyVad.stop();
      this.legacyVad = null;
    }
    this.isSpeaking = false;
    this.isPlayingBack = false;
  }
}
