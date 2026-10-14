"""Server-side ElevenLabs text-to-speech integration.

The API key is read only from server environment variables and is never
returned to clients. Generated audio is returned as MP3 bytes.
"""
from __future__ import annotations

import os
from typing import Final

import httpx
from fastapi import HTTPException

ELEVENLABS_BASE_URL: Final[str] = "https://api.elevenlabs.io/v1"
DEFAULT_MODEL_ID: Final[str] = "eleven_multilingual_v2"
DEFAULT_OUTPUT_FORMAT: Final[str] = "mp3_44100_128"
MAX_TEXT_LENGTH: Final[int] = 1200


def generate_speech(text: str) -> bytes:
    """Generate MP3 speech for a short reminder using the configured voice."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    model_id = os.getenv("ELEVENLABS_MODEL_ID", DEFAULT_MODEL_ID).strip()

    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="Voice is not configured. Set ELEVENLABS_API_KEY on the backend.",
        )
    if not voice_id:
        raise HTTPException(
            status_code=503,
            detail="Voice is not configured. Set ELEVENLABS_VOICE_ID on the backend.",
        )

    try:
        response = httpx.post(
            f"{ELEVENLABS_BASE_URL}/text-to-speech/{voice_id}",
            params={"output_format": DEFAULT_OUTPUT_FORMAT},
            headers={
                "xi-api-key": api_key,
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            json={"text": text, "model_id": model_id},
            timeout=httpx.Timeout(30.0, connect=5.0),
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Speech generation timed out.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the speech service.") from exc

    if response.status_code in (401, 403):
        raise HTTPException(
            status_code=502,
            detail="ElevenLabs authentication failed. Check the server API key and permissions.",
        )
    if response.status_code == 429:
        raise HTTPException(
            status_code=429,
            detail="ElevenLabs rate limit or usage quota reached. Try again later.",
        )
    if response.is_error:
        # Never forward provider response bodies; they may contain diagnostic details.
        raise HTTPException(
            status_code=502,
            detail=f"ElevenLabs speech generation failed (HTTP {response.status_code}).",
        )

    if not response.content:
        raise HTTPException(status_code=502, detail="ElevenLabs returned empty audio.")
    return response.content


def validate_speech_text(text: str) -> str:
    """Normalize and bound user-supplied text before sending it to the provider."""
    normalized = " ".join(text.split())
    if not normalized:
        raise HTTPException(status_code=422, detail="Text must not be empty.")
    if len(normalized) > MAX_TEXT_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Text must be {MAX_TEXT_LENGTH} characters or fewer.",
        )
    return normalized
