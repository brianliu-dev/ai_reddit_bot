"""Distillation — raw pull -> the weekly digest, via a swappable backend.

Two backends, chosen by `DISTILL_BACKEND`:

  router    the my_intern LLM router over its OpenAI-compatible ingress. The original
            implementation. Needs the server reachable at request time.

  opencode  the OpenCode CLI run headless. Brian's Jul 29 direction for the weekly
            cron. The appeal over the router is scheduling: a cron job only needs the
            binary present when it fires, whereas the router has to be up and
            reachable — a real difference for an unattended job running while he's in
            Asia for three months. It also doubles as the live trial of the standing
            "try OpenCode with OpenRouter" sprint item.

The backend boundary is deliberately narrow: every backend is just
`str (prompt) -> str (markdown digest)`. Prompt construction, truncation and the
output contract stay here so the two paths can never drift into producing different
digests.
"""
from __future__ import annotations

import datetime as dt
import shutil
import subprocess
from pathlib import Path

import httpx

from . import config

# The analyst persona lives in prompts/, NOT in this file. Prompt engineering is
# iteration, and iteration against a Python string constant means a code change (and a
# diff nobody can read) for every wording tweak. Externalising it also means the exact
# instructions that produced a given digest can be diffed and version-pinned.
#
# The persona is COMPOSED from two files so multiple digest topics can coexist without
# forking the hard part (Jul 30):
#
#     prompts/_base.md            shared, topic-agnostic: the two-artifact contract, the
#                                 universal Part 1 rules, the whole Part 2 schema + its 8
#                                 epistemic rules, the final check.
#     prompts/focus/<name>.md     per-topic: who the reader is, what to prioritise, and
#                                 Part 1's section structure for THIS topic.
#
# Base first, focus last — later instructions win on emphasis, and the base says so
# explicitly. A new digest topic is therefore ONE small focus file, not a copy of a
# 180-line spec that quietly drifts from its sibling. The honest-null / confidence /
# basis rules in particular must never fork per topic: they are the part that makes the
# machine layer trustworthy, and three diverging copies of them is three different
# definitions of "confidence".
PROMPTS_DIR = Path(__file__).resolve().parent.parent / "prompts"
BASE_PROMPT_PATH = PROMPTS_DIR / "_base.md"

# The delimiter the persona is told to emit between the human digest and the machine
# layer. Kept here because the splitter and the prompt must agree exactly.
SPLIT_MARKER = "===JARVIS==="


class DistillError(RuntimeError):
    """A backend could not produce a digest. Carries an actionable message."""


def resolve_focus(focus: str | None = None) -> str:
    """Explicit arg > ARB_FOCUS env > the active profile's `focus:` > profile name."""
    if focus:
        return focus.strip()
    if config.FOCUS:
        return config.FOCUS
    try:
        return str(config.load_config().get("focus") or config.PROFILE)
    except Exception:
        return config.PROFILE


def focus_path(focus: str | None = None) -> Path:
    """Resolve the focus file for a profile name."""
    return PROMPTS_DIR / "focus" / f"{resolve_focus(focus)}.md"


def _read(path: Path, what: str) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DistillError(f"Could not read the {what} at {path}: {exc}") from exc


