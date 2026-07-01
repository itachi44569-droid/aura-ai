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
  GET  /admin/analytics   — enhanced analytics + daily charts
  GET  /admin/settings    — get app settings
  POST /admin/settings    — update app settings
  GET  /admin/users       — list registered users
  POST /admin/users       — create user (admin)
  DELETE /admin/users/{id} — deactivate user
  POST /admin/users/{id}/activate — re-activate user
  POST /auth/register     — register new user
  POST /auth/login        — login, returns session token
  POST /auth/logout       — invalidate session token
  GET  /auth/me           — validate token, get user info
  GET  /config            — public app config (branding)
  GET  /login             — login page
"""
import os
import re
import time
import asyncio
import logging
from collections import defaultdict
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException, WebSocket, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

from core.brain import Brain
from core.rag   import RAG
from core.keys  import (
    generate_key, validate_key, check_key_limit,
    increment_key_usage, list_keys, revoke_key, init_keys_table,
)
from core.auth import (
    init_auth, register_user, login_user, validate_token, delete_session,
    list_users, deactivate_user, activate_user, get_settings, update_settings,
    save_conversation, list_conversations, get_conversation, delete_conversation,
    share_conversation, get_shared_conversation,
)

STATIC_DIR   = Path(__file__).parent / "static"
ADMIN_SECRET = os.getenv("ADMIN_PASSWORD", "admin123")

# ── Security config ───────────────────────────────────────────────────────────
_log = logging.getLogger("security")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

CHAT_RATE_LIMIT    = int(os.getenv("CHAT_RATE_LIMIT", "25"))   # requests per hour per IP
ADMIN_MAX_FAILS    = 5
ADMIN_LOCKOUT_SECS = 15 * 60  # 15 minutes
MAX_MESSAGE_LEN    = 2000

# In-memory stores (reset on restart — fine for single Railway instance)
_chat_hits: dict[str, list[float]]        = defaultdict(list)  # IP -> [timestamps]
_admin_fails: dict[str, tuple[int, float]] = {}                 # IP -> (count, first_fail_ts)
_login_fails: dict[str, tuple[int, float]] = {}                 # IP -> (count, first_fail_ts)


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_check_chat(ip: str) -> bool:
    now = time.time()
    window_start = now - 3600
    _chat_hits[ip] = [t for t in _chat_hits[ip] if t > window_start]
    if len(_chat_hits[ip]) >= CHAT_RATE_LIMIT:
        return False
    _chat_hits[ip].append(now)
    return True


def _lockout_check(store: dict, ip: str) -> bool:
    if ip not in store:
        return True
    count, first = store[ip]
    if count >= ADMIN_MAX_FAILS:
        if time.time() - first < ADMIN_LOCKOUT_SECS:
            return False
        del store[ip]
    return True


def _lockout_fail(store: dict, ip: str):
    if ip not in store:
        store[ip] = (1, time.time())
    else:
        count, first = store[ip]
        store[ip] = (count + 1, first)


def _lockout_mins_left(store: dict, ip: str) -> int:
    count, first = store.get(ip, (0, 0))
    return max(1, int((ADMIN_LOCKOUT_SECS - (time.time() - first)) / 60) + 1)


def _sanitize_message(text: str) -> str:
    text = re.sub(r'<[^>]*>', '', text)  # strip HTML tags
    return text[:MAX_MESSAGE_LEN]


def _check_admin(pw: str | None, request: Request):
    ip = _client_ip(request)
    if not _lockout_check(_admin_fails, ip):
        mins = _lockout_mins_left(_admin_fails, ip)
        _log.warning(f"[admin-lockout] IP={ip} locked out ({mins}m remaining)")
        raise HTTPException(status_code=429, detail=f"Too many failed attempts. Try again in {mins} minutes.")
    if pw != ADMIN_SECRET:
        _lockout_fail(_admin_fails, ip)
        count = _admin_fails.get(ip, (0,))[0]
        _log.warning(f"[admin-fail] IP={ip} wrong password ({count}/{ADMIN_MAX_FAILS})")
        raise HTTPException(status_code=401, detail="Wrong admin password")
    _admin_fails.pop(ip, None)  # clear on success


class _SecurityHeaders(BaseHTTPMiddleware):
    """Add standard security headers to every response."""
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault("X-XSS-Protection", "1; mode=block")
        return response


def build_app(brain: Brain, rag: RAG = None) -> FastAPI:
    init_keys_table()
    init_auth()

    # Apply saved persona_addon to brain on startup
    _s = get_settings()
    brain.system_addon = _s.get("persona_addon", "")

    app = FastAPI(
        title       = brain.personality.get("name", "AI Brain"),
        description = brain.personality.get("description", "The core AI engine"),
        version     = "1.0.0",
    )

    # CORS — the /chat endpoint is meant to be embedded on arbitrary customer
    # websites (widget product), so it must accept any origin. It carries no
    # cookies/credentials (client_id + user_id are passed explicitly in the
    # request body), so a wildcard origin is safe here.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"], allow_credentials=False,
        allow_methods=["*"], allow_headers=["*"],
    )
    app.add_middleware(_SecurityHeaders)

    # ── Keepalive ping — prevents Railway/Render cold starts ──────────────────
    @app.on_event("startup")
    async def _start_keepalive():
        _domain = os.getenv("RAILWAY_DOMAIN", "") or os.getenv("VERCEL_URL", "")
        _port   = os.getenv("PORT", "8000")
        _base   = f"https://{_domain}" if _domain else f"http://localhost:{_port}"

        async def _ping_loop():
            import aiohttp
            await asyncio.sleep(60)  # wait 1 minute after startup before first ping
            while True:
                try:
                    async with aiohttp.ClientSession() as s:
                        async with s.get(f"{_base}/health",
                                         timeout=aiohttp.ClientTimeout(total=10)) as r:
                            _log.info(f"[keepalive] ping OK ({r.status})")
                except Exception as exc:
                    _log.debug(f"[keepalive] ping failed: {exc}")
                await asyncio.sleep(270)  # every 4.5 minutes (< 5-minute timeout)

        asyncio.create_task(_ping_loop())

    # ── Models ────────────────────────────────────────────────────────────────

    class ChatRequest(BaseModel):
        user_id:  str
        message:  str
        stream:   bool = False
        api_key:  Optional[str] = None
        client_id: Optional[str] = None
        business_persona: Optional[str] = None

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

    class RegisterRequest(BaseModel):
        email:    str
        password: str
        name:     str = ""

    class LoginRequest(BaseModel):
        email:    str
        password: str

    class LogoutRequest(BaseModel):
        token: str

    class SettingsUpdateRequest(BaseModel):
        bot_name:      Optional[str] = None
        logo_emoji:    Optional[str] = None
        primary_color: Optional[str] = None
        greeting:      Optional[str] = None
        persona_addon: Optional[str] = None
        temperature:   Optional[str] = None
        require_login: Optional[str] = None

    class CreateUserRequest(BaseModel):
        email:    str
        password: str
        name:     str = ""

    class SaveConvRequest(BaseModel):
        conv_id:  str
        title:    str
        messages: list

    # ── Static files ──────────────────────────────────────────────────────────

    @app.get("/")
    async def root():
        f = STATIC_DIR / "landing.html"
        if f.exists():
            return FileResponse(f)
        f2 = STATIC_DIR / "index.html"
        return FileResponse(f2) if f2.exists() else {"status": "online"}

    @app.get("/app")
    async def app_page():
        f = STATIC_DIR / "index.html"
        return FileResponse(f) if f.exists() else {"status": "online"}

    @app.get("/admin")
    async def admin_page():
        f = STATIC_DIR / "admin.html"
        return FileResponse(f) if f.exists() else {"error": "admin page not found"}

    @app.get("/login")
    async def login_page():
        f = STATIC_DIR / "login.html"
        return FileResponse(f) if f.exists() else {"error": "login page not found"}

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
    async def chat(req: ChatRequest, request: Request):
        ip = _client_ip(request)
        if not _rate_check_chat(ip):
            _log.warning(f"[chat-ratelimit] IP={ip} exceeded {CHAT_RATE_LIMIT}/hour")
            raise HTTPException(status_code=429, detail=f"Rate limit exceeded — max {CHAT_RATE_LIMIT} messages per hour.")
        req.message = _sanitize_message(req.message)
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
            response = await brain.think(
                req.user_id, req.message,
                client_id=req.client_id,
                business_persona=req.business_persona or "",
            )
            return ChatResponse(user_id=req.user_id, message=req.message, response=response, tier=tier)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.websocket("/ws/{user_id}")
    async def websocket_chat(ws: WebSocket, user_id: str, model: str = "deep"):
        await ws.accept()
        try:
            while True:
                user_msg = await ws.receive_text()
                async for chunk in brain.think_stream(user_id, user_msg, model_pref=model):
                    await ws.send_text(chunk)
                await ws.send_text("\n[DONE]")
        except Exception:
            await ws.close()

    # ── Image analysis ────────────────────────────────────────────────────────

    @app.post("/chat/image")
    async def chat_image(
        user_id: str,
        image: UploadFile = File(...),
        caption: str = "",
    ):
        """
        Accept a browser image upload, analyze with Groq vision (Llama 4 Scout).
        If the user added a caption/question, answer that about the image.
        Otherwise just describe what's visible — no code, no tangents.
        """
        ext = (image.filename or "image.jpg").rsplit(".", 1)[-1].lower()
        mime_map = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
                    "gif": "image/gif", "webp": "image/webp", "bmp": "image/bmp"}
        mime = mime_map.get(ext, "image/jpeg")
        if ext not in mime_map:
            raise HTTPException(status_code=400, detail="Unsupported image type. Use JPG, PNG, WEBP, or GIF.")
        try:
            image_bytes = await image.read()
            if len(image_bytes) > 10 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="Image too large — max 10 MB.")

            vision = brain.get_vision()
            if not vision:
                raise HTTPException(status_code=503, detail="Vision model unavailable.")

            # If user asked a specific question, answer only that.
            # Otherwise give a plain, conversational description — no code.
            if caption and caption.strip():
                prompt = caption.strip()
            else:
                prompt = (
                    "Look at this image and describe what you see in a friendly, "
                    "conversational way. Mention what's in it, any text visible, "
                    "colors, and anything interesting — but keep it short and natural. "
                    "Do NOT write code or suggest how to build anything."
                )

            response = await vision.analyze_bytes(image_bytes, mime, prompt)
            brain._analytics.log_image(user_id, "web")
            return {"response": response}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Document analysis ─────────────────────────────────────────────────────

    @app.post("/chat/document")
    async def chat_document(
        user_id: str,
        file: UploadFile = File(...),
        caption: str = "",
    ):
        """
        Accept PDF, TXT, DOCX, or MD files, extract text, and let Aura answer
        questions about the document. Truncates at 20 000 chars (~5 000 words).
        """
        filename = file.filename or "document"
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if ext not in ("pdf", "txt", "docx", "md"):
            raise HTTPException(
                status_code=400,
                detail="Unsupported file type. Upload PDF, TXT, DOCX, or MD."
            )
        try:
            content = await file.read()
            if len(content) > 10 * 1024 * 1024:
                raise HTTPException(status_code=400, detail="File too large — max 10 MB.")

            # ── Text extraction ───────────────────────────────────────────────
            text = ""
            if ext == "pdf":
                import io
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(content))
                for page in reader.pages:
                    text += (page.extract_text() or "") + "\n"
            elif ext in ("txt", "md"):
                text = content.decode("utf-8", errors="replace")
            elif ext == "docx":
                import io
                from docx import Document as DocxDocument
                doc = DocxDocument(io.BytesIO(content))
                text = "\n".join(p.text for p in doc.paragraphs if p.text.strip())

            text = text.strip()
            if not text:
                raise HTTPException(
                    status_code=422,
                    detail="Could not extract text from this file — it may be scanned or image-based."
                )

            # ── Truncate if very long ─────────────────────────────────────────
            MAX_CHARS = 20_000
            truncated = len(text) > MAX_CHARS
            if truncated:
                text = text[:MAX_CHARS]
            trunc_note = (
                f"\n\n[Note: document was long — only the first {MAX_CHARS:,} characters were loaded.]"
                if truncated else ""
            )

            # ── Build prompt ──────────────────────────────────────────────────
            if caption and caption.strip():
                user_msg = (
                    f"I've uploaded a document called '{filename}'. "
                    f"{caption.strip()}\n\nDocument content:\n\n{text}{trunc_note}"
                )
            else:
                user_msg = (
                    f"I've uploaded a document called '{filename}'. "
                    f"Please: 1) Tell me what this document is about in 2–3 sentences, "
                    f"2) List the key topics or sections it covers, "
                    f"3) Ask me what I'd like to know about it.\n\n"
                    f"Document content:\n\n{text}{trunc_note}"
                )

            response = await brain.think(user_id, user_msg)
            return {
                "filename": filename,
                "chars_extracted": len(text),
                "truncated": truncated,
                "response": response,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ── Voice transcription ───────────────────────────────────────────────────

    @app.post("/voice")
    async def voice_chat(
        user_id: str,
        audio: UploadFile = File(...),
    ):
        """
        Accept a browser audio blob (webm/wav/ogg), transcribe with Groq Whisper,
        then get Aura's response. Returns {transcript, response}.
        """
        try:
            audio_bytes = await audio.read()
            filename = audio.filename or "audio.webm"
            transcript, response = await brain.transcribe_and_respond(
                user_id=user_id,
                audio_bytes=audio_bytes,
                filename=filename,
                channel="web",
            )
            if not transcript:
                raise HTTPException(status_code=422, detail="Could not transcribe audio — try speaking louder or closer to the mic.")
            return {"transcript": transcript, "response": response}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

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

    # ── Public config (branding) ──────────────────────────────────────────────

    @app.get("/config")
    async def public_config():
        s = get_settings()
        return {
            "bot_name":      s.get("bot_name", "Aura AI"),
            "logo_emoji":    s.get("logo_emoji", "✦"),
            "primary_color": s.get("primary_color", "#4F46E5"),
            "greeting":      s.get("greeting", ""),
            "require_login": s.get("require_login", "false"),
        }

    # ── Auth endpoints ────────────────────────────────────────────────────────

    @app.post("/auth/register")
    async def auth_register(req: RegisterRequest):
        result = register_user(req.email, req.password, req.name)
        if not result["ok"]:
            raise HTTPException(status_code=409, detail=result.get("error", "Registration failed"))
        return result

    @app.post("/auth/login")
    async def auth_login(req: LoginRequest, request: Request):
        ip = _client_ip(request)
        if not _lockout_check(_login_fails, ip):
            mins = _lockout_mins_left(_login_fails, ip)
            _log.warning(f"[login-lockout] IP={ip} locked out ({mins}m remaining)")
            raise HTTPException(status_code=429, detail=f"Too many failed login attempts. Try again in {mins} minutes.")
        result = login_user(req.email, req.password)
        if not result["ok"]:
            _lockout_fail(_login_fails, ip)
            count = _login_fails.get(ip, (0,))[0]
            _log.warning(f"[login-fail] IP={ip} email={req.email} ({count}/{ADMIN_MAX_FAILS})")
            raise HTTPException(status_code=401, detail=result.get("error", "Login failed"))
        _login_fails.pop(ip, None)
        return result

    @app.post("/auth/logout")
    async def auth_logout(req: LogoutRequest):
        delete_session(req.token)
        return {"ok": True}

    @app.get("/auth/me")
    async def auth_me(authorization: Optional[str] = Header(None)):
        token = ""
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
        user = validate_token(token)
        if not user:
            raise HTTPException(status_code=401, detail="Invalid or expired token")
        return user

    # ── Admin endpoints ───────────────────────────────────────────────────────

    @app.get("/admin/stats")
    async def admin_stats(request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        s = brain.get_stats()
        return {**s, "tools_enabled": [t.name for t in brain.tools], "rag_enabled": rag is not None}

    @app.get("/admin/keys")
    async def admin_list_keys(request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        return {"keys": list_keys()}

    @app.post("/admin/keys/generate")
    async def admin_gen_key(req: GenKeyRequest, request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        key = generate_key(label=req.label, daily_limit=req.daily_limit)
        return {"key": key, "label": req.label, "daily_limit": req.daily_limit}

    @app.post("/admin/keys/revoke")
    async def admin_revoke_key(req: RevokeKeyRequest, request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        revoke_key(req.key)
        return {"status": "revoked", "key": req.key}

    @app.get("/admin/analytics")
    async def admin_analytics(request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        s = brain.get_stats()
        users = list_users()
        daily_msgs = brain._analytics.get_daily_counts("message", days=30)
        return {
            **s,
            "tools_enabled":          [t.name for t in brain.tools],
            "rag_enabled":            rag is not None,
            "total_registered_users": len(users),
            "active_registered_users": sum(1 for u in users if u["is_active"]),
            "daily_messages":         list(reversed(daily_msgs)),
        }

    @app.get("/admin/settings")
    async def admin_get_settings(request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        return get_settings()

    @app.post("/admin/settings")
    async def admin_update_settings(req: SettingsUpdateRequest, request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        try:
            raw = req.model_dump()
        except AttributeError:
            raw = req.dict()
        updates = {k: v for k, v in raw.items() if v is not None}
        if updates:
            update_settings(updates)
            if "persona_addon" in updates:
                brain.system_addon = updates["persona_addon"]
        return {"ok": True, "updated": list(updates.keys())}

    @app.get("/admin/users")
    async def admin_list_users(request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        return {"users": list_users()}

    @app.post("/admin/users")
    async def admin_create_user(req: CreateUserRequest, request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        result = register_user(req.email, req.password, req.name)
        if not result["ok"]:
            raise HTTPException(status_code=409, detail=result.get("error", "Failed"))
        return result

    @app.delete("/admin/users/{user_id}")
    async def admin_deactivate_user(user_id: int, request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        deactivate_user(user_id)
        return {"ok": True}

    @app.post("/admin/users/{user_id}/activate")
    async def admin_activate_user(user_id: int, request: Request, x_admin_password: Optional[str] = Header(None)):
        _check_admin(x_admin_password, request)
        activate_user(user_id)
        return {"ok": True}

    # ── Conversation history ──────────────────────────────────────────────────

    def _auth_user(authorization: str | None) -> dict:
        token = ""
        if authorization and authorization.startswith("Bearer "):
            token = authorization[7:]
        user = validate_token(token)
        if not user:
            raise HTTPException(status_code=401, detail="Not authenticated")
        return user

    @app.post("/history/save")
    async def history_save(req: SaveConvRequest, authorization: Optional[str] = Header(None)):
        user = _auth_user(authorization)
        result = save_conversation(user["id"], req.conv_id, req.title, req.messages)
        return result

    @app.get("/history")
    async def history_list(authorization: Optional[str] = Header(None)):
        user = _auth_user(authorization)
        return {"conversations": list_conversations(user["id"])}

    @app.get("/history/{conv_id}")
    async def history_get(conv_id: str, authorization: Optional[str] = Header(None)):
        user = _auth_user(authorization)
        conv = get_conversation(conv_id, user["id"])
        if not conv:
            raise HTTPException(status_code=404, detail="Conversation not found")
        return conv

    @app.delete("/history/{conv_id}")
    async def history_delete(conv_id: str, authorization: Optional[str] = Header(None)):
        user = _auth_user(authorization)
        delete_conversation(conv_id, user["id"])
        return {"ok": True}

    @app.post("/history/{conv_id}/share")
    async def history_share(conv_id: str, authorization: Optional[str] = Header(None)):
        user = _auth_user(authorization)
        result = share_conversation(conv_id, user["id"])
        if not result["ok"]:
            raise HTTPException(status_code=404, detail=result.get("error", "Not found"))
        return result

    @app.get("/s/{token}")
    async def shared_view(token: str):
        import json as _json
        conv = get_shared_conversation(token)
        if not conv:
            return HTMLResponse("<html><body style='font-family:sans-serif;text-align:center;padding:80px;background:#050816;color:#F8FAFC'><h2>Link not found</h2><br><a href='/' style='color:#818CF8'>Start a new chat →</a></body></html>", status_code=404)
        cfg = get_settings()
        bot_name = cfg.get("bot_name", "Aura AI")
        logo = cfg.get("logo_emoji", "✦")
        color = cfg.get("primary_color", "#4F46E5")
        msgs_json = _json.dumps(conv["messages"])
        title_safe = conv["title"].replace("<","&lt;").replace(">","&gt;")
        html = f"""<!DOCTYPE html>
