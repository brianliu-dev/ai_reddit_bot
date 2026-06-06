"""Distillation — the one stage that depends on my_intern.

Builds a compact view of the week's posts + top comments and asks the my_intern
router to cluster them into themes and summarize the most prominent ideas/trends.
Calls the router's OpenAI-compatible ingress for simple, uniform response parsing.
"""
from __future__ import annotations

import datetime as dt

import httpx

from . import config

_SYSTEM = (
    "You are an analyst writing a weekly digest of what's happening in AI/LLM "
    "developer communities. You are given the week's most prominent Reddit posts "
    "(with their top comments) across several subreddits. Produce a tight, "
    "skimmable markdown digest with these sections:\n"
    "## The Week in 3 Lines  (a 3-bullet TL;DR)\n"
    "## Prominent Themes  (3-6 themes; for each: a bold one-line claim, 2-3 "
    "sentences of synthesis across posts, and the most relevant link)\n"
    "## What's New  (genuinely new tools/models/papers surfaced this week, as a list)\n"
    "## Notable Links  (5-10 title + url worth a click)\n"
    "Synthesize across posts — don't just list them. Be specific and concrete."
)


def _compact(run: dict, max_posts: int = 60, max_chars: int = 45000) -> str:
    """Flatten posts into a compact text block for the model."""
    lines: list[str] = []
    for p in run["posts"][:max_posts]:
        lines.append(
            f"### [{p['subreddit']}] {p['title']}  (score {p['score']}, "
            f"{p['num_comments']} comments)\n{p['permalink']}"
        )
        body = (p.get("selftext") or "").strip()
        if body:
            lines.append(body[:600])
        for c in p.get("top_comments", [])[:3]:
            lines.append(f"- (+{c['score']}) {c['body'][:300]}")
        lines.append("")
    text = "\n".join(lines)
    return text[:max_chars]


def distill(run: dict) -> str:
    """Return a markdown digest string for the given pull run."""
    week_of = dt.date.today().isoformat()
    user_content = (
        f"Week of {week_of}. {run['post_count']} posts across "
        f"{', '.join(run['subreddits'])}.\n\n{_compact(run)}"
    )

    body: dict = {
        "model": "auto",  # router auto-routes unless MI_MODEL is set
        "max_tokens": 2000,
        "messages": [
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_content},
        ],
    }

    headers = {"content-type": "application/json"}
    if config.MI_API_KEY:
        headers["authorization"] = f"Bearer {config.MI_API_KEY}"
    if config.MI_MODEL:
        headers["x-mi-model"] = config.MI_MODEL

    url = f"{config.MI_BASE_URL}/v1/chat/completions"
    with httpx.Client(timeout=180) as client:
        resp = client.post(url, json=body, headers=headers)
        resp.raise_for_status()
        data = resp.json()

    return data["choices"][0]["message"]["content"]
