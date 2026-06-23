"""
Web API channel — FastAPI.
Endpoints:
  POST /chat              — send a message (optional api_key for paid tier)
  POST /ingest/text|url|file — add knowledge
  GET  /sources           — list knowledge sources
  GET  /health, /stats    — status
  WS   /ws/{user_id}      — streaming
  GET  /admin             — admin panel (password protected)
  POST /admin/keys/generate
  POST /admin/keys/revoke
  GET  /admin/keys
"""
import os
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from core.brain import Brain
from core.rag   import RAG
from core.keys  import (
    generate_key, validate_key, check_key_limit,
    increment_key_usage, list_keys, revoke_key, init_keys_table,
)

STATIC_DIR   = Path(__file__).parent / "static"
ADMIN_SECRET = os.getenv("ADMIN_PASSWORD", "admin123")


def _check_admin(pw: str | None):
    if pw != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Wrong admin password")


def build_app(brain: Brain, rag: RAG = None) -> FastAPI:
    init_keys_table()

    app = FastAPI(
        title       = brain.personality.get("name", "AI Brain"),
        description = brain.personality.get("description", "The core AI engine"),
        version     = "1.0.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_credentials=True,
        allow_methods=["*"], allow_headers=["*"],
    )

    # ── Models ────────────────────────────────────────────────────────────────

    class ChatRequest(BaseModel):
        user_id:  str
        message:  str
        stream:   bool = False
        api_key:  Optional[str] = None

    class ChatResponse(BaseModel):
        user_id:  str
        message:  str
        response: str
        tier:     str = "free"

    class IngestTextRequest(BaseModel):
        text:      str
        source:    str = "manual"
        client_id: str = "default"

    class IngestUrlRequest(BaseModel):
        url:       str
        client_id: str = "default"

    class GenKeyRequest(BaseModel):
        label:       str
        daily_limit: int = 0

    class RevokeKeyRequest(BaseModel):
        key: str

    # ── Static files ──────────────────────────────────────────────────────────

    @app.get("/")
    async def root():
        f = STATIC_DIR / "index.html"
        return FileResponse(f) if f.exists() else {"status": "online"}

    @app.get("/admin")
    async def admin_page():
        f = STATIC_DIR / "admin.html"
        return FileResponse(f) if f.exists() else {"error": "admin page not found"}

    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ── Health & Stats ────────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "ok", "model": "llama-3.3-70b-versatile", "tools": len(brain.tools)}

    @app.get("/stats")
    async def stats():
        s = brain.get_stats()
        return {**s, "tools_enabled": [t.name for t in brain.tools], "rag_enabled": rag is not None}

    # ── Chat ──────────────────────────────────────────────────────────────────

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest):
        tier = "free"
        if req.api_key:
            key_info = validate_key(req.api_key)
            if not key_info:
                raise HTTPException(status_code=401, detail="Invalid or revoked API key.")
            ok, reason = check_key_limit(req.api_key, key_info["daily_limit"])
            if not ok:
                raise HTTPException(status_code=429, detail=reason)
            increment_key_usage(req.api_key)
            tier = "paid"
        try:
            response = await brain.think(req.user_id, req.message)
            return ChatResponse(user_id=req.user_id, message=req.message, response=response, tier=tier)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.websocket("/ws/{user_id}")
    async def websocket_chat(ws: WebSocket, user_id: str):
        await ws.accept()
        try:
            while True:
                user_msg = await ws.receive_text()
                async for chunk in brain.think_stream(user_id, user_msg):
                    await ws.send_text(chunk)
                await ws.send_text("\n[DONE]")
        except Exception:
            await ws.close()

    # ── Knowledge base ────────────────────────────────────────────────────────

    @app.post("/ingest/text")
    async def ingest_text(req: IngestTextRequest):
        if not rag:
            raise HTTPException(status_code=400, detail="RAG not enabled")
        count = rag.ingest_text(req.text, source=req.source, client_id=req.client_id)
        return {"chunks_ingested": count, "source": req.source}

    @app.post("/ingest/url")
    async def ingest_url(req: IngestUrlRequest):
        if not rag:
            raise HTTPException(status_code=400, detail="RAG not enabled")
        try:
            count = rag.ingest_url(req.url, client_id=req.client_id)
            return {"chunks_ingested": count, "url": req.url}
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @app.post("/ingest/file")
    async def ingest_file(file: UploadFile = File(...), client_id: str = "default"):
        if not rag:
            raise HTTPException(status_code=400, detail="RAG not enabled")
        ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename else ""
        if ext not in ("txt", "md", "pdf", "json"):
            raise HTTPException(status_code=400, detail=f"Unsupported type: {ext}")
        path = f"/tmp/{file.filename}"
        with open(path, "wb") as f:
            f.write(await file.read())
        try:
            count = rag.ingest_file(path, client_id=client_id)
            return {"chunks_ingested": count, "filename": file.filename}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/sources")
    async def list_sources(client_id: str = "default"):
        if not rag:
            return {"sources": []}
        return {"sources": rag.list_sources(client_id)}

    @app.delete("/knowledge")
    async def clear_knowledge(client_id: str = "default"):
        if not rag:
            raise HTTPException(status_code=400, detail="RAG not enabled")
        rag.clear(client_id)
        return {"status": "cleared"}

    # ── Admin endpoints ───────────────────────────────────────────────────────

    @app.get("/admin/stats")
    async def admin_stats(x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password)
        s = brain.get_stats()
        return {**s, "tools_enabled": [t.name for t in brain.tools], "rag_enabled": rag is not None}

    @app.get("/admin/keys")
    async def admin_list_keys(x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password)
        return {"keys": list_keys()}

    @app.post("/admin/keys/generate")
    async def admin_gen_key(req: GenKeyRequest, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password)
        key = generate_key(label=req.label, daily_limit=req.daily_limit)
        return {"key": key, "label": req.label, "daily_limit": req.daily_limit}

    @app.post("/admin/keys/revoke")
    async def admin_revoke_key(req: RevokeKeyRequest, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password)
        revoke_key(req.key)
        return {"status": "revoked", "key": req.key}

    # ── Debug (remove in production) ──────────────────────────────────────────

    @app.get("/debug/groq")
    async def debug_groq():
        from groq import AsyncGroq
        key = os.getenv("GROQ_API_KEY", "")
        result = {"key_present": bool(key), "key_length": len(key)}
        try:
            client = AsyncGroq(api_key=key)
            resp = await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": "Say hi"}],
                max_tokens=10, temperature=0.0,
            )
            result["groq_status"] = "ok"
            result["groq_response"] = resp.choices[0].message.content
        except Exception as e:
            result["groq_status"] = "error"
            result["groq_error"] = str(e)
        return result

    # Mount WhatsApp if configured
    if os.getenv("WHATSAPP_TOKEN"):
        from channels.whatsapp_channel import build_whatsapp_router
        app.include_router(build_whatsapp_router(brain))
        print("[Web] WhatsApp webhook mounted at /whatsapp/webhook")

    return app
