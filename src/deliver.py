"""Delivery — write the Obsidian note and/or email the digest.

Telegram was REMOVED on 2026-07-29 (Brian: "no need for a telegram token for now").
The two sinks that replaced it are both things he already owns end-to-end:

  Obsidian  the digest lands in Jarvis's ingestion library as a proper vault note —
            frontmatter, tags, wiki-linkable — so the weekly digest becomes something
            Jarvis can *process further* rather than a message that scrolls away.
            This is the primary sink; it's the reason the bot exists.

  Email     plain SMTP. No third-party service, no API key to rotate from abroad,
            works with a Gmail app password or any relay. The "read it on my phone"
            path that Telegram used to be.

Both are independently optional — set neither and `--dry-run` still prints the digest.
Each returns a truthy result on success so main.py can report exactly what happened
rather than claiming a delivery it didn't make.
"""
from __future__ import annotations

import datetime as dt
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

from . import config


def _slug_date(when: dt.date | None) -> dt.date:
    return when or dt.date.today()


def write_obsidian_note(
    digest_md: str, run: dict, when: dt.date | None = None
) -> Path | None:
    """Write the digest as a vault note. Returns the path, or None if not configured."""
    if not config.OBSIDIAN_VAULT_DIR:
        return None
    when = _slug_date(when)
    vault = Path(config.OBSIDIAN_VAULT_DIR)
    vault.mkdir(parents=True, exist_ok=True)
    stem = f"AI Reddit Digest {when.isoformat()}"
    path = vault / f"{stem}.md"

    # `name` + `aliases` both carry the slug: this vault links by `name:`, but Obsidian
    # itself resolves only by filename/alias, so a note with just one of them ghosts in
    # the graph view. Jarvis's graph_health.py treats a missing alias as a hard failure.
    slug = f"ai-reddit-digest-{when.isoformat()}"
    subs = ", ".join(run.get("subreddits", []))
    frontmatter = (
        "---\n"
        f"name: {slug}\n"
        f"aliases: [{slug}]\n"
        f'description: "Weekly AI/LLM subreddit digest — {run.get("post_count", 0)} posts, '
        f'week of {when.isoformat()}"\n'
        "tags: [ai-reddit-digest, llm, weekly, ingested]\n"
        f"date: {when.isoformat()}\n"
        f"source: {run.get('source', 'unknown')}\n"
        f"post_count: {run.get('post_count', 0)}\n"
        f"subreddits: [{subs}]\n"
        f"last-updated: {when.isoformat()}\n"
        "---\n\n"
        f"# AI Reddit Digest — {when.isoformat()}\n\n"
    )

    footer = "\n\n---\n\n"
    errors = run.get("errors") or []
    if errors:
        # Surface partial-pull failures in the note itself. A digest that quietly
        # covered five subs instead of seven looks identical to a complete one.
        footer += "> ⚠️ **Partial pull** — some sources failed this run:\n"
        for e in errors[:10]:
            footer += f"> - {e}\n"
        footer += "\n"
    footer += (
        f"*Pulled from {len(run.get('subreddits', []))} subreddits via "
        f"{run.get('source', 'unknown')}; {run.get('post_count', 0)} posts ranked locally.*\n"
    )

    path.write_text(frontmatter + digest_md + footer, encoding="utf-8")
    return path


def write_jarvis_signal(
    machine_layer: str, run: dict, when: dt.date | None = None
) -> Path | None:
    """Write the machine layer as a companion note for the vault to ingest.

    Deliberately a SEPARATE FILE from the human digest, for three reasons:

      1. **Different lifecycles.** The digest is read once and archived; the signal note
         gets its `status` flipped to `processed` and its items ticked off. Mixing a
         read-only artifact with a mutable one in a single file guarantees churn.
      2. **Different readers.** Nobody wants a YAML block in their email, and no parser
         wants to hunt for it inside prose.
      3. **`status: unprocessed` is the actual handshake.** It's how the vault knows a
         digest arrived and hasn't been looked at — which is the ONE failure mode of an
         automated feed. A pipeline that silently stops looks exactly like a quiet week;
         an unprocessed note that keeps getting older does not.

    The two notes wiki-link to each other so either can be the entry point.
    """
    if not config.OBSIDIAN_VAULT_DIR:
        return None
    when = _slug_date(when)
    vault = Path(config.OBSIDIAN_VAULT_DIR)
    vault.mkdir(parents=True, exist_ok=True)
    slug = f"ai-reddit-signal-{when.isoformat()}"
    path = vault / f"AI Reddit Signal {when.isoformat()}.md"

    body = machine_layer.strip() or (
        "> ⚠️ **No machine layer in this run.** The model did not emit the "
        "`===JARVIS===` marker, so only the prose digest is available. The digest "
        "itself is unaffected — see the link above."
    )

    frontmatter = (
        "---\n"
        f"name: {slug}\n"
        f"aliases: [{slug}]\n"
        f'description: "Machine layer for the {when.isoformat()} AI Reddit digest — '
        'entities, typed items, actionable candidates"\n'
        "tags: [ai-reddit-digest, signal, ingested, unprocessed]\n"
        f"date: {when.isoformat()}\n"
        "type: ingest-signal\n"
        "status: unprocessed\n"
        f"digest: \"[[ai-reddit-digest-{when.isoformat()}]]\"\n"
        f"source_count: {run.get('post_count', 0)}\n"
        f"last-updated: {when.isoformat()}\n"
        "---\n\n"
        f"# AI Reddit Signal — {when.isoformat()}\n\n"
        f"*Machine layer for [[ai-reddit-digest-{when.isoformat()}]]. "
        "Not prose — this exists so a relevance pass can run without re-reading the "
        "corpus. Flip `status:` to `processed` once surfaced.*\n\n"
        "> 🧭 **How to read this:** `entities` is the relevance surface — grep the vault "
        "against it before spending a model call. The bot deliberately does **not** know "
        "Brian's projects; matching happens here, where the context lives.\n\n"
    )
    path.write_text(frontmatter + body + "\n", encoding="utf-8")
    return path


def send_email(digest_md: str, run: dict, when: dt.date | None = None) -> bool:
    """Email the digest as plain text. Returns False if email isn't configured."""
    if not (config.SMTP_HOST and config.EMAIL_TO and config.EMAIL_FROM):
        return False
    when = _slug_date(when)

    msg = EmailMessage()
    msg["Subject"] = f"🧠 AI Reddit Digest — {when.isoformat()}"
    msg["From"] = config.EMAIL_FROM
    msg["To"] = config.EMAIL_TO
    header = (
        f"AI Reddit Digest — week of {when.isoformat()}\n"
        f"{run.get('post_count', 0)} posts across "
        f"{', '.join(run.get('subreddits', []))}\n"
        f"{'-' * 60}\n\n"
    )
    msg.set_content(header + digest_md)

    context = ssl.create_default_context()
    if config.SMTP_PORT == 465:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=context) as s:
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
    else:
        with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT) as s:
            s.ehlo()
            s.starttls(context=context)
            s.ehlo()
            if config.SMTP_USER:
                s.login(config.SMTP_USER, config.SMTP_PASSWORD)
            s.send_message(msg)
    return True
