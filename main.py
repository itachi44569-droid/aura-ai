"""
AI Brain — Entry Point

Usage:
  python main.py                                    # Telegram bot (default)
  CLIENT_NAME=finance python main.py               # Finance personality
  ENABLE_WEB=true python main.py                   # + Web API on port 8000
  ENABLE_DISCORD=true python main.py               # + Discord bot
  CLIENT_NAME=business ENABLE_RAG=true python main.py  # Business bot + knowledge base

Environment variables (.env):
  GROQ_API_KEY          = your Groq key (free at console.groq.com)
  TELEGRAM_TOKEN        = your bot token from @BotFather
  DISCORD_TOKEN         = your Discord bot token (optional)
  WHATSAPP_TOKEN        = Meta Cloud API token (optional, for WhatsApp)
  WHATSAPP_PHONE_ID     = Meta phone number ID
  WHATSAPP_VERIFY_TOKEN = Webhook verify token (any string you choose)
  CLIENT_NAME           = default | finance | business
  ENABLE_WEB            = true | false (run FastAPI)
  ENABLE_TG             = true | false (run Telegram bot)
  ENABLE_DISCORD        = true | false (run Discord bot)
  ENABLE_RAG            = true | false (enable ChromaDB knowledge base)
  ENABLE_REFLECTION     = true | false (self-critique loop, doubles quality)
  RATE_MESSAGES_PER_MIN = 20 (rate limit per user)
  RATE_MESSAGES_PER_DAY = 200
  WEB_PORT              = 8000
  DB_PATH               = brain.db
  CHROMA_PATH           = ./chroma_db
  TIMEZONE              = Asia/Kolkata
"""
import asyncio
import threading
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from config import (
    GROQ_API_KEY, TELEGRAM_TOKEN, WEB_PORT,
    DB_PATH, CHROMA_PATH, CLIENT_NAME,
    ENABLE_RAG, ENABLE_WEB, ENABLE_TG,
    load_client,
)
from core.brain   import Brain
from core.memory  import Memory
from core.rag     import RAG
from core.tools   import get_tools

ENABLE_DISCORD = os.getenv("ENABLE_DISCORD", "false").lower() == "true"


def validate_env():
    missing = []
    if not GROQ_API_KEY:
        missing.append("GROQ_API_KEY")
    if ENABLE_TG and not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if missing:
        print(f"[ERROR] Missing environment variables: {', '.join(missing)}")
        print("Create a .env file — see .env.example")
        sys.exit(1)


def build_brain() -> tuple[Brain, RAG | None]:
    personality = load_client(CLIENT_NAME)
    memory      = Memory(db_path=DB_PATH)
    rag         = RAG(persist_dir=CHROMA_PATH) if ENABLE_RAG else None
    tool_names  = personality.get("tools", None)
    tools       = get_tools(tool_names)
    brain = Brain(
        personality = personality,
        memory      = memory,
        rag         = rag,
        tools       = tools,
        client_id   = CLIENT_NAME,
    )
    return brain, rag


def run_web(brain: Brain, rag: RAG):
    import uvicorn
    from channels.web_channel import build_app
    app = build_app(brain, rag)
    print(f"[Web] API at http://localhost:{WEB_PORT}")
    print(f"[Web] Docs at http://localhost:{WEB_PORT}/docs")
    uvicorn.run(app, host="0.0.0.0", port=WEB_PORT, log_level="warning")


def run_telegram(brain: Brain, rag: RAG):
    from channels.telegram_channel import TelegramChannel
    channel = TelegramChannel(brain=brain, rag=rag)
    print(f"[Telegram] Starting — personality: {brain.personality.get('name')}")
    print(f"[Telegram] Tools: {[t.name for t in brain.tools]}")
    print(f"[Telegram] Features: RAG={'on' if rag else 'off'} | Vision=on | Voice=on | Analytics=on")
    channel.run()


async def run_discord_async(brain: Brain):
    from channels.discord_channel import DiscordChannel
    channel = DiscordChannel(brain=brain)
    await channel.run_async()


def main():
    validate_env()
    brain, rag = build_brain()

    p = brain.personality
    print(f"\n{'='*55}")
    print(f"  AI BRAIN — {p.get('name','AI Assistant')}")
    print(f"  {p.get('description','')}")
    print(f"  Channels: {'Telegram ' if ENABLE_TG else ''}{'Web ' if ENABLE_WEB else ''}{'Discord' if ENABLE_DISCORD else ''}")
    print(f"{'='*55}\n")

    if ENABLE_WEB:
        web_thread = threading.Thread(target=run_web, args=(brain, rag), daemon=True)
        web_thread.start()

    if ENABLE_DISCORD:
        # Discord runs async — need to integrate with Telegram's event loop or run separately
        discord_thread = threading.Thread(
            target=lambda: asyncio.run(run_discord_async(brain)),
            daemon=True,
        )
        discord_thread.start()
        print("[Discord] Bot starting in background thread...")

    if ENABLE_TG:
        run_telegram(brain, rag)  # blocking — runs until Ctrl+C
    elif ENABLE_WEB:
        print("[Main] Web-only mode. Press Ctrl+C to stop.")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[Main] Shutting down.")
    elif ENABLE_DISCORD:
        print("[Main] Discord-only mode. Press Ctrl+C to stop.")
        try:
            import time
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n[Main] Shutting down.")
    else:
        print("[Main] No channels enabled. Set ENABLE_TG=true, ENABLE_WEB=true, or ENABLE_DISCORD=true.")


if __name__ == "__main__":
    main()
