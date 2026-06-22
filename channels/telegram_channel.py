"""
Telegram channel — full-featured bot adapter for the AI Brain.

Handles: text, photos, voice notes, documents
Commands: /start /help /clear /memory /ingest /stats /reminders /cancel
"""
import asyncio
import os
import io
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters,
)
from core.brain   import Brain
from core.rag     import RAG
from core.scheduler import scheduler


class TelegramChannel:
    def __init__(self, brain: Brain, rag: RAG = None):
        self.brain = brain
        self.rag   = rag
        self.token = os.getenv("TELEGRAM_TOKEN", "")

    def build_app(self) -> Application:
        app = Application.builder().token(self.token).build()

        # Commands
        app.add_handler(CommandHandler("start",     self._start))
        app.add_handler(CommandHandler("help",      self._help))
        app.add_handler(CommandHandler("clear",     self._clear))
        app.add_handler(CommandHandler("memory",    self._memory))
        app.add_handler(CommandHandler("ingest",    self._ingest))
        app.add_handler(CommandHandler("stats",     self._stats))
        app.add_handler(CommandHandler("reminders", self._reminders))
        app.add_handler(CommandHandler("cancel",    self._cancel))

        # Message types
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._message))
        app.add_handler(MessageHandler(filters.PHOTO, self._photo))
        app.add_handler(MessageHandler(filters.VOICE, self._voice))
        app.add_handler(MessageHandler(filters.Document.ALL, self._document))

        return app

    def run(self):
        # Wire scheduler to send via this bot
        app = self.build_app()

        async def _scheduler_send(user_id: str, message: str):
            try:
                await app.bot.send_message(chat_id=int(user_id), text=message)
            except Exception as e:
                print(f"[Scheduler] Failed to send to {user_id}: {e}")

        scheduler.set_callback(_scheduler_send)
        scheduler.start()

        print("[Telegram] Bot is running...")
        app.run_polling(drop_pending_updates=True)

    # ── Commands ───────────────────────────────────────────────────────────────

    async def _start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        name     = update.effective_user.first_name or "there"
        p        = self.brain.personality
        greeting = p.get("greeting", f"Hello {name}! I'm your AI assistant. How can I help you today?")
        greeting = greeting.replace("{name}", name)
        await update.message.reply_text(greeting)

    async def _help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        p          = self.brain.personality
        name       = p.get("name", "AI Assistant")
        tools_list = "\n".join(f"• {t.description.split('.')[0]}" for t in self.brain.tools[:8])
        text = (
            f"<b>{name}</b>\n\n"
            f"{p.get('description','Your intelligent AI assistant.')}\n\n"
            f"<b>What I can do:</b>\n{tools_list}\n\n"
            f"<b>Commands:</b>\n"
            f"/clear — reset conversation memory\n"
            f"/memory — show what I know about you\n"
            f"/stats — usage stats\n"
            f"/ingest [url] — teach me from a URL\n"
            f"/reminders — list your active reminders\n"
            f"/cancel [job_id] — cancel a reminder\n\n"
            f"<b>Also supports:</b>\n"
            f"• Send a photo — I'll analyze it\n"
            f"• Send a voice message — I'll transcribe and respond\n"
            f"• Send a .pdf/.txt/.md file — I'll learn from it\n\n"
            f"Just send me a message to get started!"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _clear(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = str(update.effective_user.id)
        if uid in self.brain.memory._cache:
            self.brain.memory._cache[uid].clear()
        await update.message.reply_text("Memory cleared! Starting fresh.")

    async def _memory(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid   = str(update.effective_user.id)
        facts = self.brain.memory.get_user_facts(uid)
        if facts:
            await update.message.reply_text(f"<b>What I know about you:</b>\n{facts}", parse_mode="HTML")
        else:
            await update.message.reply_text("I don't have any stored facts about you yet. Chat with me more!")

    async def _ingest(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self.rag:
            await update.message.reply_text("Knowledge ingestion is not enabled for this bot.")
            return
        if not ctx.args:
            await update.message.reply_text("Usage: /ingest [url]\nExample: /ingest https://example.com/faq")
            return
        url = ctx.args[0]
        msg = await update.message.reply_text(f"Ingesting {url}...")
        try:
            count = self.rag.ingest_url(url, client_id=self.brain.client_id)
            await msg.edit_text(f"Ingested {count} text chunks from {url}. I can now answer questions about it.")
        except Exception as e:
            await msg.edit_text(f"Failed to ingest: {e}")

    async def _stats(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        s = self.brain.get_stats()
        tools_text = ""
        if s["top_tools"]:
            tools_text = "\n<b>Top tools used:</b>\n" + "\n".join(
                f"• {t['tool']}: {t['count']}x" for t in s["top_tools"]
            )
        text = (
            f"<b>Usage stats (last 7 days)</b>\n\n"
            f"Messages: {s['total_messages']}\n"
            f"Unique users: {s['unique_users']}\n"
            f"Avg response time: {s['avg_latency_ms']}ms\n"
            f"Images analyzed: {s['images_analyzed']}\n"
            f"Voice notes: {s['voice_notes']}\n"
            f"Errors: {s['errors']}"
            f"{tools_text}"
        )
        await update.message.reply_text(text, parse_mode="HTML")

    async def _reminders(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid  = str(update.effective_user.id)
        jobs = scheduler.list_reminders(uid)
        if not jobs:
            await update.message.reply_text("You have no active reminders.\nSet one by asking me to remind you about something!")
            return
        lines = [f"<b>Your reminders:</b>\n"]
        for j in jobs:
            msg_text = j.get("args", ["",""])[1] if j.get("args") else "reminder"
            lines.append(f"• {msg_text}\n  Next: {j.get('next_run','')}\n  ID: <code>{j['job_id']}</code>")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    async def _cancel(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not ctx.args:
            await update.message.reply_text("Usage: /cancel [job_id]\nGet job IDs from /reminders")
            return
        job_id = ctx.args[0]
        ok     = scheduler.cancel_reminder(job_id)
        if ok:
            await update.message.reply_text("Reminder cancelled.")
        else:
            await update.message.reply_text("Couldn't find that reminder. Use /reminders to see active ones.")

    # ── Photo handler ──────────────────────────────────────────────────────────

    async def _photo(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid     = str(update.effective_user.id)
        caption = update.message.caption or ""

        await update.effective_chat.send_action("typing")
        msg = await update.message.reply_text("Analyzing image...")

        try:
            photo      = update.message.photo[-1]  # highest resolution
            file       = await photo.get_file()
            buf        = io.BytesIO()
            await file.download_to_memory(buf)
            image_bytes = buf.getvalue()

            response = await self.brain.analyze_image(uid, image_bytes, caption or None)
            await msg.edit_text(response)
        except Exception as e:
            print(f"[Telegram] Photo error for {uid}: {e}")
            await msg.edit_text("Sorry, I couldn't analyze that image.")

    # ── Voice handler ──────────────────────────────────────────────────────────

    async def _voice(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid = str(update.effective_user.id)

        await update.effective_chat.send_action("typing")
        msg = await update.message.reply_text("Transcribing voice message...")

        try:
            voice_obj  = update.message.voice
            file       = await voice_obj.get_file()
            buf        = io.BytesIO()
            await file.download_to_memory(buf)
            audio_bytes = buf.getvalue()

            transcript, response = await self.brain.transcribe_and_respond(
                uid, audio_bytes, "audio.ogg"
            )

            if transcript:
                full = f"<i>You said:</i> {transcript}\n\n{response}"
                await msg.edit_text(full, parse_mode="HTML")
            else:
                await msg.edit_text(response)
        except Exception as e:
            print(f"[Telegram] Voice error for {uid}: {e}")
            await msg.edit_text("Sorry, I couldn't process that voice message.")

    # ── Document handler ───────────────────────────────────────────────────────

    async def _document(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        if not self.rag:
            await update.message.reply_text("Document ingestion is not enabled.")
            return
        doc  = update.message.document
        name = doc.file_name or "document"
        ext  = name.rsplit(".", 1)[-1].lower() if "." in name else ""
        if ext not in ("txt","md","pdf","json"):
            await update.message.reply_text(f"Unsupported type: .{ext}  I accept: .txt .md .pdf .json")
            return
        msg  = await update.message.reply_text(f"Processing {name}...")
        import tempfile, os as _os
        path = _os.path.join(tempfile.gettempdir(), name)
        try:
            file = await doc.get_file()
            await file.download_to_drive(path)
            count = self.rag.ingest_file(path, client_id=self.brain.client_id)
            await msg.edit_text(f"Learned {count} chunks from {name}. Ask me anything about it!")
        except Exception as e:
            await msg.edit_text(f"Error: {e}")

    # ── Text message handler ───────────────────────────────────────────────────

    async def _message(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        uid      = str(update.effective_user.id)
        user_msg = update.message.text.strip()

        await update.effective_chat.send_action("typing")

        # Check for reminder scheduling intent in message
        _lower = user_msg.lower()
        if any(kw in _lower for kw in ["remind me", "set reminder", "remind at", "schedule"]):
            # Let the brain handle via set_reminder tool, then wire to scheduler
            response = await self.brain.think(uid, user_msg)
            # If response contains reminder info, schedule it
            try:
                if "reminder_requested" in response or "reminder has been registered" in response.lower():
                    import re
                    when_m = re.search(r'"when"\s*:\s*"([^"]+)"', response)
                    msg_m  = re.search(r'"message"\s*:\s*"([^"]+)"', response)
                    if when_m and msg_m:
                        result = scheduler.add_reminder(uid, msg_m.group(1), when_m.group(1))
                        response = f"Reminder set! I'll remind you: {msg_m.group(1)}\nWhen: {result.get('when','')}"
            except Exception:
                pass
            await update.message.reply_text(response)
            return

        # Standard streaming response
        try:
            sent_msg   = None
            buffer     = ""
            last_len   = 0
            UPDATE_EVERY = 40

            async for chunk in self.brain.think_stream(uid, user_msg):
                buffer += chunk
                if len(buffer) - last_len >= UPDATE_EVERY:
                    if sent_msg is None:
                        sent_msg = await update.message.reply_text(buffer + " ▌")
                    else:
                        try:
                            await sent_msg.edit_text(buffer + " ▌")
                        except Exception:
                            pass
                    last_len = len(buffer)

            if sent_msg is None:
                await update.message.reply_text(buffer or "I'm not sure how to respond to that.")
            else:
                try:
                    await sent_msg.edit_text(buffer)
                except Exception:
                    pass

        except Exception as e:
            print(f"[Telegram] Error for {uid}: {e}")
            await update.message.reply_text("Something went wrong. Please try again.")
