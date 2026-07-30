"""Unit tests for Arctic Shift ingestion — no network.

These exist because the swap from PRAW to Arctic Shift moved real judgement into our
own code: the pull window, the pagination cursor, and the comment ranking used to be
Reddit's problem and are now ours. Each of the three has a failure mode that produces
a *plausible* digest rather than an error, which is exactly the kind of bug that
survives for months.
"""
import time

import pytest

from src import arctic, ingest

CFG = {
    "window_days": 7,
    "min_post_age_hours": 36,
    "fetch_limit": 100,
    "max_pages": 20,
    "posts_per_sub": 12,
    "min_score": 25,
    "top_comments": 10,
    "min_top_level_comments": 6,
    "comment_fetch_limit": 100,
    "comment_max_pages": 2,
    "max_comment_chars": 800,
}


# ── the pull window ───────────────────────────────────────────────────────────

def test_window_excludes_the_unscored_lag():
    """The window must END 36h ago, not now. Arctic Shift leaves score at 0 for ~36h,
    so including fresh posts injects a pile of score-0 entries that either get dropped
    or — worse — get reported as unpopular."""
    now = 1_800_000_000
    after, before = ingest._window(CFG, now=now)
    assert before == now - 36 * 3600
    assert after == before - 7 * 86400
    assert before < now  # the whole point


def test_window_length_is_exactly_the_configured_days():
    after, before = ingest._window(CFG, now=int(time.time()))
    assert (before - after) == 7 * 86400


def test_window_lag_is_configurable():
    after, before = ingest._window({**CFG, "min_post_age_hours": 0}, now=1_800_000_000)
    assert before == 1_800_000_000


# ── post filtering ────────────────────────────────────────────────────────────

def test_low_score_posts_are_dropped():
    assert ingest._is_usable({"title": "t", "score": 24}, 25) is False
    assert ingest._is_usable({"title": "t", "score": 25}, 25) is True


def test_removed_tombstones_are_dropped():
    """The archive keeps removed posts with a real title and score but no body. They'd
    occupy a digest slot while saying nothing."""
    post = {"title": "Interesting thing", "score": 900, "selftext": "[removed]"}
    assert ingest._is_usable(post, 25) is False


def test_titleless_posts_are_dropped():
    assert ingest._is_usable({"title": "", "score": 900}, 25) is False


def test_missing_score_is_treated_as_zero_not_a_crash():
    assert ingest._is_usable({"title": "t"}, 25) is False


# ── post normalisation ────────────────────────────────────────────────────────

def test_post_dict_builds_absolute_permalink():
    out = ingest._post_dict({"permalink": "/r/x/comments/abc/t/", "id": "abc"}, [])
    assert out["permalink"] == "https://reddit.com/r/x/comments/abc/t/"


def test_post_dict_leaves_absolute_urls_alone():
    out = ingest._post_dict({"permalink": "https://reddit.com/r/x/", "id": "abc"}, [])
    assert out["permalink"] == "https://reddit.com/r/x/"


def test_post_dict_tolerates_a_sparse_object():
    """Archive records vary; a missing field must not take down a weekly run."""
    out = ingest._post_dict({"id": "abc"}, [])
    assert out["score"] == 0 and out["author"] == "[deleted]" and out["title"] == ""


# ── comment ranking ───────────────────────────────────────────────────────────

def _c(cid, score, body="text", parent="t3_post", author="u"):
    return {"id": cid, "score": score, "body": body, "parent_id": parent, "author": author}


def test_comments_ranked_by_score_locally():
    """Arctic Shift has no server-side score sort (sort_type=score 400s), so ranking
    is ours. If this regresses we'd silently ship whichever comments came back first."""
    got = arctic.top_comments([_c("a", 5), _c("b", 900), _c("c", 50)], n=2, max_chars=100)
    assert [x["score"] for x in got] == [900, 50]


def test_deleted_and_automod_comments_are_filtered():
    raw = [
        _c("a", 10, body="[deleted]"),
        _c("b", 20, body="[removed]"),
        _c("c", 5, body="   "),
        _c("d", 1, body="real", author="AutoModerator"),
        _c("e", 2, body="genuine"),
    ]
    got = arctic.top_comments(raw, n=5, max_chars=100)
    assert [x["body"] for x in got] == ["genuine"]


