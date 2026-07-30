"""Reddit ingestion via Project Arctic Shift.

Replaces the original PRAW implementation (Reddit denied API access 2026-07-28 —
see arctic.py for the full why). The public contract is unchanged on purpose:
`pull()` still returns the same run dict, so storage.py and distill.py did not have
to change when the source was swapped.

What actually got better in the trade:

  * **We own the ranking now.** PRAW handed us a fixed `top(time_filter="week")`.
    Arctic Shift returns the raw window and we rank locally, so `min_score`,
    per-sub caps and the comment ranking are all ours to tune.
  * **No auth, no account, no approval queue** — nothing to expire or get revoked
    mid-trip, which matters for an unattended weekly cron running from Asia.

The one thing that got worse: a ~36h scoring lag in the archive. Handled by
`min_post_age_hours` — see `_window()`.

This stage has NO dependency on my_intern, OpenCode, or any LLM; it can be tested
standalone:
    ./.venv/bin/python -m src.main --ingest-only
"""
from __future__ import annotations

import time

import httpx

from . import arctic, config, rank


def _window(pull_cfg: dict, now: int | None = None) -> tuple[int, int]:
    """The [after, before) unix window to pull.

    `before` is pulled BACK by `min_post_age_hours` rather than being "now". Arctic
    Shift archives posts instantly but leaves score/num_comments at 0/1 for ~36h, so
    including the last day and a half would inject a pile of posts that all look
    worthless to the ranker — dropped by `min_score` at best, or kept and presented as
    unpopular at worst. Excluding them is the honest read: they aren't scored yet, so
    we genuinely don't know.

    This is also the guard on the documented invalidation condition. A weekly digest
    never notices the lag; a daily one would be crippled by it.
    """
    now = now or int(time.time())
    lag = int(pull_cfg.get("min_post_age_hours", 36)) * 3600
    before = now - lag
    after = before - int(pull_cfg["window_days"]) * 86400
    return after, before


def _post_dict(post: dict, comments: list[dict]) -> dict:
    """Arctic Shift post object -> the dict shape storage/distill already expect."""
    permalink = post.get("permalink") or ""
    return {
        "id": post.get("id"),
        "subreddit": post.get("subreddit"),
        "title": post.get("title") or "",
        "score": int(post.get("score") or 0),
        "num_comments": int(post.get("num_comments") or 0),
        "url": post.get("url") or "",
        "permalink": f"https://reddit.com{permalink}" if permalink.startswith("/") else permalink,
        "author": post.get("author") or "[deleted]",
        "created_utc": int(post.get("created_utc") or 0),
        "selftext": (post.get("selftext") or "")[:4000],
        "top_comments": comments,
    }


def _is_usable(post: dict, min_score: int) -> bool:
    if int(post.get("score") or 0) < min_score:
        return False
    # The archive keeps removed/deleted posts as tombstones: they carry a title and a
    # real score but no content, so they'd occupy a digest slot while saying nothing.
    if (post.get("selftext") or "") in ("[removed]", "[deleted]"):
        return False
    return bool(post.get("title"))


