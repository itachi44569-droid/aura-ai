"""
WhatsApp channel — Meta Cloud API webhook (free tier: 1000 conversations/month).

Setup (one-time, free):
  1. Go to https://developers.facebook.com → Create App → Business
  2. Add "WhatsApp" product → create test phone number (or use your own)
  3. Get: WHATSAPP_TOKEN (access token), WHATSAPP_PHONE_ID (phone number ID), WHATSAPP_VERIFY_TOKEN (any string you set)
  4. Set webhook URL to: https://yourdomain.com/whatsapp/webhook
  5. Subscribe to: messages webhook field
  6. Add to .env: WHATSAPP_TOKEN, WHATSAPP_PHONE_ID, WHATSAPP_VERIFY_TOKEN

This module adds /whatsapp/webhook endpoints to an existing FastAPI app.
"""
import os
import hashlib
import hmac
import json
import asyncio
import aiohttp
from fastapi import APIRouter, Request, HTTPException, Query
from fastapi.responses import PlainTextResponse

from core.brain import Brain

WHATSAPP_TOKEN        = os.getenv("WHATSAPP_TOKEN", "")
WHATSAPP_PHONE_ID     = os.getenv("WHATSAPP_PHONE_ID", "")
WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "my-secret-verify-token")
WHATSAPP_API_URL      = f"https://graph.facebook.com/v20.0/{WHATSAPP_PHONE_ID}/messages"


async def _send_whatsapp(to: str, text: str):
    """Send a WhatsApp text message via Meta Cloud API."""
    if not WHATSAPP_TOKEN or not WHATSAPP_PHONE_ID:
        print("[WhatsApp] Missing credentials — message not sent.")
        return
    # Split long messages (WhatsApp limit: ~4096 chars)
    chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type":  "application/json",
    }
    async with aiohttp.ClientSession() as s:
        for chunk in chunks:
            payload = {
                "messaging_product": "whatsapp",
                "to":                to,
                "type":              "text",
                "text":              {"body": chunk},
            }
            async with s.post(WHATSAPP_API_URL, json=payload, headers=headers) as r:
                if r.status not in (200, 201):
                    body = await r.text()
                    print(f"[WhatsApp] Send failed {r.status}: {body}")


def build_whatsapp_router(brain: Brain) -> APIRouter:
    router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])

    @router.get("/webhook")
    async def verify_webhook(
        hub_mode:         str = Query(alias="hub.mode",          default=""),
        hub_challenge:    str = Query(alias="hub.challenge",     default=""),
        hub_verify_token: str = Query(alias="hub.verify_token",  default=""),
    ):
        """Meta webhook verification handshake."""
        if hub_mode == "subscribe" and hub_verify_token == WHATSAPP_VERIFY_TOKEN:
            return PlainTextResponse(hub_challenge)
        raise HTTPException(status_code=403, detail="Verification failed")

    @router.post("/webhook")
    async def receive_message(request: Request):
        """Receive and process incoming WhatsApp messages."""
        data = await request.json()

        try:
            entry   = data["entry"][0]
            changes = entry["changes"][0]
            value   = changes["value"]

            # Ignore status updates (delivery receipts etc.)
            if "statuses" in value and "messages" not in value:
                return {"status": "ok"}

            messages = value.get("messages", [])
            for message in messages:
                msg_type = message.get("type","")
                from_num = message.get("from","")   # WhatsApp number (E.164 format)

                if msg_type == "text":
                    text = message["text"]["body"]
                    asyncio.create_task(_handle_text(brain, from_num, text))

                elif msg_type == "image":
                    # Image handling — download and analyze
                    media_id = message["image"]["id"]
                    caption  = message["image"].get("caption","")
                    asyncio.create_task(_handle_image(brain, from_num, media_id, caption))

                elif msg_type == "audio":
                    # Voice note transcription
                    media_id = message["audio"]["id"]
                    asyncio.create_task(_handle_voice(brain, from_num, media_id))

                elif msg_type == "interactive":
                    # Button or list reply
                    reply_id = (message.get("interactive",{}).get("button_reply",{}).get("id")
                                or message.get("interactive",{}).get("list_reply",{}).get("id",""))
                    asyncio.create_task(_handle_text(brain, from_num, reply_id))

        except (KeyError, IndexError) as e:
            print(f"[WhatsApp] Parse error: {e} | data: {data}")

        return {"status": "ok"}

    return router


async def _handle_text(brain: Brain, user_id: str, text: str):
    try:
        response = await brain.think(user_id, text, channel="whatsapp")
        await _send_whatsapp(user_id, response)
    except Exception as e:
        print(f"[WhatsApp] Text handler error: {e}")
        await _send_whatsapp(user_id, "Sorry, I encountered an error. Please try again.")


async def _handle_image(brain: Brain, user_id: str, media_id: str, caption: str = ""):
    """Download image from Meta CDN and analyze it."""
    try:
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        async with aiohttp.ClientSession() as s:
            # Get media URL
            async with s.get(f"https://graph.facebook.com/v20.0/{media_id}", headers=headers) as r:
                meta = await r.json()
            media_url = meta.get("url","")
            if not media_url:
                await _send_whatsapp(user_id, "Couldn't retrieve the image.")
                return
            # Download image bytes
            async with s.get(media_url, headers=headers) as r:
                img_bytes = await r.read()
        response = await brain.analyze_image(user_id, img_bytes, caption or None, "whatsapp")
        await _send_whatsapp(user_id, response)
    except Exception as e:
        print(f"[WhatsApp] Image handler error: {e}")
        await _send_whatsapp(user_id, "Sorry, I couldn't analyze that image.")


async def _handle_voice(brain: Brain, user_id: str, media_id: str):
    """Download voice note and transcribe + respond."""
    try:
        headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
        async with aiohttp.ClientSession() as s:
            async with s.get(f"https://graph.facebook.com/v20.0/{media_id}", headers=headers) as r:
                meta = await r.json()
            media_url = meta.get("url","")
            if not media_url:
                await _send_whatsapp(user_id, "Couldn't retrieve the voice message.")
                return
            async with s.get(media_url, headers=headers) as r:
                audio_bytes = await r.read()
        transcript, response = await brain.transcribe_and_respond(
            user_id, audio_bytes, "audio.ogg", "whatsapp"
        )
        if transcript:
            await _send_whatsapp(user_id, f"You said: {transcript}\n\n{response}")
        else:
            await _send_whatsapp(user_id, response)
    except Exception as e:
        print(f"[WhatsApp] Voice handler error: {e}")
        await _send_whatsapp(user_id, "Sorry, I couldn't process that voice message.")
