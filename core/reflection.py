"""
Self-reflection loop — the AI critiques and improves its own answer.
Uses Groq free tier (fast model for critique, to save tokens).

Enable: ENABLE_REFLECTION=true in .env
Cost: 1 extra LLM call per message — doubles latency but doubles quality.
"""
import os
from typing import Optional

ENABLE_REFLECTION = os.getenv("ENABLE_REFLECTION", "false").lower() == "true"
REFLECT_MODEL     = "llama-3.1-8b-instant"  # Fast, free


_CRITIQUE_PROMPT = """\
You are a quality evaluator. Review this AI response and identify any issues:

USER'S QUESTION:
{question}

AI'S DRAFT RESPONSE:
{draft}

Identify problems (if any):
1. Factual errors or unsupported claims?
2. Missing important information?
3. Unclear or confusing parts?
4. Too long / too short?
5. Tone mismatch?

If the response is good, reply: APPROVED
If it needs improvement, reply: IMPROVE: <brief instruction for what to fix>
Keep your evaluation under 50 words."""

_IMPROVE_PROMPT = """\
Improve this response based on the feedback below.

USER'S QUESTION: {question}
ORIGINAL RESPONSE: {draft}
FEEDBACK: {critique}

Provide only the improved response, no meta-commentary."""


class ReflectionEngine:
    def __init__(self, groq_client):
        self.client = groq_client

    async def reflect(self, question: str, draft: str,
                      temperature: float = 0.3) -> tuple[str, bool]:
        """
        Returns (final_response, was_improved).
        If ENABLE_REFLECTION is false or critique says APPROVED, returns original draft.
        """
        if not ENABLE_REFLECTION:
            return draft, False

        critique = await self._critique(question, draft)
        if critique.startswith("APPROVED"):
            return draft, False

        improved = await self._improve(question, draft, critique.removeprefix("IMPROVE:").strip())
        return improved, True

    async def _critique(self, question: str, draft: str) -> str:
        resp = await self.client.chat.completions.create(
            model=REFLECT_MODEL,
            messages=[{"role": "user", "content": _CRITIQUE_PROMPT.format(
                question=question, draft=draft[:2000]
            )}],
            temperature=0.1,
            max_tokens=120,
        )
        return resp.choices[0].message.content.strip()

    async def _improve(self, question: str, draft: str, critique: str) -> str:
        resp = await self.client.chat.completions.create(
            model=REFLECT_MODEL,
            messages=[{"role": "user", "content": _IMPROVE_PROMPT.format(
                question=question, draft=draft[:2000], critique=critique
            )}],
            temperature=0.4,
            max_tokens=1024,
        )
        return resp.choices[0].message.content.strip()