def test_standout_reply_is_never_locked_out():
    """THE regression Brian caught. The old rule was "if there are >= n top-level
    comments, use ONLY top-level", so on any healthy thread a +999 reply could never be
    surfaced — it wasn't outranked, it was never a candidate. With a floor instead of a
    filter it competes."""
    raw = [_c("a", 10, parent="t3_p"), _c("b", 999, parent="t1_x"), _c("c", 9, parent="t3_p")]
    got = arctic.top_comments(raw, n=2, max_chars=100, min_top_level=1)
    assert 999 in [x["score"] for x in got]


def test_floor_guarantees_top_level_slots():
    """The other half: pure score ranking would fill a digest with context-free replies."""
    raw = [_c(f"r{i}", 500 + i, parent="t1_x") for i in range(10)]
    raw += [_c("t1", 5, parent="t3_p"), _c("t2", 4, parent="t3_p")]
    got = arctic.top_comments(raw, n=5, max_chars=100, min_top_level=2)
    assert sum(1 for x in got if x["top_level"]) == 2
    assert sum(1 for x in got if not x["top_level"]) == 3


def test_default_floor_is_60_percent():
    raw = [_c(f"r{i}", 900 + i, parent="t1_x") for i in range(20)]
    raw += [_c(f"t{i}", 1, parent="t3_p") for i in range(20)]
    got = arctic.top_comments(raw, n=10, max_chars=100)  # no explicit floor
    assert sum(1 for x in got if x["top_level"]) == 6


def test_zero_floor_is_pure_score_ranking():
    raw = [_c("a", 10, parent="t3_p"), _c("b", 999, parent="t1_x")]
    got = arctic.top_comments(raw, n=1, max_chars=100, min_top_level=0)
    assert got[0]["score"] == 999


def test_thin_thread_still_fills_from_replies():
    """A thread with one top-level comment shouldn't yield a one-comment digest entry."""
    raw = [_c("a", 10, parent="t3_p"), _c("b", 999, parent="t1_x")]
    got = arctic.top_comments(raw, n=3, max_chars=100)
    assert [x["score"] for x in got] == [999, 10]


def test_results_are_marked_with_depth():
    """The digest prompt can weight a reply differently if it knows what it is."""
    got = arctic.top_comments([_c("a", 10, parent="t3_p"), _c("b", 9, parent="t1_x")],
                              n=2, max_chars=100, min_top_level=1)
    assert [x["top_level"] for x in got] == [True, False]


def test_no_duplicate_comments_across_floor_and_fill():
    raw = [_c("a", 10, parent="t3_p"), _c("b", 9, parent="t3_p")]
    got = arctic.top_comments(raw, n=5, max_chars=100, min_top_level=2)
    assert len(got) == 2


def test_comment_bodies_are_truncated():
    got = arctic.top_comments([_c("a", 10, body="x" * 5000)], n=1, max_chars=80)
    assert len(got[0]["body"]) == 80


# ── pagination (the silent-correctness one) ───────────────────────────────────

class FakeClient:
    """Serves fixed pages and records the params it was called with."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get(self, url, params):
        self.calls.append(params)
        page = self.pages[len(self.calls) - 1] if len(self.calls) <= len(self.pages) else []

        class R:
            status_code = 200

            @staticmethod
            def json():
                return {"data": page}

            @staticmethod
            def raise_for_status():
                pass

        return R()


def _page(start_id, n, base_ts=1000):
    return [{"id": f"{start_id}{i}", "created_utc": base_ts + i} for i in range(n)]


def test_pagination_walks_until_a_short_page(monkeypatch):
    """Measured for real: one page of a busy sub covers ~18h of a 7-day window and
    misses the week's actual top posts. A regression here looks like a healthy digest."""
    monkeypatch.setattr(arctic.time, "sleep", lambda *_: None)
    client = FakeClient([_page("a", 100, 1000), _page("b", 100, 2000), _page("c", 7, 3000)])
    got = arctic.fetch_posts(client, "LocalLLaMA", after=0, before=999999, limit=100)
    assert len(got) == 207
    assert len(client.calls) == 3


def test_pagination_cursor_moves_forward(monkeypatch):
    monkeypatch.setattr(arctic.time, "sleep", lambda *_: None)
    client = FakeClient([_page("a", 100, 1000), _page("b", 3, 2000)])
    arctic.fetch_posts(client, "X", after=0, before=999999, limit=100)
    assert client.calls[0]["after"] == 0
    assert client.calls[1]["after"] == 1099 + 1  # past the newest item of page 1
    assert all(c["sort"] == "asc" for c in client.calls)  # asc or the cursor is meaningless


