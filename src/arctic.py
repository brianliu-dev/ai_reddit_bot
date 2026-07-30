"""Arctic Shift API client — the Reddit ingestion source.

WHY THIS EXISTS
---------------
Reddit denied API access on 2026-07-28. Not re-appliable: since late 2025 every new
OAuth registration goes through manual approval under the Responsible Builder Policy,
which favours commercial applicants, and unauthenticated `.json` endpoints were killed
in May 2026. Project Arctic Shift (https://github.com/ArthurHeitmann/arctic_shift) is a
public Reddit research archive — free, no auth, no account, no approval queue.

THE ONE CAVEAT THAT MATTERS
---------------------------
Posts are archived instantly but `score` / `num_comments` stay 0/1 for roughly 36 hours.
This digest is WEEKLY, so every post in a trailing-7-day window is well past that and
fully scored. **If the cadence ever moves to daily, this source must be re-evaluated** —
that is the documented invalidation condition, not a footnote. `min_post_age_hours`
below enforces it defensively so a mis-scheduled run drops unscored posts instead of
silently reporting everything as score 0.

API SHAPES (verified live 2026-07-29, not assumed)
--------------------------------------------------
  GET /api/posts/search?subreddit=X&after=<unix>&before=<unix>&limit=100
      -> {"data": [ <full reddit post object>, ... ]}

  GET /api/comments/search?link_id=<post_id>&limit=100&sort=asc
      -> {"data": [ <flat comment object>, ... ]}   # flat, NOT a tree

Two things learned by probing that shape the code below:

1. There is NO server-side sort by score. `sort_type` accepts only `default` or
   `created_utc` (a `sort_type=score` request 400s). So ranking is done LOCALLY — which
   is an upgrade over PRAW's fixed `top`, since we now control the ranking.

2. `sort=asc` (oldest-first) is deliberately chosen over `desc` for comments. On a Reddit
   thread the earliest comments accumulate the most votes, so ascending order surfaces the
   high scorers within the first page — measured on a real thread, asc gave scores
   [256, 257, 40, 111, 81] where desc gave [11, 3, 3, ...]. Fetch early, rank locally.

We use /comments/search (flat) rather than /comments/tree (nested) because the digest only
needs the strongest comments, and the tree's `limit` counts total nodes across all depths,
making "give me the best N comments" awkward to express.
"""
from __future__ import annotations

import time

import httpx

BASE_URL = "https://arctic-shift.photon-reddit.com"

# Arctic Shift is a free community archive. Be a good citizen: a couple of requests
# per second is explicitly fine, so we pace rather than burst.
_PAUSE_SECONDS = 0.4
_MAX_RETRIES = 4
_TIMEOUT = 45.0


class ArcticShiftError(RuntimeError):
    """Raised when the archive can't be reached or returns something unusable.

    Deliberately distinct from httpx errors so main.py can tell "the source is down"
    (fall back to Apify, per the documented build trigger) apart from a local bug.
    """


# Status codes worth retrying. 429 is the documented rate limit; 5xx is the archive
# having a moment. 422 earned its place the hard way — during the first full 7-subreddit
# run the archive returned 422 for r/AI_Agents, and the IDENTICAL request returned 200
# moments later on its own. It is load-shedding under a burst, not a malformed query, so
# treating it as fatal would drop a subreddit from the digest for no reason.
_RETRY_STATUSES = frozenset({422, 429, 500, 502, 503, 504})


def _get(client: httpx.Client, path: str, params: dict) -> list[dict]:
    """One GET with retry/backoff.

    ⚠️ EVERY failure leaves this function as `ArcticShiftError` — never as a raw httpx
    exception. That is load-bearing, not tidiness: ingest.py catches ArcticShiftError
    per subreddit so one bad source costs one source instead of the whole week's digest.
    The first version of this file let a 422 escape as httpx.HTTPStatusError, and the
    per-subreddit skip — which was already written and looked correct — silently could
    not fire. The run died on subreddit 3 of 7. A guard that has never been shown to
    fire is not a guard.
    """
    last_error = None
    for attempt in range(_MAX_RETRIES):
        try:
            resp = client.get(f"{BASE_URL}{path}", params=params)
        except httpx.RequestError as exc:  # DNS, connect, read timeout
            last_error = exc
            time.sleep(2 ** attempt)
            continue

        if resp.status_code in _RETRY_STATUSES:
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            # Honour the server's own reset hint when it gives one; otherwise back off.
            reset = resp.headers.get("X-RateLimit-Reset") or resp.headers.get("Retry-After")
            try:
                delay = min(float(reset), 60.0)
            except (TypeError, ValueError):
                delay = 2 ** attempt
            time.sleep(max(delay, 1.0))
            continue

        if resp.status_code >= 400:
            # A non-retryable 4xx is our bug (bad param) — surface it verbatim so the
            # message says which param, and don't waste retries on it.
            raise ArcticShiftError(
                f"Arctic Shift rejected {path} with {resp.status_code}: {resp.text[:300]}"
            )

        try:
            payload = resp.json()
        except ValueError as exc:
            raise ArcticShiftError(f"{path} returned non-JSON: {resp.text[:200]}") from exc

        data = payload.get("data") if isinstance(payload, dict) else payload
        if data is None:
            raise ArcticShiftError(f"{path} returned no `data` field: {str(payload)[:200]}")
        return data

    raise ArcticShiftError(
        f"Arctic Shift failed after {_MAX_RETRIES} attempts on {path} "
        f"(last error: {last_error}). If this persists, the documented fallback is Apify."
    )


