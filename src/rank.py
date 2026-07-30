"""Local selection ranking for the digest.

WHY THIS EXISTS (Jul 30)
------------------------
Arctic Shift has no server-side sort, so ranking is 100% ours. Until now it was
`sort(key=score)[:12]` — a pure POPULARITY ranking. That is a fine default and a bad
fit for what the digest is for: the first real digest gave most of its space to headline
news and ideological argument, because on Reddit those out-score technique threads by an
order of magnitude. Popularity was never the target; usefulness was.

    final_rank = score  x  topic_multiplier  x  discussion_multiplier

Three deliberate design choices:

1. **Keyword lists, not a classifier.** A blunt, readable rule you can audit and edit in
   one line beats a clever inference you cannot see into. This mirrors the lesson the
   graph linter taught the vault: prefer a narrow check that risks false alarms over a
   clever inference that can explain away the thing it hunts.

2. **Multiplicative, not additive.** A boost is worth proportionally the same to a
   200-point post and a 2,000-point post, so the weighting never *inverts* score — it
   re-weights it. A technique thread still has to be reasonably good to win.

3. **Every post carries its own explanation.** `rank_explain` records which patterns
   fired and what each factor did, so `--explain-rank` can show the ranking's actual
   behaviour on a real pull instead of us assuming it works. The pagination bug the day
   before this was written looked healthy from the outside too.

Per-subreddit percentile replaces the old absolute `min_score: 25`, which was inherited
from the PRAW build and applied one number across subs whose 12th-best posts differed by
30x (measured: r/ClaudeAI 1124 vs r/AI_Agents 330).
"""
from __future__ import annotations

import re
import statistics
from typing import Iterable


def compile_rules(rank_cfg: dict) -> dict:
    """Pre-compile the pattern lists once per run."""
    def _comp(key: str) -> list:
        return [re.compile(p, re.IGNORECASE) for p in (rank_cfg.get(key) or [])]

    return {
        "boost": _comp("boost_patterns"),
        "penalty": _comp("penalty_patterns"),
        "topic_boost": float(rank_cfg.get("topic_boost", 1.0)),
        "topic_penalty": float(rank_cfg.get("topic_penalty", 1.0)),
        "discussion_weight": float(rank_cfg.get("discussion_weight", 0.0)),
        "discussion_cap": float(rank_cfg.get("discussion_cap", 2.0)),
        "percentile": float(rank_cfg.get("percentile", 0.0)),
    }


def _text_of(post: dict) -> str:
    return f"{post.get('title') or ''}\n{post.get('selftext') or ''}"


def topic_multiplier(post: dict, rules: dict) -> tuple[float, list[str]]:
    """Multiplier from title/body shape, plus the patterns that fired.

    Boost and penalty COMPOSE rather than one winning: a post titled "How I stopped my
    agent hallucinating after the CEO drama" is genuinely both, and should land between
    the two rather than being fully claimed by whichever list is checked first.
    """
    text = _text_of(post)
    fired: list[str] = []
    mult = 1.0

    if any(rx.search(text) and fired.append(f"+{rx.pattern}") is None for rx in rules["boost"]):
        mult *= rules["topic_boost"]
    if any(rx.search(text) and fired.append(f"-{rx.pattern}") is None for rx in rules["penalty"]):
        mult *= rules["topic_penalty"]

    return mult, fired


def discussion_multiplier(post: dict, median_ratio: float, rules: dict,
                          topic_mult: float = 1.0) -> float:
    """Reward comments-per-point relative to the subreddit's own median.

    Normalising against the sub's median is what stops this becoming a 'chatty
    subreddits always win' rule — each sub is compared to itself.

    ⚠️ GATED ON TOPIC (Jul 30, after measuring). The original design assumed technique
    threads run high comments-per-point because people ask "how did you do that". True —
    but MEASURED ON THE REAL CORPUS, political and drama threads run higher still:
    "Elon completely contradicts himself…" and "Anyone else's human get quietly nerfed…"
    both pinned the cap. Controversy is the most comment-dense content on Reddit, so an
    ungated depth bonus is a *drama* bonus, i.e. it actively worked against the reason
    this profile exists.

    So depth only counts for posts that already look like practice (topic_mult >= 1.0).
    A drama post gets no lift from being argued about; a technique post still gets
    rewarded for generating real discussion.
    """
    weight = rules["discussion_weight"]
    if weight <= 0 or median_ratio <= 0 or topic_mult < 1.0:
        return 1.0

    score = max(int(post.get("score") or 0), 1)
    ratio = int(post.get("num_comments") or 0) / score
    rel = ratio / median_ratio                      # 1.0 == typical for this sub
    mult = 1.0 + weight * (rel - 1.0)
    return max(0.25, min(mult, rules["discussion_cap"]))


