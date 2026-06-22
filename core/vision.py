"""
Vision module — analyze images using Groq's vision model (free tier).
Supported: image URLs, base64-encoded images.
Model: meta-llama/llama-4-scout-17b-16e-instruct (Groq free)
"""
import base64
import os
import re
from pathlib import Path
from typing import Optional

VISION_MODEL = "meta-llama/llama-4-scout-17b-16e-instruct"


class VisionAnalyzer:
    def __init__(self, groq_api_key: str):
        from groq import AsyncGroq
        self.client = AsyncGroq(api_key=groq_api_key)

    async def analyze_url(self, image_url: str, prompt: str = "Describe this image in detail.") -> str:
        response = await self.client.chat.completions.create(
            model=VISION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text",      "text": prompt},
                ],
            }],
            max_tokens=1024,
        )
        return response.choices[0].message.content

    async def analyze_bytes(self, image_bytes: bytes, mime_type: str = "image/jpeg",
                            prompt: str = "Describe this image in detail.") -> str:
        b64 = base64.b64encode(image_bytes).decode("utf-8")
        data_url = f"data:{mime_type};base64,{b64}"
        return await self.analyze_url(data_url, prompt)

    async def analyze_file(self, file_path: str, prompt: str = "Describe this image in detail.") -> str:
        p = Path(file_path)
        ext = p.suffix.lower()
        mime_map = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
                    ".gif": "image/gif", ".webp": "image/webp"}
        mime = mime_map.get(ext, "image/jpeg")
        with open(file_path, "rb") as f:
            return await self.analyze_bytes(f.read(), mime, prompt)

    async def analyze_telegram_photo(self, file_bytes: bytes,
                                     user_caption: Optional[str] = None) -> str:
        prompt = user_caption if user_caption else (
            "Please describe this image in detail. Include: what you see, any text visible, "
            "colors, objects, people, setting, and anything notable."
        )
        return await self.analyze_bytes(file_bytes, "image/jpeg", prompt)
