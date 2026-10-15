"""Agent avatars (SPEC §8.7): generated with the image model chain or uploaded, always stored as a
384×384 WebP at ``avatars/{avatar_id}.webp`` and served publicly by id."""

from __future__ import annotations

import asyncio
import io
import logging
import re

from PIL import Image, ImageOps, UnidentifiedImageError

from topskill import gemini
from topskill.config import get_settings
from topskill.db import new_id
from topskill.domain import AccentColor
from topskill.errors import ApiError, Codes
from topskill.ratelimit import hit
from topskill.storage import get_blob_store

log = logging.getLogger(__name__)

AVATAR_SIZE = 384
WEBP_QUALITY = 85
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PIXELS = 40_000_000
AVATAR_ID_RE = re.compile(r"^av_[0-9a-f]{20}$")
ALLOWED_FORMATS = frozenset({"PNG", "JPEG", "WEBP"})

STYLE_SUFFIX = (
    "flat vector illustration, soft gradient background in {accent} tones, centered subject, "
    "no text, no letters"
)


def avatar_path(avatar_id: str) -> str:
    return f"avatars/{avatar_id}.webp"


def is_avatar_id(value: str | None) -> bool:
    return isinstance(value, str) and bool(AVATAR_ID_RE.fullmatch(value))


def build_prompt(prompt: str, accent: AccentColor | None) -> str:
    base = " ".join(prompt.split()).rstrip(" .,;")
    return f"{base}. {STYLE_SUFFIX.format(accent=accent or 'blue')}"


def to_webp(data: bytes) -> bytes:
    """Decode PNG/JPEG/WebP, center-crop to a square, resize to 384×384, encode WebP (quality 85).

    Raises ``ApiError(400, "unsupported_file_type")`` for anything else.
    """
    try:
        with Image.open(io.BytesIO(data)) as probe:
            fmt = (probe.format or "").upper()
            width, height = probe.size
        if fmt not in ALLOWED_FORMATS:
            raise ApiError(400, Codes.UNSUPPORTED_FILE_TYPE, "Upload a PNG, JPEG or WebP image.")
        if width * height > MAX_PIXELS:
            raise ApiError(400, Codes.UNSUPPORTED_FILE_TYPE, "The image dimensions are too large.")
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            img = ImageOps.exif_transpose(img)
            has_alpha = img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info)
            img = img.convert("RGBA" if has_alpha else "RGB")
            img = ImageOps.fit(img, (AVATAR_SIZE, AVATAR_SIZE), method=Image.Resampling.LANCZOS)
            out = io.BytesIO()
            img.save(out, format="WEBP", quality=WEBP_QUALITY, method=4)
            return out.getvalue()
    except ApiError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise ApiError(400, Codes.UNSUPPORTED_FILE_TYPE, "Upload a PNG, JPEG or WebP image.") from exc


async def store_avatar(data: bytes) -> dict:
    """Process image bytes and store them under a new avatar id → ``{avatar_id, avatar_url}``."""
    from topskill.agents.schemas import avatar_url

    webp = await asyncio.to_thread(to_webp, data)
    avatar_id = new_id("av")
    await get_blob_store().put(avatar_path(avatar_id), webp, "image/webp")
    return {"avatar_id": avatar_id, "avatar_url": avatar_url(avatar_id)}


async def generate_avatar(uid: str, prompt: str, accent: AccentColor | None) -> dict:
    """Rate-limited image generation (``AVATAR_RATE_LIMIT_PER_HOUR``) → stored avatar."""
    await hit("avatar", uid, limit=get_settings().avatar_rate_limit_per_hour, window="hour")
    image, mime = await gemini.generate_image(build_prompt(prompt, accent))
    try:
        result = await store_avatar(image)
    except ApiError as exc:
        log.error("avatar_unusable mime=%s bytes=%d", mime, len(image))
        raise ApiError(502, Codes.IMAGE_FAILED, "Image generation failed. Please try again.") from exc
    log.info("avatar_generated avatar_id=%s uid=%s", result["avatar_id"], uid)
    return result


async def avatar_exists(avatar_id: str) -> bool:
    if not is_avatar_id(avatar_id):
        return False
    try:
        await get_blob_store().get(avatar_path(avatar_id))
    except ApiError:
        return False
    return True


async def read_avatar(avatar_id: str) -> bytes:
    if not is_avatar_id(avatar_id):
        raise ApiError(404, Codes.NOT_FOUND, "Avatar not found.")
    return await get_blob_store().get(avatar_path(avatar_id))
