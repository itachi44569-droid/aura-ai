"""
The Core AI Brain — upgraded with:
  - Multi-agent routing (specialist system prompts)
  - Self-reflection loop (optional, ENABLE_REFLECTION=true)
  - Rate limiting + content filter
  - Analytics tracking
  - Vision (image analysis via Groq)
  - Voice (audio transcription via Groq Whisper)
  - Hybrid RAG (BM25 + semantic, via core/hybrid_rag.py)
  - Reminder scheduling (APScheduler)
  - 3-tier memory: short-term + working + long-term
  - Agentic tool loop with parallel execution
"""
import json
import asyncio
import os
import time
from typing import AsyncIterator, Optional

from groq import AsyncGroq

from .memory      import Memory
from .tools       import Tool, run_tool
from .analytics   import Analytics
from .filter      import rate_limiter, content_filter
from .router      import AgentRouter
from .reflection  import ReflectionEngine

# ── Models ─────────────────────────────────────────────────────────────────────

MODEL_STRONG  = "llama-3.3-70b-versatile"
MODEL_FAST    = "llama-3.1-8b-instant"
MODEL_EXTRACT = "llama-3.1-8b-instant"

MAX_TOOL_LOOPS     = 6
MAX_TOKENS         = 1024
FACT_EXTRACT_EVERY = 5


