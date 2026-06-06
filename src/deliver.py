"""Delivery — write the Obsidian note and push to Telegram."""
from __future__ import annotations

import datetime as dt
from pathlib import Path

import httpx

from . import config

_TELEGRAM_LIMIT = 4000  # Telegram hard-caps messages at 4096 chars; stay under.


def write_obsidian_note(digest_md: str, run: dict, when: dt.date | None = None) -> Path | None:
    if not config.OBSIDIAN_VAULT_DIR:
        return None
    when = when or dt.date.today()
    vault = Path(config.OBSIDIAN_VAULT_DIR)
    vault.mkdir(parents=True, exist_ok=True)
    path = vault / f"AI Reddit Digest {when.isoformat()}.md"

    frontmatter = (
        "---\n"
        f"date: {when.isoformat()}\n"
        "tags: [ai-reddit-digest, llm, weekly]\n"
        f"post_count: {run['post_count']}\n"
        f"subreddits: [{', '.join(run['subreddits'])}]\n"
        "---\n\n"
        f"# AI Reddit Digest — {when.isoformat()}\n\n"
    )
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(frontmatter + digest_md + "\n")
    return path


def _chunks(text: str, size: int) -> list[str]:
    out, cur = [], ""
    for line in text.splitlines(keepends=True):
        if len(cur) + len(line) > size:
            out.append(cur)
            cur = ""
        cur += line
    if cur:
        out.append(cur)
    return out


def push_telegram(digest_md: str, when: dt.date | None = None) -> bool:
    if not (config.TELEGRAM_BOT_TOKEN and config.TELEGRAM_CHAT_ID):
        return False
    when = when or dt.date.today()
    header = f"🧠 *AI Reddit Digest — {when.isoformat()}*\n\n"
    api = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"

    parts = _chunks(header + digest_md, _TELEGRAM_LIMIT)
    with httpx.Client(timeout=30) as client:
        for part in parts:
            resp = client.post(
                api,
                json={
                    "chat_id": config.TELEGRAM_CHAT_ID,
                    "text": part,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
            )
            resp.raise_for_status()
    return True
