/**
 * Browser-Side Voice Activity Detection (VAD)
 *
 * Implements Web Audio AnalyserNode energy-gate VAD per Decision R1 & R2:
 *
 * - Speech detection: RMS > 0.045 with 650ms trailing silence before audio.commit
 * - Barge-in during playback: elevated dual-threshold RMS > 0.14 with 240ms hold
 *   to prevent laptop speaker echo from self-triggering barge-in while still
 *   catching deliberate user interruptions.
 *
 * Usage:
 *   const vad = new BrowserVAD(audioContext, mediaStreamSource, {
 *     onSpeechStart: () => setListening(true),
 *     onSpeechEnd: (buffer) => ws.send(JSON.stringify({ type: 'audio.commit' })),
 *     onBargeIn: () => { player.stop(); ws.send(JSON.stringify({ type: 'turn.cancel' })); },
 *   });
 *   vad.start();
 *   // when done:
 *   vad.stop();
 */

/** Callback hooks provided to BrowserVAD. */
export interface VADCallbacks {
  /** Fired when speech energy is first detected above the idle threshold. */
  onSpeechStart?: () => void;

  /**
   * Fired after 650ms of continuous trailing silence following an active speech
   * segment. The caller should emit audio.commit at this point.
   */
  onSpeechEnd?: () => void;

  /**
   * Fired when the assistant is currently playing audio and the user's voice
   * exceeds the elevated barge-in threshold (RMS > 0.14) for at least 240ms.
   * The caller should immediately: pcmPlayer.stop() and send turn.cancel.
   */
  onBargeIn?: () => void;
}

/** Configuration for BrowserVAD (all optional — defaults match R1/R2 decisions). */
export interface VADConfig {
  /**
   * FFT size for the AnalyserNode. Must be a power of 2.
   * Smaller = faster frame eval; default 512 gives ~21ms frames at 24kHz.
   */
  fftSize?: number;

  /**
   * RMS energy threshold to detect speech when the assistant is idle/silent.
   * R1 decision: 0.045
   */
  idleThreshold?: number;

  /**
   * RMS energy threshold to detect barge-in when the assistant is playing audio.
   * R2 decision: 0.14 — high enough to reject laptop speaker echo residual (≈0.080),
   * but low enough to catch a normal conversational voice (≈0.220).
   */
  bargeInThreshold?: number;

  /**
   * Duration (ms) of continuous elevated energy required to confirm a barge-in
   * during playback. R2 decision: 240ms.
   */
  bargeInHoldMs?: number;

  /**
   * Duration (ms) of continuous silence (RMS below idleThreshold) after a speech
   * segment before onSpeechEnd (audio.commit) is fired. R1 decision: 650ms.
   */
  silenceMs?: number;

  /**
   * Polling interval (ms) for the energy gate loop.
   * Default 20ms (50 fps) is sufficient for sub-50ms detection latency.
   */
  pollIntervalMs?: number;
}

/** Internal state of the VAD state machine. */
type VADState = 'idle' | 'speaking' | 'trailing_silence';

export class BrowserVAD {
  private readonly analyser: AnalyserNode;
  private readonly sourceNode: MediaStreamAudioSourceNode | AudioNode;
  private readonly dataBuffer: Uint8Array<ArrayBuffer>;
  private readonly callbacks: VADCallbacks;

  // Config with R1/R2 decision defaults
  private idleThreshold: number;
  private bargeInThreshold: number;
  private bargeInHoldMs: number;
  private silenceMs: number;
  private pollIntervalMs: number;

  // State
  private state: VADState = 'idle';
  private pollTimer: ReturnType<typeof setInterval> | null = null;
  private silenceStartTime: number = 0;
  private bargeInHoldStart: number = 0;
  private bargeInHoldActive: boolean = false;
  private bargeInConsecutiveFrames: number = 0;
  private bargeInDropoutFrames: number = 0;
  private isPlayingBack: boolean = false;
  private bargeInFired: boolean = false;

  constructor(
    audioContext: AudioContext,
    source: MediaStreamAudioSourceNode | AudioNode,
    callbacks: VADCallbacks,
    config: VADConfig = {}
  ) {
    this.callbacks = callbacks;
    this.sourceNode = source;

    // Merge defaults with caller config:
    // idleThreshold: 0.045
    // bargeInThreshold: 0.065 (tuned for responsive interruption with echo cancellation)
    // bargeInHoldMs: 100ms (fast conversational reaction without false triggers)
    this.idleThreshold = config.idleThreshold ?? 0.045;
    this.bargeInThreshold = config.bargeInThreshold ?? 0.065;
    this.bargeInHoldMs = config.bargeInHoldMs ?? 100;
    this.silenceMs = config.silenceMs ?? 650;
    this.pollIntervalMs = config.pollIntervalMs ?? 20;

    const fftSize = config.fftSize ?? 512;

    // Create AnalyserNode — does NOT output sound, purely used for freq data
    this.analyser = audioContext.createAnalyser();
    this.analyser.fftSize = fftSize;
    this.analyser.smoothingTimeConstant = 0; // No smoothing — we want raw frame energy

    // Connect: microphone source -> analyser (analyser is not connected to destination)
    source.connect(this.analyser);

    this.dataBuffer = new Uint8Array(this.analyser.frequencyBinCount) as Uint8Array<ArrayBuffer>;
  }

