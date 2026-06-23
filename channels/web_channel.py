"""
Web API channel — FastAPI.
Endpoints:
  POST /chat           — send a message, get a response
  POST /ingest/text    — ingest raw text into knowledge base
  POST /ingest/file    — upload a file (.txt, .pdf, .json, .md)
  POST /ingest/url     — ingest from a URL
  GET  /sources        — list ingested sources
  GET  /health         — health check
  GET  /stats          — usage statistics
  WS   /ws/{user_id}   — websocket for streaming responses
"""
import os
from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional

from core.brain import Brain
from core.rag   import RAG


def build_app(brain: Brain, rag: RAG = None) -> FastAPI:
    app = FastAPI(
        title       = brain.personality.get("name", "AI Brain"),
        description = brain.personality.get("description", "The core AI engine"),
        version     = "1.0.0",
    )

    # Allow all origins (set this to specific domains in production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins     = ["*"],
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    # ── Request / Response models ──────────────────────────────────────────────

    class ChatRequest(BaseModel):
        user_id:  str
        message:  str
        stream:   bool = False

    class ChatResponse(BaseModel):
        user_id:   str
        message:   str
        response:  str

    class IngestTextRequest(BaseModel):
        text:      str
        source:    str = "manual"
        client_id: str = "default"

    class IngestUrlRequest(BaseModel):
        url:       str
        client_id: str = "default"

    # ── Endpoints ──────────────────────────────────────────────────────────────

    @app.get("/health")
    async def health():
        return {"status": "ok", "model": "llama-3.3-70b-versatile", "tools": len(brain.tools)}

    @app.get("/debug/groq")
    async def debug_groq():
        """Test Groq API directly — returns the error message if it fails."""
        import os
        from groq import AsyncGroq
        key = os.getenv("GROQ_API_KEY", "")
        result = {
            "key_present": bool(key),
            "key_length": len(key),
            "key_prefix": key[:8] if key else "",
        }
        try:
            client = AsyncGroq(api_key=key)
            resp = await client.chat.completions.create(
                model="llama-3.1-8b-instant",
                messages=[{"role": "user", "content": "Say hi"}],
                max_tokens=10,
                temperature=0.0,
            )
            result["groq_status"] = "ok"
            result["groq_response"] = resp.choices[0].message.content
        except Exception as e:
            result["groq_status"] = "error"
            result["groq_error_type"] = type(e).__name__
            result["groq_error"] = str(e)
        return result

    @app.get("/stats")
    async def stats():
        summary = brain.get_stats()
        return {
            **summary,
            "tools_enabled": [t.name for t in brain.tools],
            "rag_enabled":   rag is not None,
        }

    @app.post("/chat", response_model=ChatResponse)
    async def chat(req: ChatRequest):
        try:
            response = await brain.think(req.user_id, req.message)
            return ChatResponse(user_id=req.user_id, message=req.message, response=response)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.websocket("/ws/{user_id}")
    async def websocket_chat(ws: WebSocket, user_id: str):
        """WebSocket endpoint — streams response chunks to the client."""
        await ws.accept()
        try:
            while True:
                user_msg = await ws.receive_text()
                async for chunk in brain.think_stream(user_id, user_msg):
                    await ws.send_text(chunk)
                await ws.send_text("\n[DONE]")
        except Exception:
            await ws.close()

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
    async def ingest_file(
        file:      UploadFile = File(...),
        client_id: str = "default",
    ):
        if not rag:
            raise HTTPException(status_code=400, detail="RAG not enabled")
        ext = file.filename.rsplit(".", 1)[-1].lower() if file.filename else ""
        if ext not in ("txt","md","pdf","json"):
            raise HTTPException(status_code=400, detail=f"Unsupported type: {ext}")
        path = f"/tmp/{file.filename}"
        content = await file.read()
        with open(path, "wb") as f:
            f.write(content)
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
        return {"status": "cleared", "client_id": client_id}

    # Mount WhatsApp webhook if credentials are set
    whatsapp_token = os.getenv("WHATSAPP_TOKEN","")
    if whatsapp_token:
        from channels.whatsapp_channel import build_whatsapp_router
        app.include_router(build_whatsapp_router(brain))
        print("[Web] WhatsApp webhook mounted at /whatsapp/webhook")

    return app