def test_pagination_respects_max_pages(monkeypatch):
    monkeypatch.setattr(arctic.time, "sleep", lambda *_: None)
    client = FakeClient([_page(chr(97 + i), 100, 1000 * (i + 1)) for i in range(30)])
    got = arctic.fetch_posts(client, "X", after=0, before=999999, limit=100, max_pages=5)
    assert len(client.calls) == 5
    assert len(got) == 500


def test_pagination_dedupes_overlapping_pages(monkeypatch):
    """Timestamp cursors can re-serve a boundary item; the same post must not be
    counted twice."""
    monkeypatch.setattr(arctic.time, "sleep", lambda *_: None)
    dupe = _page("a", 100, 1000)
    client = FakeClient([dupe, dupe[:50]])
    got = arctic.fetch_posts(client, "X", after=0, before=999999, limit=100)
    assert len({p["id"] for p in got}) == len(got) == 100


def test_pagination_stops_on_stalled_cursor(monkeypatch):
    """Every item sharing one timestamp must terminate, not spin to max_pages."""
    monkeypatch.setattr(arctic.time, "sleep", lambda *_: None)
    same = [{"id": f"x{i}", "created_utc": 500} for i in range(100)]
    client = FakeClient([same] * 30)
    arctic.fetch_posts(client, "X", after=600, before=999999, limit=100, max_pages=30)
    assert len(client.calls) < 30


def test_empty_first_page_returns_nothing(monkeypatch):
    monkeypatch.setattr(arctic.time, "sleep", lambda *_: None)
    assert arctic.fetch_posts(FakeClient([[]]), "X", 0, 1, limit=100) == []


# ── error typing + the per-subreddit skip ─────────────────────────────────────
#
# These exist because of a real bug, not a hypothetical one. The first full 7-subreddit
# run died on subreddit 3 when the archive load-shed a 422: `_get` let it escape as
# httpx.HTTPStatusError, and ingest's per-subreddit skip — already written, and looking
# perfectly correct — could not catch it. The guard was never proven to fire, so it
# didn't. Below, the defect is planted deliberately and the skip is asserted.

class StatusClient:
    """Returns a scripted sequence of status codes."""

    def __init__(self, statuses, body=None):
        self.statuses = list(statuses)
        self.body = body if body is not None else {"data": []}
        self.calls = 0

    def get(self, url, params):
        code = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        outer = self

        class R:
            status_code = code
            headers = {}
            text = f"body for {code}"
            request = None

            @staticmethod
            def json():
                return outer.body

            @staticmethod
            def raise_for_status():
                raise AssertionError("raise_for_status must never be reached")

        return R()


@pytest.mark.parametrize("code", [422, 429, 500, 503])
def test_transient_statuses_are_retried_then_succeed(monkeypatch, code):
    monkeypatch.setattr(arctic.time, "sleep", lambda *_: None)
    client = StatusClient([code, code, 200], body={"data": [{"id": "a", "created_utc": 1}]})
    got = arctic.fetch_posts(client, "X", 0, 999, limit=100)
    assert [p["id"] for p in got] == ["a"]
    assert client.calls == 3  # it really did retry


@pytest.mark.parametrize("code", [422, 429, 503])
def test_persistent_transient_failure_becomes_ArcticShiftError(monkeypatch, code):
    """The exact escape that broke the first real run: it must be OUR error type, or
    ingest's per-subreddit handler cannot catch it."""
    monkeypatch.setattr(arctic.time, "sleep", lambda *_: None)
    with pytest.raises(arctic.ArcticShiftError):
        arctic.fetch_posts(StatusClient([code]), "X", 0, 999, limit=100)


def test_non_retryable_4xx_is_also_our_error_type(monkeypatch):
    monkeypatch.setattr(arctic.time, "sleep", lambda *_: None)
    with pytest.raises(arctic.ArcticShiftError) as e:
        arctic.fetch_posts(StatusClient([400]), "X", 0, 999, limit=100)
    assert "400" in str(e.value)


def test_no_raw_httpx_error_can_escape(monkeypatch):
    """A connection-level failure must be translated too — ingest only catches ours."""
    import httpx as _httpx

    class Boom:
        def get(self, url, params):
            raise _httpx.ConnectError("network down")

    monkeypatch.setattr(arctic.time, "sleep", lambda *_: None)
    with pytest.raises(arctic.ArcticShiftError):
        arctic.fetch_posts(Boom(), "X", 0, 999, limit=100)


