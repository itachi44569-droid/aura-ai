"""
Voice transcription — Groq Whisper (free tier).
Accepts audio bytes (OGG, MP3, WAV, M4A, FLAC) and returns transcribed text.
"""
import io
import os
import tempfile
from pathlib import Path


WHISPER_MODEL = "whisper-large-v3"


class VoiceTranscriber:
    def __init__(self, groq_api_key: str):
        from groq import AsyncGroq
        self.client = AsyncGroq(api_key=groq_api_key)

    async def transcribe_bytes(self, audio_bytes: bytes, filename: str = "audio.ogg",
                               language: str = None) -> str:
        kwargs = dict(
            file=(filename, io.BytesIO(audio_bytes)),
            model=WHISPER_MODEL,
            response_format="text",
        )
        if language:
            kwargs["language"] = language

        result = await self.client.audio.transcriptions.create(**kwargs)
        return result if isinstance(result, str) else result.text

    async def transcribe_file(self, file_path: str, language: str = None) -> str:
        p = Path(file_path)
        with open(file_path, "rb") as f:
            return await self.transcribe_bytes(f.read(), p.name, language)
