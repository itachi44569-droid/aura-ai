"""
Discord channel adapter — free Discord bot API (discord.py).

Setup (one-time, free):
  1. Go to https://discord.com/developers/applications
  2. Create new application → Bot → Reset Token → copy it
  3. Bot Permissions: Read Messages, Send Messages, Read Message History
  4. Under OAuth2 → URL Generator → check "bot" → copy invite URL → invite to your server
  5. Set DISCORD_TOKEN=your_token_here in .env

Features:
  - Responds to mentions (@bot) and DMs
  - Prefix commands: !ask, !clear, !stats, !help
  - Handles image attachments (vision analysis)
  - Streaming-style chunked responses for long answers
"""
import asyncio
import io
import os

try:
    import discord
    from discord.ext import commands
    _HAS_DISCORD = True
except ImportError:
    _HAS_DISCORD = False

from core.brain import Brain

DISCORD_TOKEN  = os.getenv("DISCORD_TOKEN", "")
COMMAND_PREFIX = os.getenv("DISCORD_PREFIX", "!")
MAX_MSG_LEN    = 1990  # Discord limit is 2000 chars


def _split_message(text: str) -> list[str]:
    """Split long text into Discord-safe chunks."""
    if len(text) <= MAX_MSG_LEN:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:MAX_MSG_LEN])
        text = text[MAX_MSG_LEN:]
    return chunks


class DiscordChannel:
    def __init__(self, brain: Brain):
        if not _HAS_DISCORD:
            raise ImportError("discord.py not installed. Run: pip install discord.py")
        self.brain = brain

    def build_bot(self) -> "commands.Bot":
        intents = discord.Intents.default()
        intents.message_content = True
        bot = commands.Bot(command_prefix=COMMAND_PREFIX, intents=intents, help_command=None)

        brain = self.brain

        @bot.event
        async def on_ready():
            p = brain.personality
            print(f"[Discord] Logged in as {bot.user} — personality: {p.get('name','AI')}")
            await bot.change_presence(activity=discord.Activity(
                type=discord.ActivityType.listening,
                name=f"{COMMAND_PREFIX}help | mention me!"
            ))

        @bot.event
        async def on_message(message: discord.Message):
            if message.author.bot:
                return
            # Respond to DMs and mentions, not every message
            is_dm      = isinstance(message.channel, discord.DMChannel)
            is_mention = bot.user in message.mentions

            if not is_dm and not is_mention:
                await bot.process_commands(message)
                return

            content = message.content
            # Strip the bot mention from the message
            if bot.user:
                content = content.replace(f"<@{bot.user.id}>", "").strip()
                content = content.replace(f"<@!{bot.user.id}>", "").strip()

            if not content and not message.attachments:
                await message.reply("Hi! How can I help you?")
                return

            uid = str(message.author.id)

            async with message.channel.typing():
                # Handle image attachments
                if message.attachments:
                    att = message.attachments[0]
                    if any(att.filename.lower().endswith(ext) for ext in [".jpg",".jpeg",".png",".webp",".gif"]):
                        try:
                            img_bytes = await att.read()
                            response  = await brain.analyze_image(uid, img_bytes, content or None, "discord")
                        except Exception as e:
                            response = f"I couldn't analyze that image: {e}"
                    else:
                        response = await brain.think(uid, content or f"[File: {att.filename}]", "discord")
                else:
                    response = await brain.think(uid, content, "discord")

            for chunk in _split_message(response):
                await message.reply(chunk)

            await bot.process_commands(message)

        @bot.command(name="help")
        async def help_cmd(ctx: commands.Context):
            p    = brain.personality
            text = (
                f"**{p.get('name','AI Assistant')}**\n"
                f"{p.get('description','')}\n\n"
                f"**How to use:**\n"
                f"• Mention me: @{bot.user.display_name if bot.user else 'Bot'} your question\n"
                f"• DM me directly\n"
                f"• Use `{COMMAND_PREFIX}ask` followed by your message\n\n"
                f"**Commands:**\n"
                f"`{COMMAND_PREFIX}ask [question]` — ask anything\n"
                f"`{COMMAND_PREFIX}clear` — reset your conversation\n"
                f"`{COMMAND_PREFIX}stats` — usage statistics\n"
                f"`{COMMAND_PREFIX}memory` — what I know about you\n\n"
                f"**Also:** send me an image and I'll analyze it!"
            )
            await ctx.reply(text)

        @bot.command(name="ask")
        async def ask_cmd(ctx: commands.Context, *, question: str):
            uid = str(ctx.author.id)
            async with ctx.typing():
                response = await brain.think(uid, question, "discord")
            for chunk in _split_message(response):
                await ctx.reply(chunk)

        @bot.command(name="clear")
        async def clear_cmd(ctx: commands.Context):
            uid = str(ctx.author.id)
            if uid in brain.memory._cache:
                brain.memory._cache[uid].clear()
            await ctx.reply("Memory cleared! Starting fresh.")

        @bot.command(name="stats")
        async def stats_cmd(ctx: commands.Context):
            s = brain.get_stats()
            text = (
                f"**Usage stats (last 7 days)**\n"
                f"Messages: {s['total_messages']}\n"
                f"Unique users: {s['unique_users']}\n"
                f"Avg response: {s['avg_latency_ms']}ms\n"
                f"Images analyzed: {s['images_analyzed']}\n"
                f"Voice notes: {s['voice_notes']}"
            )
            await ctx.reply(text)

        @bot.command(name="memory")
        async def memory_cmd(ctx: commands.Context):
            uid   = str(ctx.author.id)
            facts = brain.memory.get_user_facts(uid)
            if facts:
                await ctx.reply(f"**What I know about you:**\n{facts}")
            else:
                await ctx.reply("I don't have any stored facts about you yet.")

        return bot

    def run(self):
        if not DISCORD_TOKEN:
            print("[Discord] DISCORD_TOKEN not set — skipping Discord bot.")
            return
        bot = self.build_bot()
        print("[Discord] Starting bot...")
        bot.run(DISCORD_TOKEN, log_handler=None)

    async def run_async(self):
        """Run inside an existing event loop (e.g., alongside Telegram)."""
        if not DISCORD_TOKEN:
            print("[Discord] DISCORD_TOKEN not set — skipping.")
            return
        bot = self.build_bot()
        await bot.start(DISCORD_TOKEN)