def _transform_score(score: int, how: str) -> float:
    """Compress the raw score before weighting.

    WHY (Jul 30, forced by the selftest): Reddit scores are heavy-tailed — within one
    sub the top post routinely outscores the tenth by 10-30x — but a 10x score gap is
    emphatically not a 10x *usefulness* gap for a technique digest. On raw score the
    topic weighting (a 3.8x swing between full boost and full penalty) simply cannot
    reach past a 10x popularity gap, so the highest-scoring news post wins every week no
    matter what the weights say. The weighting existed but could not bite.

    `sqrt` is the deliberate middle:
      * a 10x score gap becomes 3.2x  -> the topic weighting CAN overturn it (intended)
      * a 1000x score gap becomes 32x -> it cannot (also intended)
    So a modest technique thread beats a big news post, while a genuinely massive story
    still leads. `log` was tried and rejected: it flattens so hard that a 50-point post
    can outrank a 50,000-point one, which is not re-ranking, it's ignoring the signal.
    """
    score = max(score, 0)
    if how == "none":
        return float(score)
    if how == "log":
        import math
        return math.log10(1.0 + score)
    return score ** 0.5  # sqrt, the default


def _median_comment_ratio(posts: Iterable[dict]) -> float:
    ratios = [
        int(p.get("num_comments") or 0) / max(int(p.get("score") or 0), 1)
        for p in posts
        if int(p.get("score") or 0) > 0
    ]
    return statistics.median(ratios) if ratios else 0.0


def percentile_floor(posts: list[dict], percentile: float) -> float:
    """Score at the given percentile of THIS subreddit's window.

    Returns 0.0 when disabled or when there is too little data to be meaningful —
    a thin week should not have its few posts cut by a percentile computed from noise.
    """
    if not posts or percentile <= 0:
        return 0.0
    scores = sorted(int(p.get("score") or 0) for p in posts)
    if len(scores) < 10:
        return 0.0
    idx = min(int(len(scores) * percentile), len(scores) - 1)
    return float(scores[idx])


def rank_posts(posts: list[dict], rank_cfg: dict, *, floor_pool: list[dict] | None = None) -> list[dict]:
    """Score, annotate and sort posts for one subreddit (highest first).

    `floor_pool` is the full scanned window used to compute the percentile floor; when
    omitted, `posts` itself is used. They differ in the two-pass scan, where the floor
    should come from everything seen, not just what survived hydration.
    """
    rules = compile_rules(rank_cfg)
    pool = floor_pool if floor_pool is not None else posts
    median_ratio = _median_comment_ratio(pool)

    transform = rank_cfg.get("score_transform", "sqrt")
    for post in posts:
        base = _transform_score(int(post.get("score") or 0), transform)
        tmult, fired = topic_multiplier(post, rules)
        dmult = discussion_multiplier(post, median_ratio, rules, topic_mult=tmult)
        post["rank_score"] = base * tmult * dmult
        post["rank_explain"] = {
            "score": base,
            "topic": round(tmult, 3),
            "discussion": round(dmult, 3),
            "patterns": fired,
            "final": round(base * tmult * dmult, 1),
        }

    posts.sort(key=lambda p: p["rank_score"], reverse=True)
    return posts


# ---------------------------------------------------------------------------
# Selftest — a ranker whose success and failure both look like "a sorted list"
# needs a deliberate proof that its weighting actually moves things.
# Run: python -m src.rank --selftest
# ---------------------------------------------------------------------------

