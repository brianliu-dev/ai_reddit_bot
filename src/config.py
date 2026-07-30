"""Config + env loading for ai_reddit_bot."""
from __future__ import annotations

import os
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_CFG = Path(os.environ.get("ARB_CONFIG", _ROOT / "config" / "profiles.yaml"))


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


def _deep_merge(base: dict, override: dict) -> dict:
    """Override wins key-by-key; nested dicts merge rather than replace.

    This is what lets a profile say `pull: {posts_per_sub: 20}` without restating the
    other eleven pull knobs — the whole point of defaults+profiles. Replace-semantics
    would mean every new topic copies the full tuning block and then drifts from it.
    """
    out = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(profile: str | None = None) -> dict:
    """Resolve one profile into a flat config: defaults deep-merged with its overrides.

    Backward compatible: a legacy flat file (top-level `subreddits:` + `pull:`, no
    `profiles:` key) is returned as-is, so an old config or ARB_CONFIG override keeps
    working without a migration step.
    """
    with open(_CFG, "r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    if "profiles" not in raw:
        return raw  # legacy flat shape

    name = (profile or PROFILE).strip()
    profiles = raw.get("profiles") or {}
    if name not in profiles:
        available = ", ".join(sorted(profiles)) or "(none)"
        raise KeyError(
            f"No digest profile named {name!r} in {_CFG}. Available: {available}. "
            f"Set ARB_PROFILE=<name> or pass --profile <name>."
        )

    cfg = _deep_merge(raw.get("defaults") or {}, profiles[name])
    cfg["profile"] = name
    # A profile's focus block defaults to its own name, so the common case needs no
    # `focus:` line at all — one less thing to keep in sync when adding a topic.
    cfg.setdefault("focus", name)
    return cfg


# --- Ingestion: Project Arctic Shift (no auth, no account, no keys) ---
# Reddit denied API access 2026-07-28, so PRAW and its credentials are gone entirely.
# The only knob is a courtesy user-agent for a free community archive. → src/arctic.py
ARCTIC_USER_AGENT = os.environ.get(
    "ARCTIC_USER_AGENT", "ai_reddit_bot/0.2 (personal weekly digest)"
)

# --- Distillation backend ---
# Which engine turns the raw pull into the digest. → src/distill.py
#   router   = the my_intern LLM router (needs the server reachable)
#   opencode = the OpenCode CLI, run headless (the weekly-cron direction, Jul 29)
DISTILL_BACKEND = os.environ.get("DISTILL_BACKEND", "router").strip().lower()

# --- Digest profile / focus ---
# PROFILE picks the subreddit group + tuning from config/profiles.yaml; FOCUS picks the
# focus block composed onto the shared analyst contract (prompts/focus/<FOCUS>.md).
# They are separate because two profiles can legitimately share one focus — e.g. a
# "local-llm" and a "coding-agents" digest both reading as AI practice — while a profile
# that omits `focus:` just inherits its own name. One digest topic == one profile entry
# + one focus file, never a fork of the pipeline. → src/distill.py, src/rank.py
PROFILE = os.environ.get("ARB_PROFILE", "ai-technique").strip()
FOCUS = os.environ.get("ARB_FOCUS", "").strip()  # blank -> the profile's `focus:` value

# --- my_intern router backend ---
# Uses the OpenAI-compatible ingress for simple, uniform response parsing.
# NOTE for local (non-Docker) runs: host.docker.internal only resolves inside Docker.
# Point this at the server's Tailscale IP instead, e.g. http://100.x.y.z:8088
MI_BASE_URL = os.environ.get("MI_BASE_URL", "http://host.docker.internal:8088").rstrip("/")
MI_API_KEY = os.environ.get("MI_API_KEY", "")
# Optional explicit model override sent to the router (else it auto-routes).
# A long, hard summarization is a good fit for a strong tier — pin it if you like:
#   MI_MODEL=anthropic/claude-opus-4-8
MI_MODEL = os.environ.get("MI_MODEL", "")

# --- OpenCode backend ---
# Headless CLI run: `opencode run --model <model> "<prompt>"`. Chosen for the weekly
# cron because it needs no always-on server — the box only has to be awake at cron time,
# unlike the router which must be reachable on demand.
OPENCODE_BIN = os.environ.get("OPENCODE_BIN", "opencode")
OPENCODE_MODEL = os.environ.get("OPENCODE_MODEL", "")  # e.g. openrouter/anthropic/claude-sonnet-4
OPENCODE_TIMEOUT = int(os.environ.get("OPENCODE_TIMEOUT", "900"))  # seconds

# --- Output sinks (each independently optional; set what you want) ---
# Obsidian: written into Jarvis's ingestion library for downstream processing.
OBSIDIAN_VAULT_DIR = os.environ.get("OBSIDIAN_VAULT_DIR", "")
DATA_DIR = Path(os.environ.get("ARB_DATA_DIR", _ROOT / "data" / "raw"))

# Email: plain SMTP so it works with Gmail app passwords, Fastmail, or a local relay —
# no third-party service and no API key to rotate from abroad.
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
EMAIL_FROM = os.environ.get("EMAIL_FROM", "") or SMTP_USER
EMAIL_TO = os.environ.get("EMAIL_TO", "")  # comma-separated for multiple recipients