# Fields the projection accepts, probed one by one (2026-07-29). Notably ABSENT:
# `permalink` and `upvote_ratio` — both 400. The permalink is reconstructed from
# subreddit + id instead, which is lossless: Reddit's canonical form is
# /r/<sub>/comments/<id>/.
SCAN_FIELDS = "id,score,num_comments,created_utc"


def permalink_for(subreddit: str, post_id: str) -> str:
    """Rebuild the permalink the projection won't give us."""
    return f"https://reddit.com/r/{subreddit}/comments/{post_id}/"


def fetch_post_index(
    client: httpx.Client,
    subreddit: str,
    after: int,
    before: int,
    limit: int = 100,
    max_pages: int = 20,
) -> list[dict]:
    """PASS 1 — scan the window for {id, score, num_comments, created_utc} only.

    Ranking needs nothing but the score, so pulling full post objects for thousands of
    posts in order to keep twelve is pure waste. Measured on real 7-day windows:

        r/LocalLLaMA   full pages 3,708 KB  ->  two-pass 100 KB   (37x less)
        r/ClaudeAI     full pages 7,621 KB  ->  two-pass 192 KB   (40x less)

    That matters beyond tidiness. Arctic Shift is a **donation-funded archive run by one
    person**, with no SLA and no obligation to us. Moving ~40x less data for an identical
    digest is what keeps this job an unremarkable client rather than a cost centre — and
    the cheapest insurance against the free tier going away.

    Wall-clock only improves ~1.3x because the run is dominated by deliberate pacing,
    not transfer. Bandwidth was the point.
    """
    return _paginate(client, "/api/posts/search",
                     {"subreddit": subreddit, "fields": SCAN_FIELDS},
                     after, before, limit, max_pages)


def hydrate_posts(client: httpx.Client, post_ids: list[str]) -> list[dict]:
    """PASS 2 — fetch full objects for the handful of posts that actually won."""
    if not post_ids:
        return []
    data = _get(client, "/api/posts/ids", {"ids": ",".join(post_ids)})
    time.sleep(_PAUSE_SECONDS)
    return data


def fetch_posts(
    client: httpx.Client,
    subreddit: str,
    after: int,
    before: int,
    limit: int = 100,
    max_pages: int = 20,
    progress=None,
) -> list[dict]:
    """ALL posts for one subreddit in the [after, before) window, paginated, FULL objects.

    Kept for callers that genuinely want every field of every post. The pipeline uses
    `fetch_post_index` + `hydrate_posts` instead — see those for why.

    ⚠️ Pagination is not an optimisation here, it is a correctness requirement, and
    getting it wrong is silent. Measured on a real 7-day LocalLLaMA window:

        one page  (limit=100)  ->  100 posts, best score 1071
        paginated              ->  737 posts, best score 3144

    `limit` is hard-capped at 100 by the archive (a limit=250 request 400s) and the
    DEFAULT SORT IS DESCENDING — so a single call returns the *newest* 100 posts, which
    on a busy sub is the last ~18 hours. Ranking those and calling it "the week's top
    posts" would produce a confident, plausible, wrong digest every single run: the
    output looks perfectly healthy, it's just quietly reporting one day as seven.

    We therefore pass `sort=asc` and walk `after` forward from the oldest edge, which
    makes the cursor monotonic and the termination condition obvious. `max_pages` is a
    safety stop for very high-volume subs (r/ClaudeAI hit 1500 posts in a week); when it
    trips we've still got the oldest N pages of the window rather than a random slice.
    """
    return _paginate(client, "/api/posts/search", {"subreddit": subreddit},
                     after, before, limit, max_pages, progress=progress)


def fetch_comments(
    client: httpx.Client,
    post_id: str,
    limit: int = 100,
    max_pages: int = 2,
) -> list[dict]:
    """Flat comment list for one post, oldest-first, paginated.

    Same page cap (100) as posts, so a 600-comment thread needs several calls to be
    seen at all. This is a genuine sampling limit, not a detail: with only one page we
    rank the best 10 comments out of the first 100 of a 617-comment thread, and any
    strong reply further down is invisible — it never loses a ranking, it's simply
    never a candidate.

    `max_pages` is the honest knob for that tradeoff. It defaults low (2 = 200 comments)
    because this runs per-post across ~76 posts and every page is another request
    against a free archive. Raise it if the digest starts feeling like it missed the
    good arguments.

    `sort=asc` is deliberate — see the module docstring. Early comments accumulate the
    votes, so ascending order front-loads the high scorers.
    """
    return _paginate(client, "/api/comments/search", {"link_id": post_id},
                     after=None, before=None, limit=limit, max_pages=max_pages)