  /**
   * Dynamically update VAD thresholds and timings on a running instance.
   */
  public updateConfig(config: Partial<VADConfig>): void {
    if (config.idleThreshold !== undefined) this.idleThreshold = config.idleThreshold;
    if (config.bargeInThreshold !== undefined) this.bargeInThreshold = config.bargeInThreshold;
    if (config.bargeInHoldMs !== undefined) this.bargeInHoldMs = config.bargeInHoldMs;
    if (config.silenceMs !== undefined) this.silenceMs = config.silenceMs;
    if (config.pollIntervalMs !== undefined) this.pollIntervalMs = config.pollIntervalMs;
  }

  /**
   * Notify the VAD whether the assistant is currently playing audio back.
   * When true, the elevated barge-in threshold (0.065, 100ms hold) is active.
   * When false, the idle threshold (0.045, 650ms silence) is active.
   */
  public setPlaybackState(isPlaying: boolean): void {
    this.isPlayingBack = isPlaying;
    if (!isPlaying) {
      // Reset barge-in hold state when playback stops
      this.bargeInHoldActive = false;
      this.bargeInHoldStart = 0;
      this.bargeInConsecutiveFrames = 0;
      this.bargeInDropoutFrames = 0;
      this.bargeInFired = false;
    }
  }

  /** Start the VAD polling loop. */
  public start(): void {
    if (this.pollTimer !== null) return; // Already running
    this.state = 'idle';
    this.bargeInHoldActive = false;
    this.bargeInFired = false;

    this.pollTimer = setInterval(() => this._tick(), this.pollIntervalMs);
  }

  /** Stop the VAD and release the AnalyserNode connection. */
  public stop(): void {
    if (this.pollTimer !== null) {
      clearInterval(this.pollTimer);
      this.pollTimer = null;
    }
    try {
      this.sourceNode.disconnect(this.analyser);
    } catch {
      // Source node or analyser may already be disconnected
    }
    try {
      this.analyser.disconnect();
    } catch {
      // May already be disconnected
    }
    this.state = 'idle';
    this.bargeInHoldActive = false;
    this.bargeInFired = false;
  }

  /**
   * Calculate current RMS energy of the microphone signal.
   * Returns a normalised value in [0.0, 1.0].
   */
  private _getRMS(): number {
    this.analyser.getByteTimeDomainData(this.dataBuffer);
    let sumSquares = 0.0;
    for (let i = 0; i < this.dataBuffer.length; i++) {
      // Convert uint8 [0,255] to float [-1, 1]
      const sample = (this.dataBuffer[i] - 128) / 128.0;
      sumSquares += sample * sample;
    }
    return Math.sqrt(sumSquares / this.dataBuffer.length);
  }

  /** Core polling tick — runs every pollIntervalMs. */
  private _tick(): void {
    if (this.pollTimer === null) return;

    let rms = 0;
    try {
      rms = this._getRMS();
    } catch {
      // AudioContext closed or AnalyserNode disconnected
      return;
    }

    const now = Date.now();

    if (this.isPlayingBack) {
      // ----------------------------------------------------------------
      // BARGE-IN mode: responsive dual-threshold detection
      // Triggers when user speech exceeds bargeInThreshold (default 0.065
      // for speakers, 0.045 for headphones) for >= 100ms or 4 consecutive
      // frames, with a 2-frame (40ms) phoneme dip grace window.
      // ----------------------------------------------------------------
      if (rms > this.bargeInThreshold) {
        this.bargeInConsecutiveFrames++;
        this.bargeInDropoutFrames = 0;
        if (!this.bargeInHoldActive) {
          this.bargeInHoldActive = true;
          this.bargeInHoldStart = now;
        }

        const elapsed = now - this.bargeInHoldStart;
        if (!this.bargeInFired && (elapsed >= this.bargeInHoldMs || this.bargeInConsecutiveFrames >= 4)) {
          this.bargeInFired = true;
          this.callbacks.onBargeIn?.();
        }
      } else {
        // Allow up to 2 dropout frames (40ms) of acoustic dip between phonemes without resetting
        this.bargeInDropoutFrames++;
        if (this.bargeInDropoutFrames > 2) {
          this.bargeInHoldActive = false;
          this.bargeInHoldStart = 0;
          this.bargeInConsecutiveFrames = 0;
        }
      }
    } else {
      // ----------------------------------------------------------------
      // IDLE mode: standard speech detection (R1 decision)
      // RMS > 0.045 = speech start; 650ms below threshold = speech end.
      // ----------------------------------------------------------------
      switch (this.state) {
        case 'idle': {
          if (rms > this.idleThreshold) {
            this.state = 'speaking';
            this.callbacks.onSpeechStart?.();
          }
          break;
        }

        case 'speaking': {
          if (rms <= this.idleThreshold) {
            // Energy dropped — start trailing silence timer
            this.state = 'trailing_silence';
            this.silenceStartTime = now;
          }
          // If still above threshold, stay in 'speaking' — no action needed
          break;
        }

        case 'trailing_silence': {
          if (rms > this.idleThreshold) {
            // Speech resumed within silence window — go back to speaking
            this.state = 'speaking';
          } else if ((now - this.silenceStartTime) >= this.silenceMs) {
            // 650ms of silence confirmed — commit the utterance
            this.state = 'idle';
            this.callbacks.onSpeechEnd?.();
          }
          break;
        }
      }
    }
  }
}