def _selftest() -> int:
    cfg = {
        "percentile": 0.85,
        "discussion_weight": 0.6,
        "discussion_cap": 2.0,
        "topic_boost": 1.7,
        "topic_penalty": 0.45,
        "boost_patterns": ["how i ", "workflow", "agent"],
        "penalty_patterns": ["\\bceo\\b", "lawsuit", "\\bagi\\b"],
    }
    fails: list[str] = []

    def check(name: str, cond: bool) -> None:
        if not cond:
            fails.append(name)

    # THE CASE THIS WAS BUILT FOR: a modest technique thread must beat a big news post.
    technique = {"title": "How I restructured my agent workflow", "score": 300, "num_comments": 180}
    news = {"title": "OpenAI CEO announces new model", "score": 3000, "num_comments": 90}
    ranked = rank_posts([news, technique], cfg)
    check("technique outranks 10x-score news", ranked[0]["title"].startswith("How I"))

    # ...but the weighting must NOT be able to invert an arbitrary gap. A 100x news
    # post should still win — this is re-weighting, not overriding.
    small = {"title": "How I use agents", "score": 50, "num_comments": 10}
    huge = {"title": "OpenAI CEO announces new model", "score": 50000, "num_comments": 500}
    ranked = rank_posts([small, huge], cfg)
    check("weighting re-ranks but does not override", ranked[0]["score"] == 50000)

    # Boost and penalty compose rather than one short-circuiting.
    both = {"title": "How I survived the CEO drama", "score": 100, "num_comments": 0}
    m, fired = topic_multiplier(both, compile_rules(cfg))
    check("boost+penalty compose", abs(m - (1.7 * 0.45)) < 1e-9)
    check("both patterns recorded", any(f.startswith("+") for f in fired) and any(f.startswith("-") for f in fired))

    # Discussion multiplier is relative to the sub's median, not absolute.
    rules = compile_rules(cfg)
    chatty = {"title": "x", "score": 100, "num_comments": 200}   # ratio 2.0
    quiet = {"title": "x", "score": 100, "num_comments": 10}     # ratio 0.1
    med = _median_comment_ratio([chatty, quiet])
    check("chatty above median", discussion_multiplier(chatty, med, rules) > 1.0)
    check("quiet below median", discussion_multiplier(quiet, med, rules) < 1.0)
    check("discussion respects cap", discussion_multiplier(
        {"score": 1, "num_comments": 10000}, 0.01, rules) <= cfg["discussion_cap"])

    # Percentile floor refuses to act on too little data (a thin week must not self-empty).
    check("floor disabled under 10 posts", percentile_floor([{"score": i} for i in range(5)], 0.85) == 0.0)
    floor = percentile_floor([{"score": i} for i in range(100)], 0.85)
    check("floor at ~85th percentile", 80 <= floor <= 90)

    # Explanation is populated — --explain-rank depends on it.
    check("explain populated", "patterns" in technique["rank_explain"] and technique["rank_explain"]["final"] > 0)

    # Score transform: sqrt must compress a 10x gap to ~3.2x and leave 1000x uncrossable.
    check("sqrt compresses 10x to ~3.2x", abs(_transform_score(3000,"sqrt")/_transform_score(300,"sqrt") - 3.162) < 0.01)
    check("log rejected for a reason", _transform_score(50000,"log")/_transform_score(50,"log") < 3)
    check("none is identity", _transform_score(42,"none") == 42.0)

    # Drama must NOT collect a discussion bonus (the gate added after measuring).
    rules2 = compile_rules(cfg)
    drama = {"title": "The CEO lawsuit thread", "score": 1000, "num_comments": 5000}
    tm, _ = topic_multiplier(drama, rules2)
    check("drama gets no depth bonus", discussion_multiplier(drama, 0.1, rules2, topic_mult=tm) == 1.0)

    # A planted defect must be caught: zero weights should collapse to pure score order.
    flat = dict(cfg, topic_boost=1.0, topic_penalty=1.0, discussion_weight=0.0)
    ranked = rank_posts([technique.copy(), news.copy()], flat)
    check("neutral config == pure score order", ranked[0]["score"] == 3000)

    if fails:
        print(f"rank selftest: {len(fails)} FAILED")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("rank selftest: all checks passed ✅")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_selftest() if "--selftest" in sys.argv else 0)
