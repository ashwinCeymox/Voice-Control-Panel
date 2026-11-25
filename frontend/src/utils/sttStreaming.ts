/** Shared UI classification helpers for STT native streaming models (Sherpa + Parakeet Native). */

export type StreamingMode = 'native' | 'none';

export interface StreamingCapabilities {
  isNative: boolean;
  streamingMode: StreamingMode;
  streamingBackend: string | null;
  isStreamingDisabled: boolean;
  labelPrefix: string;
}

export function resolveSttStreamingUi(model: any, selectedMode?: string): StreamingCapabilities {
  const idLower = (model?.id || '').toLowerCase();
  const transports: string[] = Array.isArray(model?.transports) ? model.transports : [];
  const backend: string | null = model?.streaming_backend || null;

  const isNative =
    model?.streaming_mode === 'native' ||
    model?.engine === 'streaming-stt' ||
    model?.provider === 'stream-stt' ||
    model?.provider === 'streaming-stt' ||
    idLower.includes('sherpa') ||
    transports.includes('ws_stream_native');

  const isStreamingKind = selectedMode === 'streaming' || model?.kind === 'streaming';
  const isStreamingDisabled = Boolean(
    model &&
      (model.available === false || (isStreamingKind && !isNative))
  );

  let labelPrefix = '';
  if (isStreamingKind) {
    labelPrefix = isNative ? 'Live (native) • ' : '';
  }

  return {
    isNative,
    streamingMode: isNative ? 'native' : 'none',
    streamingBackend: backend || (isNative ? (idLower.includes('sherpa') ? 'sherpa' : 'parakeet_native') : null),
    isStreamingDisabled,
    labelPrefix,
  };
}