def load_system_prompt(focus: str | None = None) -> str:
    """Compose the analyst prompt: shared base + this profile's focus block."""
    fpath = focus_path(focus)
    if not fpath.exists():
        available = sorted(p.stem for p in (PROMPTS_DIR / "focus").glob("*.md"))
        raise DistillError(
            f"No focus block named {fpath.stem!r} at {fpath}.\n"
            f"  Available: {', '.join(available) or '(none)'}\n"
            f"  Set ARB_FOCUS=<name> or pass --focus <name>."
        )
    return (
        _read(BASE_PROMPT_PATH, "shared analyst contract")
        + "\n\n---\n\n"
        + _read(fpath, "focus block")
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
            depth = "" if c.get("top_level", True) else " [reply]"
            lines.append(f"- (+{c['score']}){depth} {c['body'][:300]}")
        lines.append("")
    text = "\n".join(lines)
    return text[:max_chars]


def build_prompt(run: dict) -> str:
    """The full user-side prompt. Shared by every backend so they can't diverge."""
    week_of = dt.date.today().isoformat()
    return (
        f"Week of {week_of}. {run['post_count']} posts across "
        f"{', '.join(run['subreddits'])}.\n\n{_compact(run)}"
    )


# ── backends ──────────────────────────────────────────────────────────────────

def _via_router(prompt: str) -> str:
    body: dict = {
        "model": "auto",  # router auto-routes unless MI_MODEL is set
        "max_tokens": 4000,
        "messages": [
            {"role": "system", "content": load_system_prompt()},
            {"role": "user", "content": prompt},
        ],
    }
    headers = {"content-type": "application/json"}
    if config.MI_API_KEY:
        headers["authorization"] = f"Bearer {config.MI_API_KEY}"
    if config.MI_MODEL:
        headers["x-mi-model"] = config.MI_MODEL

    url = f"{config.MI_BASE_URL}/v1/chat/completions"
    try:
        with httpx.Client(timeout=300) as client:
            resp = client.post(url, json=body, headers=headers)
            resp.raise_for_status()
            data = resp.json()
    except httpx.RequestError as exc:
        raise DistillError(
            f"my_intern router unreachable at {config.MI_BASE_URL} ({exc}). "
            "For a local (non-Docker) run, MI_BASE_URL must be the server's Tailscale "
            "IP — host.docker.internal only resolves inside Docker."
        ) from exc

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise DistillError(f"Unexpected router response shape: {str(data)[:300]}") from exc


def _via_opencode(prompt: str) -> str:
    """Run the digest through the OpenCode CLI headlessly.

    ⚠️ UNVERIFIED AGAINST A REAL BINARY. OpenCode was not installed on this machine
    when this was written (2026-07-29), so the invocation below follows the documented
    `opencode run` non-interactive form but has never actually executed. Treat the
    first real run as the test — the preflight check exists so that first run fails
    with a useful sentence instead of a stack trace, and DISTILL_BACKEND still
    defaults to `router` so nothing depends on this path until it's proven.
    """
    binary = shutil.which(config.OPENCODE_BIN)
    if not binary:
        raise DistillError(
            f"DISTILL_BACKEND=opencode but '{config.OPENCODE_BIN}' is not on PATH.\n"
            "  Install it (https://opencode.ai), or set OPENCODE_BIN to its full path,\n"
            "  or fall back with DISTILL_BACKEND=router."
        )

    cmd = [binary, "run"]
    if config.OPENCODE_MODEL:
        cmd += ["--model", config.OPENCODE_MODEL]
    # System prompt is prepended to the message: `run` is a one-shot with no separate
    # system-prompt flag, and keeping the instructions identical across backends
    # matters more than using a dedicated field.
    cmd.append(f"{load_system_prompt()}\n\n---\n\n{prompt}")

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=config.OPENCODE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as exc:
        raise DistillError(
            f"OpenCode timed out after {config.OPENCODE_TIMEOUT}s. Raise OPENCODE_TIMEOUT "
            "or shrink the prompt (config `posts_per_sub`)."
        ) from exc
    except OSError as exc:
        raise DistillError(f"Could not execute {binary}: {exc}") from exc

    if proc.returncode != 0:
        raise DistillError(
            f"OpenCode exited {proc.returncode}.\n"
            f"  stderr: {(proc.stderr or '').strip()[:500]}"
        )

    out = (proc.stdout or "").strip()
    if not out:
        raise DistillError(
            "OpenCode returned an empty digest. Check that OPENCODE_MODEL is set to a "
            "model your OpenRouter key can reach."
        )
    return out


_BACKENDS = {
    "router": _via_router,
    "opencode": _via_opencode,
}


def split_output(raw: str) -> tuple[str, str]:
    """Split one generation into (human digest, machine layer).

    ONE generation, TWO artifacts — deliberately. Two separate LLM passes would cost
    twice as much and, worse, could produce two accounts of the same week that disagree;
    a fact with two authorities drifts. Splitting one response guarantees the machine
    layer and the prose describe the same reading.

    A missing marker is NOT fatal: the human digest is the part that must always survive,
    so we hand back everything as prose and an empty machine layer. Delivery reports the
    gap rather than failing a run that produced something useful.
    """
    if SPLIT_MARKER in raw:
        human, _, machine = raw.partition(SPLIT_MARKER)
        return human.strip(), machine.strip()
    return raw.strip(), ""


def distill(run: dict, backend: str | None = None) -> tuple[str, str]:
    """Return (human_digest_md, machine_layer) for the given pull run."""
    name = (backend or config.DISTILL_BACKEND).strip().lower()
    fn = _BACKENDS.get(name)
    if fn is None:
        raise DistillError(
            f"Unknown DISTILL_BACKEND {name!r}. Valid: {', '.join(sorted(_BACKENDS))}."
        )
    return split_output(fn(build_prompt(run)))
