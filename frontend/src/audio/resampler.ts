/**
 * Real-time Audio Resampler and Chunk Buffer.
 *
 * Ensures live microphone audio from macOS hardware (default 44.1kHz or 48kHz)
 * is cleanly and accurately resampled to 16kHz, 16-bit, mono PCM before reaching
 * the ASR inference engine, eliminating garbled pitch-shifted audio.
 */

export class AudioResampler {
  public readonly inputSampleRate: number;
  public readonly targetSampleRate: number;
  private readonly ratio: number;
  private lastSample: number = 0;
  private fraction: number = 0;

  constructor(inputSampleRate: number, targetSampleRate = 16000) {
    this.inputSampleRate = Math.max(8000, Math.round(inputSampleRate));
    this.targetSampleRate = Math.max(8000, Math.round(targetSampleRate));
    this.ratio = this.inputSampleRate / this.targetSampleRate;
  }

  /**
   * Resample an input buffer of Float32Array samples down/up to targetSampleRate (16kHz).
   * Maintains fractional phase across chunk boundaries to avoid audio artifacts.
   */
  public resample(input: Float32Array): Float32Array {
    if (this.inputSampleRate === this.targetSampleRate || input.length === 0) {
      return input;
    }

    const outLength = Math.floor((input.length - this.fraction) / this.ratio);
    if (outLength <= 0) {
      // Input too small for even one output sample, save fraction
      this.fraction -= input.length;
      return new Float32Array(0);
    }

    const output = new Float32Array(outLength);
    let inIndex = 0;
    let outIndex = 0;

    while (outIndex < outLength) {
      const nextIndex = inIndex + this.fraction;
      const indexFloor = Math.floor(nextIndex);
      const frac = nextIndex - indexFloor;

      const s0 =
        indexFloor === 0 && inIndex === 0
          ? this.lastSample
          : (input[indexFloor - 1] ?? input[0] ?? 0);
      const s1 = input[indexFloor] ?? s0;

      output[outIndex++] = s0 + frac * (s1 - s0);
      inIndex += this.ratio;
    }

    this.lastSample = input[input.length - 1] ?? 0;
    this.fraction = inIndex - input.length;
    return output;
  }

  public reset(): void {
    this.lastSample = 0;
    this.fraction = 0;
  }
}

/**
 * Accumulates 16kHz audio samples into optimal context windows (e.g. 560ms–1120ms)
 * to provide sufficient temporal lookahead context for the acoustic encoder.
 */
export class StreamingAudioChunker {
  private readonly targetChunkSamples: number;
  private buffer: Float32Array;
  private bufferLength: number = 0;

  constructor(sampleRate = 16000, chunkDurationMs = 560) {
    this.targetChunkSamples = Math.round((sampleRate * chunkDurationMs) / 1000);
    // Allocate 4x chunk capacity to avoid repeated reallocation
    this.buffer = new Float32Array(this.targetChunkSamples * 4);
  }

  /**
   * Push newly arrived 16kHz samples. Returns an array of ready chunks
   * (each exactly targetChunkSamples long).
   */
  public push(samples: Float32Array): Float32Array[] {
    if (samples.length === 0) return [];

    // Ensure capacity
    const needed = this.bufferLength + samples.length;
    if (needed > this.buffer.length) {
      const newCap = Math.max(needed, this.buffer.length * 2);
      const nextBuf = new Float32Array(newCap);
      nextBuf.set(this.buffer.subarray(0, this.bufferLength));
      this.buffer = nextBuf;
    }

    this.buffer.set(samples, this.bufferLength);
    this.bufferLength += samples.length;

    const chunks: Float32Array[] = [];
    while (this.bufferLength >= this.targetChunkSamples) {
      const chunk = new Float32Array(
        this.buffer.subarray(0, this.targetChunkSamples)
      );
      chunks.push(chunk);

      // Shift remaining samples
      this.buffer.copyWithin(0, this.targetChunkSamples, this.bufferLength);
      this.bufferLength -= this.targetChunkSamples;
    }

    return chunks;
  }

  /**
   * Flush any remaining samples in the buffer upon end of speech / stream stop.
   */
  public flush(): Float32Array | null {
    if (this.bufferLength === 0) return null;
    const remaining = new Float32Array(this.buffer.subarray(0, this.bufferLength));
    this.bufferLength = 0;
    return remaining;
  }

  public reset(): void {
    this.bufferLength = 0;
  }
}
