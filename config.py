import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def load_client(name: str = "default") -> dict:
    """Load a client personality config from clients/<name>.yaml"""
    path = Path(__file__).parent / "clients" / f"{name}.yaml"
    if not path.exists():
        path = Path(__file__).parent / "clients" / "default.yaml"
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)

# ── Environment variables ──────────────────────────────────────────────────────

GROQ_API_KEY   = os.getenv("GROQ_API_KEY",   "")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
WEB_PORT       = int(os.getenv("WEB_PORT",   "8000"))
DB_PATH        = os.getenv("DB_PATH",        "brain.db")
CHROMA_PATH    = os.getenv("CHROMA_PATH",    "./chroma_db")
CLIENT_NAME    = os.getenv("CLIENT_NAME",    "default")
ENABLE_RAG     = os.getenv("ENABLE_RAG",     "true").lower() == "true"
ENABLE_WEB     = os.getenv("ENABLE_WEB",     "false").lower() == "true"
ENABLE_TG      = os.getenv("ENABLE_TG",      "true").lower() == "true"
