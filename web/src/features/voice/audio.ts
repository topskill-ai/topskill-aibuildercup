/**
 * Browser audio for voice mode (SPEC §18.1):
 * - `MicCapture`: getUserMedia (echo cancellation on, so the agent's voice does not barge in on itself)
 *   → AudioWorklet that decimates to 16 kHz PCM16 in 40 ms frames;
 * - `PcmPlayer`: schedules 24 kHz PCM16 chunks back to back and stops everything at once on barge-in.
 * One AudioContext (created on the click that starts the call, so autoplay rules allow playback).
 */
import { createDecimator } from "./pcm";

export const FRAME_SAMPLES = 640; // 40 ms at 16 kHz

const WORKLET_NAME = "topskill-pcm-capture";

function workletSource(outRate: number): string {
  return `
const createDecimator = (${createDecimator.toString()});
class PcmCapture extends AudioWorkletProcessor {
  constructor() {
    super();
    this.push = createDecimator(sampleRate, ${outRate}, ${FRAME_SAMPLES});
  }
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) {
      for (const frame of this.push(channel)) this.port.postMessage(frame.buffer, [frame.buffer]);
    }
    return true;
  }
}
registerProcessor("${WORKLET_NAME}", PcmCapture);
`;
}

export type MicErrorKind = "denied" | "unavailable" | "insecure";

export class MicError extends Error {
  readonly kind: MicErrorKind;

  constructor(kind: MicErrorKind, message: string) {
    super(message);
    this.name = "MicError";
    this.kind = kind;
  }
}

/** Maps getUserMedia failures to the three cases the UI explains. */
export function micErrorFrom(err: unknown): MicError {
  const name = err instanceof DOMException || err instanceof Error ? err.name : "";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "PermissionDeniedError") {
    return new MicError("denied", "Microphone access is blocked. Allow it for this site and try again.");
  }
  return new MicError("unavailable", "No microphone could be opened. Check that one is connected and not in use.");
}

export function createAudioContext(): AudioContext {
  const Ctor = window.AudioContext ?? (window as unknown as { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
  if (!Ctor) throw new MicError("unavailable", "This browser cannot play or record audio.");
  return new Ctor({ latencyHint: "interactive" });
}

export class MicCapture {
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private sink: GainNode | null = null;
  private url: string | null = null;
  muted = false;
  private readonly ctx: AudioContext;
  private readonly onFrame: (frame: Int16Array) => void;

  constructor(ctx: AudioContext, onFrame: (frame: Int16Array) => void) {
    this.ctx = ctx;
    this.onFrame = onFrame;
  }

  async start(outRate: number): Promise<void> {
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw new MicError("insecure", "Voice needs a secure (https) page and a browser with microphone support.");
    }
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
      });
    } catch (err) {
      throw micErrorFrom(err);
    }
    this.url = URL.createObjectURL(new Blob([workletSource(outRate)], { type: "application/javascript" }));
    await this.ctx.audioWorklet.addModule(this.url);
    this.source = this.ctx.createMediaStreamSource(this.stream);
    this.node = new AudioWorkletNode(this.ctx, WORKLET_NAME, { numberOfInputs: 1, numberOfOutputs: 1 });
    // Keep the worklet in the rendered graph (some engines skip nodes that reach no destination), muted.
    this.sink = this.ctx.createGain();
    this.sink.gain.value = 0;
    this.node.connect(this.sink).connect(this.ctx.destination);
    this.node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
      const frame = new Int16Array(e.data);
      // Muted: keep the stream going with silence, so the server's voice activity detection can close a
      // half-spoken turn instead of waiting for more audio.
      this.onFrame(this.muted ? new Int16Array(frame.length) : frame);
    };
    this.source.connect(this.node);
  }

  stop(): void {
    this.node?.port.close();
    this.source?.disconnect();
    this.node?.disconnect();
    this.sink?.disconnect();
    for (const track of this.stream?.getTracks() ?? []) track.stop();
    if (this.url) URL.revokeObjectURL(this.url);
    this.stream = null;
    this.node = null;
    this.source = null;
    this.sink = null;
    this.url = null;
  }
}

export class PcmPlayer {
  private next = 0;
  private readonly sources = new Set<AudioBufferSourceNode>();
  private readonly ctx: AudioContext;
  private readonly rate: number;
  private readonly onActive: (active: boolean) => void;

  constructor(ctx: AudioContext, rate: number, onActive: (active: boolean) => void) {
    this.ctx = ctx;
    this.rate = rate;
    this.onActive = onActive;
  }

  get active(): boolean {
    return this.sources.size > 0;
  }

  play(samples: Float32Array): void {
    if (samples.length === 0) return;
    const buffer = this.ctx.createBuffer(1, samples.length, this.rate);
    buffer.getChannelData(0).set(samples);
    const src = this.ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.ctx.destination);
    const at = Math.max(this.ctx.currentTime + 0.02, this.next);
    src.start(at);
    this.next = at + buffer.duration;
    const wasActive = this.active;
    this.sources.add(src);
    src.onended = () => {
      this.sources.delete(src);
      if (!this.active) this.onActive(false);
    };
    if (!wasActive) this.onActive(true);
  }

  /** Barge-in or hang-up: silence now and forget everything queued. */
  flush(): void {
    for (const src of this.sources) {
      src.onended = null;
      try {
        src.stop();
      } catch {
        // already stopped
      }
    }
    const wasActive = this.active;
    this.sources.clear();
    this.next = 0;
    if (wasActive) this.onActive(false);
  }
}
