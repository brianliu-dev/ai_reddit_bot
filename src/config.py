"""Config + env loading for ai_reddit_bot."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_CFG = Path(os.environ.get("ARB_CONFIG", _ROOT / "config" / "subreddits.yaml"))


def _load_dotenv() -> None:
    """Load `.env` from the repo root into the environment for local runs.

    Dependency-free, and uses setdefault so anything already set (e.g. Docker's
    `env_file`, or an explicit shell export) always wins over the file.
    """
    env_path = _ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


_load_dotenv()


def load_config() -> dict:
    with open(_CFG, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# --- Reddit (PRAW, OAuth script app) ---
REDDIT_CLIENT_ID = os.environ.get("REDDIT_CLIENT_ID", "")
REDDIT_CLIENT_SECRET = os.environ.get("REDDIT_CLIENT_SECRET", "")
REDDIT_USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "ai_reddit_bot/0.1 by u/yourname")

# --- my_intern router (does the distillation) ---
# Uses the OpenAI-compatible ingress for simple, uniform response parsing.
MI_BASE_URL = os.environ.get("MI_BASE_URL", "http://host.docker.internal:8088").rstrip("/")
MI_API_KEY = os.environ.get("MI_API_KEY", "")
# Optional explicit model override sent to the router (else it auto-routes).
# A long, hard summarization is a good fit for a strong tier — pin it if you like:
#   MI_MODEL=anthropic/claude-opus-4-8
MI_MODEL = os.environ.get("MI_MODEL", "")

# --- Output ---
OBSIDIAN_VAULT_DIR = os.environ.get("OBSIDIAN_VAULT_DIR", "")  # where to write the note
DATA_DIR = Path(os.environ.get("ARB_DATA_DIR", _ROOT / "data" / "raw"))

# --- Telegram ---
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
