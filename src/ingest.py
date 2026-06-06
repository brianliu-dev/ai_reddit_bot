"""Reddit ingestion via PRAW.

Pulls top (past week) + newest posts from the configured subreddits, keeps the
top comments for richer signal, dedupes, and applies a min-score threshold.
Returns a list of plain dicts (JSON-serializable) — no PRAW objects leak out.

This stage has NO dependency on my_intern; it can be built and tested first.
"""
from __future__ import annotations

import time

import praw

from . import config


def _client() -> praw.Reddit:
    return praw.Reddit(
        client_id=config.REDDIT_CLIENT_ID,
        client_secret=config.REDDIT_CLIENT_SECRET,
        user_agent=config.REDDIT_USER_AGENT,
        check_for_async=False,
    )


def _top_comments(submission, n: int, max_chars: int) -> list[dict]:
    try:
        submission.comment_sort = "top"
        submission.comments.replace_more(limit=0)
        out = []
        for c in submission.comments[:n]:
            body = (c.body or "")[:max_chars]
            out.append(
                {
                    "author": str(c.author) if c.author else "[deleted]",
                    "score": int(c.score),
                    "body": body,
                }
            )
        return out
    except Exception:
        return []


def _post_dict(submission, pull: dict) -> dict:
    return {
        "id": submission.id,
        "subreddit": str(submission.subreddit),
        "title": submission.title,
        "score": int(submission.score),
        "num_comments": int(submission.num_comments),
        "url": submission.url,
        "permalink": f"https://reddit.com{submission.permalink}",
        "author": str(submission.author) if submission.author else "[deleted]",
        "created_utc": int(submission.created_utc),
        "selftext": (submission.selftext or "")[:4000],
        "top_comments": _top_comments(
            submission, pull["top_comments"], pull["max_comment_chars"]
        ),
    }


def pull(cfg: dict | None = None) -> dict:
    """Pull posts for all configured subreddits. Returns a run dict."""
    cfg = cfg or config.load_config()
    pull_cfg = cfg["pull"]
    reddit = _client()

    seen: set[str] = set()
    posts: list[dict] = []

    for name in cfg["subreddits"]:
        sub = reddit.subreddit(name)
        streams = [
            sub.top(time_filter=pull_cfg["top_time_filter"], limit=pull_cfg["top_limit"]),
            sub.new(limit=pull_cfg["new_limit"]),
        ]
        for stream in streams:
            for submission in stream:
                if submission.id in seen:
                    continue
                if submission.score < pull_cfg["min_score"]:
                    continue
                seen.add(submission.id)
                posts.append(_post_dict(submission, pull_cfg))

    posts.sort(key=lambda p: p["score"], reverse=True)
    return {
        "pulled_at": int(time.time()),
        "subreddits": cfg["subreddits"],
        "post_count": len(posts),
        "posts": posts,
    }