def _fake_two_pass(monkeypatch, dead=()):
    """Wire both passes of the scan with in-memory data."""
    def index(client, name, after, before, **kw):
        if name in dead:
            raise arctic.ArcticShiftError("archive said 422 four times")
        return [{"id": f"id-{name}", "score": 500, "num_comments": 40, "created_utc": 1}]

    def hydrate(client, ids):
        return [{
            "id": i, "subreddit": i.replace("id-", ""), "title": f"post {i}",
            "score": 500, "num_comments": 40, "created_utc": 1,
            "permalink": "/r/x/", "selftext": "text",
        } for i in ids]

    monkeypatch.setattr(arctic, "fetch_post_index", index)
    monkeypatch.setattr(arctic, "hydrate_posts", hydrate)


def test_one_dead_subreddit_does_not_kill_the_run(monkeypatch):
    """THE regression test. Plant a failure on subreddit 2 of 3 and assert the other
    two still make it into the digest, with the failure recorded rather than hidden."""
    cfg = {"subreddits": ["good1", "dead", "good2"], "pull": dict(CFG, posts_per_sub=1)}
    _fake_two_pass(monkeypatch, dead={"dead"})
    monkeypatch.setattr(arctic, "fetch_comments", lambda *a, **k: [])

    run = ingest.pull(cfg, progress=lambda *_: None)

    assert run["post_count"] == 2
    assert {p["subreddit"] for p in run["posts"]} == {"good1", "good2"}
    assert len(run["errors"]) == 1 and "dead" in run["errors"][0]


def test_hydration_failure_is_also_survivable(monkeypatch):
    """Pass 2 can fail independently of pass 1 — it must skip the sub, not the run."""
    cfg = {"subreddits": ["a", "b"], "pull": dict(CFG, posts_per_sub=1)}
    _fake_two_pass(monkeypatch)
    real = arctic.hydrate_posts

    def flaky(client, ids):
        if ids and ids[0] == "id-a":
            raise arctic.ArcticShiftError("hydrate failed")
        return real(client, ids)

    monkeypatch.setattr(arctic, "hydrate_posts", flaky)
    monkeypatch.setattr(arctic, "fetch_comments", lambda *a, **k: [])
    run = ingest.pull(cfg, progress=lambda *_: None)

    assert {p["subreddit"] for p in run["posts"]} == {"b"}
    assert len(run["errors"]) == 1


def test_index_pass_filters_before_hydrating(monkeypatch):
    """The whole point of two-pass: we must not hydrate posts under min_score."""
    hydrated = {}

    def index(client, name, after, before, **kw):
        return [{"id": f"hi", "score": 500, "num_comments": 0, "created_utc": 1},
                {"id": f"lo", "score": 3, "num_comments": 0, "created_utc": 2}]

    def hydrate(client, ids):
        hydrated["ids"] = list(ids)
        return [{"id": i, "subreddit": "s", "title": i, "score": 500,
                 "num_comments": 0, "created_utc": 1, "permalink": "/r/x/",
                 "selftext": "t"} for i in ids]

    monkeypatch.setattr(arctic, "fetch_post_index", index)
    monkeypatch.setattr(arctic, "hydrate_posts", hydrate)
    ingest.pull({"subreddits": ["s"], "pull": dict(CFG, posts_per_sub=5)},
                progress=lambda *_: None)

    assert hydrated["ids"] == ["hi"]  # the score-3 post never cost a full fetch


def test_comment_failure_keeps_the_post(monkeypatch):
    """A post whose comments fail to load is still worth digesting."""
    cfg = {"subreddits": ["s"], "pull": dict(CFG, posts_per_sub=1)}
    _fake_two_pass(monkeypatch)

    def boom(*a, **k):
        raise arctic.ArcticShiftError("comments unavailable")

    monkeypatch.setattr(arctic, "fetch_comments", boom)
    run = ingest.pull(cfg, progress=lambda *_: None)

    assert run["post_count"] == 1
    assert run["posts"][0]["top_comments"] == []
    assert any("comments" in e for e in run["errors"])


def test_permalink_reconstruction_matches_reddit_canonical_form():
    """The `fields` projection rejects `permalink`, so it's rebuilt. If this drifts,
    every link in every digest breaks at once."""
    assert arctic.permalink_for("LocalLLaMA", "1v3ba1z") == \
        "https://reddit.com/r/LocalLLaMA/comments/1v3ba1z/"