def pull(cfg: dict | None = None, progress=print) -> dict:
    """Pull posts + their best comments for all configured subreddits.

    Returns the run dict: {pulled_at, source, window, subreddits, post_count,
    errors, posts}.
    """
    cfg = cfg or config.load_config()
    pull_cfg = cfg["pull"]
    # Absent `rank:` == the old pure-score behaviour: percentile 0 disables the relative
    # floor and the neutral multipliers collapse to score order. A legacy flat config
    # therefore keeps working unchanged rather than crashing on a missing key.
    rank_cfg = cfg.get("rank") or {}
    after, before = _window(pull_cfg)

    seen: set[str] = set()
    posts: list[dict] = []
    errors: list[str] = []

    with httpx.Client(timeout=45.0, headers={"user-agent": config.ARCTIC_USER_AGENT}) as client:
        for name in cfg["subreddits"]:
            try:
                # PASS 1 — scan the window for scores only (~40x less data than pulling
                # full objects for thousands of posts to keep twelve).
                index = arctic.fetch_post_index(
                    client,
                    name,
                    after,
                    before,
                    limit=pull_cfg["fetch_limit"],
                    max_pages=pull_cfg["max_pages"],
                )
                # Two-stage cut. The absolute `min_score` is now only a noise floor;
                # the real selection is a PER-SUB percentile, because one absolute
                # number across subs whose 12th-best posts differ 30x (r/ClaudeAI 1124
                # vs r/AI_Agents 330, measured Jul 29) cannot serve both.
                floor = max(
                    float(pull_cfg["min_score"]),
                    rank.percentile_floor(index, rank_cfg.get("percentile", 0.0)),
                )
                over = [p for p in index if int(p.get("score") or 0) >= floor]

                # Pass 1 has no titles (the scan projection is scores only), so topic
                # weighting cannot apply yet — rank by score here purely to pick who
                # gets hydrated, then re-rank properly once titles exist.
                over.sort(key=lambda p: int(p.get("score") or 0), reverse=True)
                # Over-fetch generously: the topic re-rank after hydration can promote a
                # post the score-only pass had well down the list, so the shortlist must
                # be wider than the final cut or the weighting has nothing to choose
                # from. (Also absorbs tombstones — `[removed]` bodies the index can't show.)
                shortlist = over[: pull_cfg["posts_per_sub"] * 3 + 3]

                # PASS 2 — full objects for the shortlist only.
                raw = arctic.hydrate_posts(client, [p["id"] for p in shortlist])
            except arctic.ArcticShiftError as exc:
                # One bad subreddit must not cost the whole week's digest — collect the
                # failure, keep going, and surface it on the run so it's visible rather
                # than quietly producing a thinner digest.
                errors.append(f"{name}: {exc}")
                progress(f"      ! {name}: {exc}")
                continue

            usable = [p for p in raw if _is_usable(p, pull_cfg["min_score"])]
            # THE RE-RANK. Titles exist now, so topic shape and discussion depth can
            # apply. `floor_pool=index` so the comment-ratio median comes from the whole
            # scanned window rather than from the already-score-biased shortlist.
            rank.rank_posts(usable, rank_cfg, floor_pool=index)
            selected = usable[: pull_cfg["posts_per_sub"]]
            promoted = sum(
                1 for i, p in enumerate(selected)
                if p["rank_explain"]["topic"] != 1.0 or p["rank_explain"]["discussion"] != 1.0
            )
            progress(
                f"      {name}: {len(index)} scanned -> {len(over)} over floor({floor:.0f}) "
                f"-> hydrated {len(raw)} -> keeping {len(selected)} "
                f"({promoted} re-weighted)"
            )

            for post in selected:
                pid = post.get("id")
                if not pid or pid in seen:
                    continue
                seen.add(pid)

                comments: list[dict] = []
                if pull_cfg["top_comments"] > 0 and int(post.get("num_comments") or 0) > 0:
                    try:
                        raw_comments = arctic.fetch_comments(
                            client,
                            pid,
                            limit=pull_cfg["comment_fetch_limit"],
                            max_pages=pull_cfg["comment_max_pages"],
                        )
                        comments = arctic.top_comments(
                            raw_comments,
                            n=pull_cfg["top_comments"],
                            max_chars=pull_cfg["max_comment_chars"],
                            min_top_level=pull_cfg.get("min_top_level_comments"),
                        )
                    except arctic.ArcticShiftError as exc:
                        # A post without its comments is still worth digesting.
                        errors.append(f"comments {pid}: {exc}")

                posts.append(_post_dict(post, comments))

    posts.sort(key=lambda p: p["score"], reverse=True)
    return {
        "pulled_at": int(time.time()),
        "source": "arctic_shift",
        "window": {"after": after, "before": before},
        "subreddits": cfg["subreddits"],
        "post_count": len(posts),
        "errors": errors,
        "posts": posts,
    }