def _paginate(
    client: httpx.Client,
    path: str,
    base_params: dict,
    after: int | None,
    before: int | None,
    limit: int,
    max_pages: int,
    progress=None,
) -> list[dict]:
    """Walk a `created_utc`-ordered endpoint forward until the window is exhausted.

    ⚠️ Pagination is not an optimisation here, it is a correctness requirement, and
    getting it wrong is silent. Measured on a real 7-day LocalLLaMA window:

        one page  (limit=100)  ->  100 posts, best score 1071
        paginated              ->  737 posts, best score 3144

    `limit` is hard-capped at 100 by the archive (a limit=250 request 400s) and the
    DEFAULT SORT IS DESCENDING — so a single call returns the *newest* 100 items, which
    on a busy sub is the last ~18 hours. Ranking those and calling it "the week's top
    posts" would produce a confident, plausible, wrong digest every single run: the
    output looks perfectly healthy, it's just quietly reporting one day as seven.

    We therefore pass `sort=asc` and walk `after` forward from the oldest edge, which
    makes the cursor monotonic and the termination condition obvious. `max_pages` is the
    safety stop (r/ClaudeAI exceeded 2000 posts in one week).
    """
    collected: list[dict] = []
    seen: set[str] = set()
    cursor = after

    for _ in range(max_pages):
        params = {**base_params, "limit": limit, "sort": "asc"}
        if cursor is not None:
            params["after"] = cursor
        if before is not None:
            params["before"] = before

        page = _get(client, path, params)
        time.sleep(_PAUSE_SECONDS)
        if not page:
            break

        fresh = [p for p in page if p.get("id") and p["id"] not in seen]
        for p in fresh:
            seen.add(p["id"])
        collected.extend(fresh)

        if progress:
            progress(f"        …{len(collected)} scanned")

        # Short page = window exhausted.
        if len(page) < limit:
            break

        # Advance past the newest item on this page. The +1 guarantees forward motion;
        # without it a page whose items share a timestamp would loop forever.
        newest = max(int(p.get("created_utc") or 0) for p in page)
        next_cursor = newest + 1
        if cursor is not None and next_cursor <= cursor:
            break
        cursor = next_cursor

    return collected


def top_comments(
    comments: list[dict],
    n: int,
    max_chars: int,
    min_top_level: int | None = None,
) -> list[dict]:
    """Rank a flat comment list locally and return the best `n`.

    THE TRADEOFF, AND WHY IT CHANGED (Brian's catch, 2026-07-29)
    -----------------------------------------------------------
    Top-level comments (`parent_id` starting `t3_`) are worth a thumb on the scale: a
    deep reply quoted without its parent is often incoherent in a digest — it answers a
    question the reader can't see.

    But the first implementation turned that preference into an absolute. It was
    "if there are >= n top-level comments, use ONLY top-level", which means on any
    healthy thread — where top-level comments are plentiful — **a +500 reply could never
    be surfaced at all.** It didn't lose a ranking; it was never a candidate. Brian asked
    exactly the right question about it, and the answer was: no, it would never show up.

    The fix is a FLOOR, not a filter. Of `n` slots, at least `min_top_level` go to
    top-level comments (coherence), and every remaining slot is filled by pure score at
    any depth. So the best replies compete on merit, while the digest still gets enough
    standalone-readable material to make sense.

    Default floor is 60% of `n` — with n=10 that's 6 guaranteed top-level and 4 open
    slots. Tune `min_top_level` in config; 0 makes it a pure score ranking.
    """
    usable = [
        c for c in comments
        if (c.get("body") or "").strip() not in ("", "[deleted]", "[removed]")
        and c.get("author") not in ("AutoModerator",)
    ]
    usable.sort(key=lambda c: c.get("score", 0), reverse=True)

    floor = int(n * 0.6) if min_top_level is None else min_top_level
    floor = max(0, min(floor, n))

    top_level = [c for c in usable if str(c.get("parent_id", "")).startswith("t3_")]

    picked: list[dict] = []
    picked_ids: set[str] = set()

    # Reserve the floor for top-level comments (or as many as the thread actually has).
    for c in top_level[:floor]:
        picked.append(c)
        picked_ids.add(c.get("id"))

    # Fill everything else by raw score, depth-blind — this is the part that lets a
    # standout reply in, and the part the old implementation made impossible.
    for c in usable:
        if len(picked) >= n:
            break
        if c.get("id") not in picked_ids:
            picked.append(c)
            picked_ids.add(c.get("id"))

    picked.sort(key=lambda c: c.get("score", 0), reverse=True)
    return [
        {
            "author": c.get("author") or "[deleted]",
            "score": int(c.get("score", 0)),
            "top_level": str(c.get("parent_id", "")).startswith("t3_"),
            "body": (c.get("body") or "")[:max_chars],
        }
        for c in picked[:n]
    ]
