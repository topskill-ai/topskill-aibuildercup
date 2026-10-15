/**
 * PCM helpers for voice mode. `createDecimator` is self-contained on purpose: its source text is also
 * injected into the AudioWorklet (`audio.ts`), so it must not reference anything outside its body.
 */

/** Float samples at `inRate` → PCM16 frames of `frameSize` samples at `outRate` (box-filter decimation). */
export function createDecimator(inRate: number, outRate: number, frameSize: number) {
  const ratio = inRate / outRate;
  let pos = 0;
  let acc = 0;
  let count = 0;
  let frame = new Int16Array(frameSize);
  let filled = 0;
  return function push(samples: Float32Array): Int16Array[] {
    const out: Int16Array[] = [];
    for (let i = 0; i < samples.length; i += 1) {
      acc += samples[i];
      count += 1;
      pos += 1;
      if (pos >= ratio) {
        pos -= ratio;
        const v = Math.max(-1, Math.min(1, acc / count));
        acc = 0;
        count = 0;
        frame[filled] = v < 0 ? Math.round(v * 0x8000) : Math.round(v * 0x7fff);
        filled += 1;
        if (filled === frameSize) {
          out.push(frame);
          frame = new Int16Array(frameSize);
          filled = 0;
        }
      }
    }
    return out;
  };
}

/** PCM16 little-endian bytes → float samples in [-1, 1). */
export function pcm16ToFloat32(buffer: ArrayBuffer): Float32Array {
  const view = new DataView(buffer);
  const n = Math.floor(buffer.byteLength / 2);
  const out = new Float32Array(n);
  for (let i = 0; i < n; i += 1) out[i] = view.getInt16(i * 2, true) / 0x8000;
  return out;
}

/** Root mean square of PCM16 samples, 0..1 (drives the mic level ring). */
export function rmsLevel(frame: Int16Array): number {
  if (frame.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < frame.length; i += 1) {
    const v = frame[i] / 0x8000;
    sum += v * v;
  }
  return Math.sqrt(sum / frame.length);
}
