"""
Multi-agent router — classifies the user's message and injects a specialist
system-prompt extension so the right "agent" handles it.

Agent types:
  finance   — stocks, crypto, trading, economy
  code      — programming, debugging, algorithms
  creative  — writing, brainstorming, storytelling
  research  — facts, science, history, explanations
  general   — everything else

Uses Groq fast model (llama-3.1-8b-instant) for classification — one tiny call.
"""
import os

ROUTER_MODEL = "llama-3.1-8b-instant"

_AGENT_PROMPTS: dict[str, str] = {
    "finance": (
        "You are a financial analyst. Provide precise data, cite prices/percentages, "
        "explain market context. Use bullet points for data. Always add a risk disclaimer."
    ),
    "code": (
        "You are a senior software engineer. Write clean, production-ready code. "
        "Explain your reasoning. Always include error handling. Show examples."
    ),
    "creative": (
        "You are a creative writing partner. Be imaginative, vivid, and engaging. "
        "Use descriptive language, vary sentence structure, and match the user's tone."
    ),
    "research": (
        "You are a research assistant. Provide accurate, well-structured explanations. "
        "Break complex topics into digestible parts. Cite sources when possible."
    ),
    "general": "",
}

_CLASSIFY_PROMPT = """\
Classify this user message into exactly one category:
finance | code | creative | research | general

Message: {message}

Reply with just the category word. Nothing else."""


class AgentRouter:
    def __init__(self, groq_client):
        self.client = groq_client
        self._cache: dict[str, str] = {}  # simple in-memory cache

    async def route(self, message: str) -> tuple[str, str]:
        """
        Returns (agent_type, specialist_system_prompt_extension).
        """
        key = message[:100].lower()
        if key in self._cache:
            agent = self._cache[key]
            return agent, _AGENT_PROMPTS[agent]

        try:
            resp = await self.client.chat.completions.create(
                model=ROUTER_MODEL,
                messages=[{"role": "user", "content": _CLASSIFY_PROMPT.format(message=message[:500])}],
                temperature=0.0,
                max_tokens=10,
            )
            raw   = resp.choices[0].message.content.strip().lower()
            agent = raw if raw in _AGENT_PROMPTS else "general"
        except Exception:
            agent = "general"

        self._cache[key] = agent
        return agent, _AGENT_PROMPTS[agent]
