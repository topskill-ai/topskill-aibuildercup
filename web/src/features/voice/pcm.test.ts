import { describe, expect, it } from "vitest";
import { createDecimator, pcm16ToFloat32, rmsLevel } from "./pcm";

describe("createDecimator", () => {
  it("turns 48 kHz floats into 16 kHz PCM16 frames of the requested size", () => {
    const push = createDecimator(48_000, 16_000, 4);
    const input = new Float32Array(30).fill(0.5); // 30 samples → 10 output samples → 2 full frames
    const frames = push(input);
    expect(frames).toHaveLength(2);
    expect(frames.every((f) => f.length === 4)).toBe(true);
    expect(Array.from(frames[0])).toEqual([16384, 16384, 16384, 16384]);
    // The remaining two samples are kept for the next call.
    expect(push(new Float32Array(6).fill(0.5))).toHaveLength(1);
  });

  it("averages each window and clips to the PCM16 range", () => {
    const push = createDecimator(32_000, 16_000, 3);
    const frames = push(new Float32Array([1, 0, -2, -2, 0.25, 0.75]));
    expect(Array.from(frames[0])).toEqual([Math.round(0.5 * 0x7fff), -0x8000, Math.round(0.5 * 0x7fff)]);
  });

  it("handles a non-integer ratio (44.1 kHz) at the right average rate", () => {
    const push = createDecimator(44_100, 16_000, 160);
    let samples = 0;
    for (let i = 0; i < 100; i += 1) samples += push(new Float32Array(441)).reduce((n, f) => n + f.length, 0);
    expect(samples).toBeGreaterThanOrEqual(15_840); // 1 s of input → 16 000 samples minus the open frame
    expect(samples).toBeLessThanOrEqual(16_000);
  });
});

describe("pcm16ToFloat32 / rmsLevel", () => {
  it("decodes little-endian PCM16", () => {
    const buf = new ArrayBuffer(6);
    const view = new DataView(buf);
    view.setInt16(0, 0x4000, true);
    view.setInt16(2, -0x8000, true);
    view.setInt16(4, 0, true);
    expect(Array.from(pcm16ToFloat32(buf))).toEqual([0.5, -1, 0]);
    expect(pcm16ToFloat32(new ArrayBuffer(3))).toHaveLength(1);
  });

  it("measures loudness", () => {
    expect(rmsLevel(new Int16Array(0))).toBe(0);
    expect(rmsLevel(new Int16Array([0, 0]))).toBe(0);
    expect(rmsLevel(new Int16Array([0x4000, -0x4000]))).toBeCloseTo(0.5);
  });
});