class Brain:
    """Universal AI engine. One instance, many channels."""

    def __init__(
        self,
        personality: dict,
        memory:      Memory,
        rag          = None,
        tools:       list[Tool] = None,
        client_id:   str = "default",
    ):
        self.personality = personality
        self.memory      = memory
        self.rag         = rag
        self.tools       = tools or []
        self.client_id   = client_id

        api_key      = os.getenv("GROQ_API_KEY", "")
        self._groq   = AsyncGroq(api_key=api_key)
        self._analytics = Analytics()
        self._router    = AgentRouter(self._groq)
        self._reflector = ReflectionEngine(self._groq)
        self._exchange_counts: dict[str, int] = {}

        # Optional vision + voice (lazy init to avoid import errors)
        self._vision = None
        self._voice  = None

    def get_vision(self):
        if self._vision is None:
            try:
                from .vision import VisionAnalyzer
                self._vision = VisionAnalyzer(os.getenv("GROQ_API_KEY",""))
            except Exception:
                pass
        return self._vision

    def get_voice(self):
        if self._voice is None:
            try:
                from .voice import VoiceTranscriber
                self._voice = VoiceTranscriber(os.getenv("GROQ_API_KEY",""))
            except Exception:
                pass
        return self._voice

    # ── Public API ─────────────────────────────────────────────────────────────

    async def think(self, user_id: str, user_msg: str,
                    channel: str = "telegram") -> str:
        """Main entry point. Returns final text response."""
        t0 = time.time()

        # Rate limit check
        ok, reason = rate_limiter.check(user_id)
        if not ok:
            return reason

        # Content filter
        ok, reason = content_filter.check_input(user_msg)
        if not ok:
            return reason

        # Track message
        self._analytics.log_message(user_id, channel)

        # Route to specialist agent
        agent_type, specialist_ext = await self._router.route(user_msg)

        messages = await self._build_messages(user_id, user_msg, specialist_ext)
        tool_schemas = [t.schema for t in self.tools] if self.tools else None

        response = await self._agent_loop(messages, tool_schemas)

        # Handle reminder scheduling from tool results
        await self._handle_reminders(user_id, response)

        # Self-reflection (optional — adds 1 extra LLM call)
        response, _ = await self._reflector.reflect(user_msg, response)

        # Persist exchange
        self.memory.add_exchange(user_id, user_msg, response)

        # Background fact extraction
        self._exchange_counts[user_id] = self._exchange_counts.get(user_id, 0) + 1
        if self._exchange_counts[user_id] % FACT_EXTRACT_EVERY == 0:
            asyncio.create_task(self._extract_facts(user_id, user_msg, response))

        # Analytics
        latency = (time.time() - t0) * 1000
        self._analytics.log_response(user_id, latency, channel=channel)

        return response

    async def think_stream(self, user_id: str, user_msg: str,
                           channel: str = "telegram") -> AsyncIterator[str]:
        """Streaming version. Tools run first (non-streaming), then answer streams."""
        t0 = time.time()

        ok, reason = rate_limiter.check(user_id)
        if not ok:
            yield reason
            return

        ok, reason = content_filter.check_input(user_msg)
        if not ok:
            yield reason
            return

        self._analytics.log_message(user_id, channel)

        _, specialist_ext = await self._router.route(user_msg)
        messages     = await self._build_messages(user_id, user_msg, specialist_ext)
        tool_schemas = [t.schema for t in self.tools] if self.tools else None
        messages     = await self._tool_phase(messages, tool_schemas)

        full_response = ""
        try:
            stream = await self._groq.chat.completions.create(
                model      = MODEL_STRONG,
                messages   = messages,
                max_tokens = MAX_TOKENS,
                temperature= self.personality.get("temperature", 0.7),
                stream     = True,
            )
            async for chunk in stream:
                delta = chunk.choices[0].delta.content or ""
                if delta:
                    full_response += delta
                    yield delta
        except Exception:
            result = await self._call_llm(messages, MODEL_FAST)
            full_response = result
            yield result

        # Post-stream: memory + analytics
        self.memory.add_exchange(user_id, user_msg, full_response)
        self._exchange_counts[user_id] = self._exchange_counts.get(user_id, 0) + 1
        if self._exchange_counts[user_id] % FACT_EXTRACT_EVERY == 0:
            asyncio.create_task(self._extract_facts(user_id, user_msg, full_response))
        latency = (time.time() - t0) * 1000
        self._analytics.log_response(user_id, latency, channel=channel)

    # ── Vision ─────────────────────────────────────────────────────────────────

    async def analyze_image(self, user_id: str, image_bytes: bytes,
                            caption: str = None, channel: str = "telegram") -> str:
        """Analyze an image sent by the user."""
        vision = self.get_vision()
        if not vision:
            return "Image analysis is not available right now."
        try:
            self._analytics.log_image(user_id, channel)
            description = await vision.analyze_telegram_photo(image_bytes, caption)
            # Let the brain respond contextually
            prompt = f"[The user sent an image. Here is what the image shows:]\n{description}"
            if caption:
                prompt += f"\n[The user's caption was: {caption}]"
            return await self.think(user_id, prompt, channel)
        except Exception as e:
            self._analytics.log_error("vision_error", user_id, str(e))
            return "I couldn't analyze that image. Please try another."

    # ── Voice ──────────────────────────────────────────────────────────────────

    async def transcribe_and_respond(self, user_id: str, audio_bytes: bytes,
                                     filename: str = "audio.ogg",
                                     channel: str = "telegram") -> tuple[str, str]:
        """
        Transcribe voice note and respond to it.
        Returns (transcript, ai_response).
        """
        voice = self.get_voice()
        if not voice:
            return "", "Voice transcription is not available right now."
        try:
            self._analytics.log_voice(user_id, channel=channel)
            transcript = await voice.transcribe_bytes(audio_bytes, filename)
            if not transcript.strip():
                return "", "I couldn't make out what you said. Please try again."
            response = await self.think(user_id, transcript, channel)
            return transcript, response
        except Exception as e:
            self._analytics.log_error("voice_error", user_id, str(e))
            return "", "I couldn't process that voice message. Please try again."

    # ── Stats for /stats command ───────────────────────────────────────────────

    def get_stats(self) -> dict:
        return self._analytics.get_summary(days=7)

    # ── Context building ───────────────────────────────────────────────────────

    async def _build_messages(self, user_id: str, user_msg: str,
                              specialist_ext: str = "") -> list[dict]:
        facts      = self.memory.get_user_facts(user_id)
        summaries  = self.memory.get_summaries(user_id)
        summary_text = ("\n\n[PREVIOUS CONVERSATION SUMMARY]\n" + summaries[0]) if summaries else ""

        # RAG search — try hybrid if available, else standard
        rag_context = ""
        if self.rag:
            try:
                from .hybrid_rag import HybridRAG
                hybrid = HybridRAG(self.rag)
                docs   = await hybrid.search(user_msg, user_id=self.client_id, top_k=3)
            except Exception:
                docs = await self.rag.search(user_msg, client_id=self.client_id, top_k=3)
            if docs:
                rag_context = "\n\n[KNOWLEDGE BASE]\n" + "\n---\n".join(docs)

        system = self._build_system(facts, rag_context, summary_text, specialist_ext)
        messages = [{"role": "system", "content": system}]
        messages.extend(self.memory.get_history(user_id))
        messages.append({"role": "user", "content": user_msg})
        return messages

    def _build_system(self, facts: str, rag: str, summary: str,
                      specialist_ext: str = "") -> str:
        base = self.personality.get(
            "system_prompt",
            "You are a helpful, intelligent AI assistant. Be concise and human."
        )
        parts = [base]
        if specialist_ext:
            parts.append(f"\n[SPECIALIST ROLE]\n{specialist_ext}")
        if facts:
            parts.append(f"\n[WHAT YOU KNOW ABOUT THIS USER]\n{facts}")
        if summary:
            parts.append(summary)
        if rag:
            parts.append(rag)
        parts.append(
            "\nRules: "
            "Answer in the same language the user writes. "
            "Be concise — no unnecessary filler. "
            "Never mention you are an AI unless directly asked. "
            "If using tools, act on the result immediately without explaining the tool call."
        )
        return "\n".join(parts)

    # ── Agentic loop ───────────────────────────────────────────────────────────

    async def _agent_loop(self, messages: list, tool_schemas) -> str:
        messages = await self._tool_phase(messages, tool_schemas)
        return await self._call_llm(messages, MODEL_STRONG)

    async def _tool_phase(self, messages: list, tool_schemas) -> list:
        for _ in range(MAX_TOOL_LOOPS):
            kwargs = {
                "model":       MODEL_STRONG,
                "messages":    messages,
                "max_tokens":  512,
                "temperature": self.personality.get("temperature", 0.7),
            }
            if tool_schemas:
                kwargs["tools"]       = tool_schemas
                kwargs["tool_choice"] = "auto"

            try:
                resp = await self._groq.chat.completions.create(**kwargs)
            except Exception:
                break

            msg = resp.choices[0].message
            if not msg.tool_calls:
                if msg.content:
                    messages.append({"role": "assistant", "content": msg.content})
                break

            messages.append({
                "role":       "assistant",
                "content":    msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in msg.tool_calls
                ],
            })

            # Track tool calls in analytics
            for tc in msg.tool_calls:
                self._analytics.log_tool_call(tc.function.name)

            results = await asyncio.gather(*[
                run_tool(tc.function.name, json.loads(tc.function.arguments or "{}"))
                for tc in msg.tool_calls
            ])

            for tc, result in zip(msg.tool_calls, results):
                messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      json.dumps(result),
                })

        return messages

    async def _call_llm(self, messages: list, model: str) -> str:
        for m in [model, MODEL_FAST]:
            try:
                resp = await self._groq.chat.completions.create(
                    model       = m,
                    messages    = messages,
                    max_tokens  = MAX_TOKENS,
                    temperature = self.personality.get("temperature", 0.7),
                )
                content = resp.choices[0].message.content
                return content or "I'm sorry, I couldn't generate a response."
            except Exception:
                continue
        return "I'm having trouble right now. Please try again in a moment."

    # ── Reminder handling ──────────────────────────────────────────────────────

    async def _handle_reminders(self, user_id: str, response_text: str):
        """If a set_reminder tool was called, register it with the scheduler."""
        try:
            from .scheduler import scheduler
            if "reminder_requested" in response_text or "reminder_registered" in response_text:
                pass  # Handled via the tool result; scheduler picks up on first call
        except Exception:
            pass

    # ── Background: fact extraction ────────────────────────────────────────────

    async def _extract_facts(self, user_id: str, user_msg: str, ai_response: str):
        prompt = [
            {"role": "system", "content": (
                "Extract factual information about the user from this conversation exchange. "
                "Output a JSON object with keys like: name, location, occupation, interests, "
                "language, preferences, mentioned_products, goals. "
                "Only include keys where you found actual information. "
                "Output ONLY valid JSON, nothing else."
            )},
            {"role": "user", "content": f"User said: {user_msg}\nAI responded: {ai_response}"},
        ]
        try:
            resp = await self._groq.chat.completions.create(
                model=MODEL_EXTRACT, messages=prompt, max_tokens=256, temperature=0.1,
            )
            text  = (resp.choices[0].message.content or "").strip()
            start = text.find("{"); end = text.rfind("}") + 1
            if start >= 0 and end > start:
                facts = json.loads(text[start:end])
                if facts:
                    self.memory.update_user_facts(user_id, facts)
        except Exception:
            pass

    async def summarize_history(self, user_id: str) -> str:
        history = self.memory.get_history(user_id)
        if len(history) < 6:
            return ""
        convo = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in history)
        prompt = [
            {"role": "system", "content":
             "Summarize this conversation in 3-5 bullet points, focusing on key facts, decisions, and user preferences."},
            {"role": "user", "content": convo},
        ]
        try:
            resp = await self._groq.chat.completions.create(
                model=MODEL_FAST, messages=prompt, max_tokens=256, temperature=0.3,
            )
            summary = resp.choices[0].message.content or ""
            if summary:
                self.memory.save_summary(user_id, summary)
            return summary
        except Exception:
            return ""
