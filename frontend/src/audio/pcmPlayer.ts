/**
 * Streaming PCM Audio Player using Web Audio API
 * Specifically tuned for signed 16-bit, 24kHz mono PCM output from local-tts.
 *
 * B8 additions:
 * - Buffer source nodes are routed through an AnalyserNode before reaching
 *   audioCtx.destination: source -> analyserNode -> destination
 * - getPlaybackAudioLevel(): returns current RMS level [0.0 - 1.0] for orb animation
 * - getAnalyserNode(): exposes the AnalyserNode for Canvas 2D visualizer tap (R5)
 * - stop() interrupts and disconnects all active sources in <15ms
 */

export class StreamingPCMPlayer {
  private audioCtx: AudioContext | null = null;
  private analyserNode: AnalyserNode | null = null;
  private analyserBuffer: Uint8Array<ArrayBuffer> | null = null;
  private nextPlayTime: number = 0;
  private isPlaying: boolean = false;
  private readonly sampleRate: number = 24000;
  private activeSourceNodes: AudioBufferSourceNode[] = [];

  constructor(sampleRate: number = 24000) {
    this.sampleRate = sampleRate;
  }

  private initContext() {
    if (!this.audioCtx) {
      const AudioCtxClass =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      this.audioCtx = new AudioCtxClass({ sampleRate: this.sampleRate });

      // Create a shared AnalyserNode for the entire playback graph.
      // All buffer sources connect through this node before reaching destination,
      // enabling real-time level metering for the Canvas 2D Orb (R5).
      this.analyserNode = this.audioCtx.createAnalyser();
      this.analyserNode.fftSize = 256; // 128-bin frequency resolution, low CPU cost
      this.analyserNode.smoothingTimeConstant = 0.6; // Moderate smoothing for fluid orb response

      // Connect analyser -> destination (always live; sources feed into analyser)
      this.analyserNode.connect(this.audioCtx.destination);

      // Allocate reusable time-domain data buffer
      this.analyserBuffer = new Uint8Array(this.analyserNode.frequencyBinCount) as Uint8Array<ArrayBuffer>;
    }
    if (this.audioCtx.state === 'suspended') {
      this.audioCtx.resume();
    }
  }

  /**
   * Feed raw binary PCM chunk (ArrayBuffer or Uint8Array).
   * The source is connected through analyserNode -> destination for audio tap.
   */
  public feed(chunk: ArrayBuffer | Uint8Array) {
    this.initContext();
    if (!this.audioCtx || !this.analyserNode) return;

    const buffer = chunk instanceof Uint8Array ? chunk.buffer : chunk;
    const int16Array = new Int16Array(buffer);
    const numSamples = int16Array.length;
    if (numSamples === 0) return;

    // Convert 16-bit signed PCM to 32-bit float [-1.0, 1.0]
    const float32Array = new Float32Array(numSamples);
    for (let i = 0; i < numSamples; i++) {
      float32Array[i] = int16Array[i] / 32768.0;
    }

    const audioBuffer = this.audioCtx.createBuffer(1, numSamples, this.sampleRate);
    audioBuffer.getChannelData(0).set(float32Array);

    const source = this.audioCtx.createBufferSource();
    source.buffer = audioBuffer;

    // B8: Route source -> analyserNode -> destination (not directly to destination)
    source.connect(this.analyserNode);

    const now = this.audioCtx.currentTime;
    // Add small buffer jitter margin (25ms) on cold start
    if (this.nextPlayTime < now) {
      this.nextPlayTime = now + 0.025;
    }

    source.start(this.nextPlayTime);
    this.activeSourceNodes.push(source);
    this.isPlaying = true;

    this.nextPlayTime += audioBuffer.duration;

    source.onended = () => {
      const idx = this.activeSourceNodes.indexOf(source);
      if (idx !== -1) {
        this.activeSourceNodes.splice(idx, 1);
      }
      if (this.activeSourceNodes.length === 0) {
        this.isPlaying = false;
      }
    };
  }

  /**
   * Immediately stops all active and queued audio playback (on turn.cancel).
   * Guarantees silence within <15ms by calling source.stop() synchronously
   * and disconnecting each node from the graph.
   */
  public stop() {
    for (const source of this.activeSourceNodes) {
      try {
        source.stop();
        source.disconnect();
      } catch {
        // Node already stopped or disconnected — safe to ignore
      }
    }
    this.activeSourceNodes = [];
    if (this.audioCtx) {
      this.nextPlayTime = this.audioCtx.currentTime;
    }
    this.isPlaying = false;
  }

  /**
   * Returns the current RMS playback audio level in the range [0.0, 1.0].
   * Used by the Canvas 2D Orb visualizer (R5) and the BrowserVAD barge-in
   * monitor to determine whether audio is actively playing.
   *
   * Returns 0.0 when no AudioContext exists or no audio is playing.
   */
  public getPlaybackAudioLevel(): number {
    if (!this.analyserNode || !this.analyserBuffer) return 0.0;

    this.analyserNode.getByteTimeDomainData(this.analyserBuffer);

    let sumSquares = 0.0;
    for (let i = 0; i < this.analyserBuffer.length; i++) {
      // Convert uint8 [0,255] to float [-1, 1]
      const sample = (this.analyserBuffer[i] - 128) / 128.0;
      sumSquares += sample * sample;
    }
    const rms = Math.sqrt(sumSquares / this.analyserBuffer.length);
    // Clamp to [0, 1] — RMS is already ≤ 1.0 for normalised audio
    return Math.min(1.0, rms);
  }

  /**
   * Returns the shared AnalyserNode for direct use by the Canvas 2D Orb
   * visualizer component (G-UI / U3). Returns null before the first audio chunk
   * is fed (AudioContext not yet created).
   *
   * The returned node has: fftSize=256, smoothingTimeConstant=0.6.
   * Connect a requestAnimationFrame loop calling getByteTimeDomainData() or
   * getByteFrequencyData() on this node to drive the orb animation.
   */
  public getAnalyserNode(): AnalyserNode | null {
    return this.analyserNode;
  }

  public get running(): boolean {
    return this.isPlaying;
  }
}
