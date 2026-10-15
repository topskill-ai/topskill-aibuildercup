"""Scripted end-to-end client for the voice WebSocket (SPEC §18.1).

Speaks a question (macOS ``say`` + ``afconvert`` → 16 kHz mono PCM16, or a WAV file), streams it in real
time like the browser does, then waits for the spoken answer and checks that

* the visitor's speech came back as a caption (input transcription),
* answer audio frames arrived (24 kHz PCM16),
* a ``round`` event settled the answer (``outcome == "ok"``, with billing).

Usage (from ``backend/``)::

    # local dev backend (dev auth bypass), e.g. on port 8271
    uv run python -m scripts.voice_e2e --base http://127.0.0.1:8271 --slug visa-advisor --dev-user e2e_voice
    # prod with a Firebase ID token in $VOICE_E2E_ID_TOKEN
    uv run python -m scripts.voice_e2e --base https://topskill-api-920441209758.asia-southeast1.run.app \\
        --slug visa-advisor --origin https://top-skill.web.app

Exit code 0 when every check passed; a JSON summary is printed either way. ``--out reply.wav`` saves the
answer audio. Behind a local HTTP proxy that answers CONNECT with HTTP/1.0 (websockets rejects it), set
``WSS_PROXY=socks5h://host:port`` and run with ``uv run --with 'python-socks[asyncio]' …``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass, field
from pathlib import Path

INPUT_RATE = 16_000
OUTPUT_RATE = 24_000
CHUNK_MS = 40
DEFAULT_QUESTION = "What documents do I need for a work visa? Please keep it short."


def spoken_pcm(text: str, voice: str | None = None) -> bytes:
    """``text`` spoken by macOS ``say`` (optional ``voice``) as raw 16 kHz mono PCM16 little endian."""
    if not shutil.which("say") or not shutil.which("afconvert"):
        raise RuntimeError("macOS 'say' and 'afconvert' are required to synthesise the question")
    with tempfile.TemporaryDirectory(prefix="voice-e2e-") as tmp:
        aiff, wav = Path(tmp) / "q.aiff", Path(tmp) / "q.wav"
        subprocess.run(["say", *(["-v", voice] if voice else []), "-o", str(aiff), text], check=True)
        subprocess.run(
            ["afconvert", "-f", "WAVE", "-d", f"LEI16@{INPUT_RATE}", "-c", "1", str(aiff), str(wav)],
            check=True,
        )
        return read_wav_pcm(wav)


def read_wav_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as w:
        if w.getframerate() != INPUT_RATE or w.getnchannels() != 1 or w.getsampwidth() != 2:
            raise ValueError(f"{path} must be 16 kHz mono 16-bit PCM")
        return w.readframes(w.getnframes())


def write_wav(path: Path, pcm: bytes, rate: int = OUTPUT_RATE) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)


@dataclass
class Result:
    ready: dict | None = None
    user_caption: str = ""
    model_caption: str = ""
    audio_frames: int = 0
    audio_bytes: int = 0
    first_audio_s: float | None = None
    tools: list[dict] = field(default_factory=list)
    rounds: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)
    closed: dict | None = None
    close_code: int | None = None

    def checks(self) -> dict[str, bool]:
        ok_round = next((r for r in self.rounds if r.get("outcome") == "ok"), None)
        return {
            "ready": self.ready is not None,
            "user_transcript": bool(self.user_caption.strip()),
            "model_transcript": bool(self.model_caption.strip()),
            "audio_frames": self.audio_frames > 0,
            "round_settled": ok_round is not None and ok_round.get("billing") is not None,
            "no_errors": not self.errors,
        }


def ws_url(base: str, slug: str) -> str:
    base = base.rstrip("/")
    if base.startswith("https://"):
        base = "wss://" + base[len("https://") :]
    elif base.startswith("http://"):
        base = "ws://" + base[len("http://") :]
    return f"{base}/api/public/agents/{slug}/voice"


async def run(args: argparse.Namespace) -> Result:
    import websockets  # transitive dependency of google-genai

    pcm = read_wav_pcm(Path(args.audio)) if args.audio else spoken_pcm(args.question, args.say_voice)
    start: dict = {"type": "start", "conversation_id": args.conversation_id}
    token = os.environ.get("VOICE_E2E_ID_TOKEN")
    if token:
        start["token"] = token
    elif args.dev_user:
        start["dev"] = {"uid": args.dev_user}
    url = ws_url(args.base, args.slug)
    local = "127.0.0.1" in url or "localhost" in url
    headers = {"Origin": args.origin} if args.origin else None
    result = Result()
    answer_audio = bytearray()
    t0 = time.monotonic()
    async with websockets.connect(
        url, additional_headers=headers, proxy=None if local else True, max_size=2**22, open_timeout=20
    ) as ws:
        await ws.send(json.dumps(start))
        done = asyncio.Event()

        async def reader() -> None:
            async for frame in ws:
                if isinstance(frame, bytes):
                    result.audio_frames += 1
                    result.audio_bytes += len(frame)
                    answer_audio.extend(frame)
                    if result.first_audio_s is None:
                        result.first_audio_s = round(time.monotonic() - t0, 2)
                    continue
                event = json.loads(frame)
                kind = event.get("type")
                if kind == "ready":
                    result.ready = event
                elif kind == "transcript":
                    if event["role"] == "user":
                        result.user_caption += event["text"]
                    else:
                        result.model_caption += event["text"]
                elif kind == "tool":
                    result.tools.append(event["tool"])
                elif kind == "round":
                    result.rounds.append(event)
                    if event.get("outcome") == "ok":
                        done.set()
                elif kind == "error":
                    result.errors.append(event)
                    done.set()
                elif kind == "closed":
                    result.closed = event
                    done.set()

        reading = asyncio.create_task(reader())
        # Stream the question in real time, then silence so server-side VAD detects the end of speech.
        step = INPUT_RATE * 2 * CHUNK_MS // 1000
        silence = b"\x00" * step
        for i in range(0, len(pcm), step):
            await ws.send(pcm[i : i + step])
            await asyncio.sleep(CHUNK_MS / 1000)
        deadline = time.monotonic() + args.timeout
        while not done.is_set() and time.monotonic() < deadline:
            await ws.send(silence)
            await asyncio.sleep(CHUNK_MS / 1000)
        if not args.keep_open:
            await asyncio.sleep(1.0)  # let trailing audio arrive
            await ws.send(json.dumps({"type": "stop"}))
        try:
            await asyncio.wait_for(reading, timeout=30)
        except TimeoutError:
            reading.cancel()
        result.close_code = ws.close_code
    if args.out and answer_audio:
        write_wav(Path(args.out), bytes(answer_audio))
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="API base URL (http/https)")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--dev-user", help="dev bypass uid (dev/test backends only)")
    parser.add_argument("--origin", help="Origin header to send (must be an allowed CORS origin)")
    parser.add_argument("--conversation-id")
    parser.add_argument("--question", default=DEFAULT_QUESTION)
    parser.add_argument("--say-voice", help="macOS say voice for --question (e.g. Samantha)")
    parser.add_argument("--audio", help="16 kHz mono PCM16 WAV instead of --question")
    parser.add_argument("--timeout", type=float, default=45.0, help="seconds to wait for the answer")
    parser.add_argument("--out", help="save the answer audio as WAV")
    parser.add_argument("--keep-open", action="store_true", help="do not send stop after the answer")
    args = parser.parse_args(argv)
    result = asyncio.run(run(args))
    checks = result.checks()
    summary = {
        "checks": checks,
        "model": result.ready.get("model") if result.ready else None,
        "conversation_id": result.ready.get("conversation_id") if result.ready else None,
        "user_caption": result.user_caption.strip(),
        "model_caption": result.model_caption.strip(),
        "audio_frames": result.audio_frames,
        "audio_seconds": round(result.audio_bytes / (OUTPUT_RATE * 2), 2),
        "first_audio_s": result.first_audio_s,
        "tools": [f"{t['title']} ({t['status']})" for t in result.tools],
        "rounds": [{"outcome": r["outcome"], "billing": r["billing"]} for r in result.rounds],
        "errors": result.errors,
        "closed": result.closed,
        "close_code": result.close_code,
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