<html lang="en"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title_safe} — {bot_name}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
<script src="https://cdnjs.cloudflare.com/ajax/libs/marked/9.1.6/marked.min.js"></script>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'Inter',sans-serif;background:#050816;color:#F8FAFC;min-height:100vh;padding:24px 16px 60px}}
.hdr{{max-width:720px;margin:0 auto 28px;display:flex;align-items:center;gap:12px;padding-bottom:16px;border-bottom:1px solid rgba(255,255,255,.08)}}
.logo-box{{width:38px;height:38px;border-radius:10px;background:linear-gradient(135deg,{color},{color}88);display:flex;align-items:center;justify-content:center;font-size:18px;flex-shrink:0}}
.hdr-title{{font-size:15px;font-weight:600}}
.hdr-sub{{font-size:11px;color:#64748B;margin-top:2px}}
.msgs{{max-width:720px;margin:0 auto;display:flex;flex-direction:column;gap:14px}}
.row{{display:flex;gap:10px;align-items:flex-start}}
.row.user{{flex-direction:row-reverse}}
.av{{width:28px;height:28px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:13px;flex-shrink:0;background:rgba(255,255,255,.06)}}
.bubble{{max-width:84%;padding:11px 15px;border-radius:14px;font-size:14px;line-height:1.65;background:rgba(255,255,255,.05);border:1px solid rgba(255,255,255,.07)}}
.row.user .bubble{{background:rgba(79,70,229,.18);border-color:rgba(79,70,229,.28);text-align:left}}
.bubble img{{max-width:100%;border-radius:10px;margin:6px 0;display:block}}
.bubble pre{{background:#0D1117;padding:12px;border-radius:8px;overflow-x:auto;font-size:12px;margin:8px 0;line-height:1.5}}
.bubble code:not(pre code){{background:rgba(79,70,229,.18);padding:2px 5px;border-radius:4px;font-size:12px}}
.cta{{text-align:center;margin:40px 0 0}}
.cta a{{display:inline-block;padding:12px 28px;border-radius:24px;background:linear-gradient(135deg,{color},{color}cc);color:#fff;text-decoration:none;font-weight:600;font-size:14px}}
</style></head><body>
<div class="hdr">
  <div class="logo-box">{logo}</div>
  <div><div class="hdr-title">{title_safe}</div><div class="hdr-sub">Shared conversation · {bot_name}</div></div>
</div>
<div class="msgs" id="msgs"></div>
<div class="cta"><a href="/">Start your own chat →</a></div>
<script>
const msgs = {msgs_json};
const el = document.getElementById('msgs');
msgs.forEach(m => {{
  const ur = document.createElement('div'); ur.className='row user';
  ur.innerHTML='<div class="av">👤</div><div class="bubble"></div>';
  ur.querySelector('.bubble').textContent = m.u || '';
  el.appendChild(ur);
  const ar = document.createElement('div'); ar.className='row';
  ar.innerHTML='<div class="av">{logo}</div><div class="bubble"></div>';
  ar.querySelector('.bubble').innerHTML = marked.parse(m.a||'');
  el.appendChild(ar);
}});
</script></body></html>"""
        return HTMLResponse(html)

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
