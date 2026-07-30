"""Unit tests for backend dispatch — no network, no subprocess.

The point of these: the backend swap must fail LOUDLY and specifically. A distiller
that returns "" or a stack trace on a Sunday-morning cron, while Brian is in Asia, is
indistinguishable from a quiet week — so every failure path is asserted to carry an
actionable message.
"""
import pytest

from src import config, distill

RUN = {
    "post_count": 3,
    "subreddits": ["LocalLLaMA"],
    "posts": [
        {
            "subreddit": "LocalLLaMA",
            "title": "Kimi K3 weights released",
            "score": 3144,
            "num_comments": 617,
            "permalink": "https://reddit.com/r/LocalLLaMA/comments/x/",
            "selftext": "body text",
            "top_comments": [{"author": "u", "score": 569, "body": "a comment"}],
        }
    ],
}


def test_prompt_carries_posts_scores_and_comments():
    p = distill.build_prompt(RUN)
    assert "Kimi K3 weights released" in p
    assert "score 3144" in p
    assert "a comment" in p
    assert "LocalLLaMA" in p


def test_prompt_is_truncated_to_the_cap():
    big = {**RUN, "posts": [{**RUN["posts"][0], "selftext": "x" * 10_000}] * 200}
    assert len(distill.build_prompt(big)) <= 45_000 + 500  # body cap + header slack


def test_unknown_backend_names_the_valid_ones():
    with pytest.raises(distill.DistillError) as e:
        distill.distill(RUN, backend="gpt5-please")
    assert "router" in str(e.value) and "opencode" in str(e.value)


def test_backend_selection_is_case_insensitive(monkeypatch):
    monkeypatch.setitem(distill._BACKENDS, "router", lambda prompt: "OK")
    assert distill.distill(RUN, backend="ROUTER") == ("OK", "")


def test_explicit_backend_overrides_config(monkeypatch):
    monkeypatch.setattr(config, "DISTILL_BACKEND", "router")
    monkeypatch.setitem(distill._BACKENDS, "opencode", lambda prompt: "FROM_OPENCODE")
    assert distill.distill(RUN, backend="opencode")[0] == "FROM_OPENCODE"


# ── opencode preflight ────────────────────────────────────────────────────────

def test_opencode_missing_binary_gives_actionable_error(monkeypatch):
    """OpenCode isn't installed yet (true as of 2026-07-29), so this is the path the
    first real attempt will hit. It must say what to do, not raise FileNotFoundError."""
    monkeypatch.setattr(distill.shutil, "which", lambda _: None)
    with pytest.raises(distill.DistillError) as e:
        distill.distill(RUN, backend="opencode")
    msg = str(e.value)
    assert "not on PATH" in msg
    assert "DISTILL_BACKEND=router" in msg  # names the escape hatch


def test_opencode_nonzero_exit_surfaces_stderr(monkeypatch):
    class P:
        returncode = 2
        stdout = ""
        stderr = "no such model: bogus"

    monkeypatch.setattr(distill.shutil, "which", lambda _: "/usr/local/bin/opencode")
    monkeypatch.setattr(distill.subprocess, "run", lambda *a, **k: P())
    with pytest.raises(distill.DistillError) as e:
        distill.distill(RUN, backend="opencode")
    assert "no such model" in str(e.value)


def test_opencode_empty_output_is_an_error_not_an_empty_digest(monkeypatch):
    """An empty digest would deliver a blank note and look like a quiet week."""
    class P:
        returncode = 0
        stdout = "   \n"
        stderr = ""

    monkeypatch.setattr(distill.shutil, "which", lambda _: "/usr/local/bin/opencode")
    monkeypatch.setattr(distill.subprocess, "run", lambda *a, **k: P())
    with pytest.raises(distill.DistillError) as e:
        distill.distill(RUN, backend="opencode")
    assert "empty" in str(e.value).lower()


def test_opencode_passes_model_and_system_prompt(monkeypatch):
    seen = {}

    class P:
        returncode = 0
        stdout = "# Digest"
        stderr = ""

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        seen["timeout"] = kw.get("timeout")
        return P()

    monkeypatch.setattr(distill.shutil, "which", lambda _: "/usr/local/bin/opencode")
    monkeypatch.setattr(distill.subprocess, "run", fake_run)
    monkeypatch.setattr(config, "OPENCODE_MODEL", "openrouter/anthropic/claude-sonnet-4")
    monkeypatch.setattr(config, "OPENCODE_TIMEOUT", 900)

    assert distill.distill(RUN, backend="opencode")[0] == "# Digest"
    assert seen["cmd"][1] == "run"
    assert "--model" in seen["cmd"]
    assert "openrouter/anthropic/claude-sonnet-4" in seen["cmd"]
    assert "===JARVIS===" in seen["cmd"][-1]  # system prompt travels with the message
    assert seen["timeout"] == 900


def test_opencode_timeout_suggests_the_fix(monkeypatch):
    import subprocess as sp

    def boom(*a, **k):
        raise sp.TimeoutExpired(cmd="opencode", timeout=900)

    monkeypatch.setattr(distill.shutil, "which", lambda _: "/usr/local/bin/opencode")
    monkeypatch.setattr(distill.subprocess, "run", boom)
    with pytest.raises(distill.DistillError) as e:
        distill.distill(RUN, backend="opencode")
    assert "OPENCODE_TIMEOUT" in str(e.value)


# ── router ────────────────────────────────────────────────────────────────────

def test_router_unreachable_explains_the_docker_host_trap(monkeypatch):
    """host.docker.internal resolving only inside Docker has bitten this project
    before; the error should say so rather than just 'connection refused'."""
    import httpx

    class C:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def post(self, *a, **k):
            raise httpx.ConnectError("nope")

    monkeypatch.setattr(distill.httpx, "Client", C)
    with pytest.raises(distill.DistillError) as e:
        distill.distill(RUN, backend="router")
    assert "Tailscale" in str(e.value)


# ── the one-generation / two-artifact split ───────────────────────────────────

def test_split_separates_human_and_machine():
    raw = "## The Week\n- a thing\n\n===JARVIS===\n\n```yaml\nsignal_count: 2\n```"
    human, machine = distill.split_output(raw)
    assert human == "## The Week\n- a thing"
    assert machine.startswith("```yaml")
    assert "===JARVIS===" not in human and "===JARVIS===" not in machine


def test_missing_marker_keeps_the_prose_rather_than_failing():
    """The human digest is the artifact that must always survive. A model that forgets
    the marker should cost us the machine layer, not the whole weekly run."""
    human, machine = distill.split_output("## Just prose, no marker")
    assert human == "## Just prose, no marker"
    assert machine == ""


def test_only_the_first_marker_splits():
    """A marker quoted inside the YAML must not produce a third fragment."""
    raw = "prose\n===JARVIS===\nyaml: 1\n# mentions ===JARVIS=== again"
    human, machine = distill.split_output(raw)
    assert human == "prose"
    assert "yaml: 1" in machine


def test_split_is_applied_by_distill(monkeypatch):
    monkeypatch.setitem(distill._BACKENDS, "router",
                        lambda p: "HUMAN\n===JARVIS===\nMACHINE")
    assert distill.distill(RUN, backend="router") == ("HUMAN", "MACHINE")


# ── the externalised persona ──────────────────────────────────────────────────

def test_prompt_file_exists_and_is_loaded():
    """The persona lives in prompts/digest.md so it can be iterated without a code
    change. If it goes missing every backend fails identically, so fail loudly here."""
    text = distill.load_system_prompt()
    assert distill.SPLIT_MARKER in text, "the prompt must tell the model the delimiter"
    assert len(text) > 1000


def test_prompt_teaches_the_honest_null():
    """The single most important instruction: a week with nothing worth acting on must
    say so. Without it the digest manufactures signal and trains Brian to skim."""
    text = distill.load_system_prompt().lower()
    assert "null_result" in text
    assert "never manufacture" in text


def test_prompt_forbids_guessing_the_readers_projects():
    """The bot stays general; relevance matching happens in the vault where the context
    lives. Coupling them would ship Brian's project list to a cloud runner."""
    text = distill.load_system_prompt().lower()
    assert "do not guess at the reader's projects" in text


def test_missing_base_prompt_is_an_actionable_error(monkeypatch, tmp_path):
    monkeypatch.setattr(distill, "BASE_PROMPT_PATH", tmp_path / "nope.md")
    with pytest.raises(distill.DistillError) as e:
        distill.load_system_prompt()
    assert "shared analyst contract" in str(e.value)


def test_unknown_focus_names_the_available_ones(monkeypatch):
    """A typo'd --focus must say what IS available, not just fail. With several digest
    topics in one repo, "no such focus" alone is a guessing game."""
    with pytest.raises(distill.DistillError) as e:
        distill.load_system_prompt("no-such-topic")
    msg = str(e.value)
    assert "no-such-topic" in msg and "ai-technique" in msg


def test_focus_block_comes_after_the_base_so_it_wins():
    """Composition order is load-bearing: the base states general rules, the focus
    overrides emphasis and section order. Later instructions win, so focus goes last."""
    text = distill.load_system_prompt("ai-technique")
    assert text.index("Shared Analyst Contract") < text.index("FOCUS BLOCK")


def test_every_focus_block_can_compose():
    """Every focus file on disk must actually build a prompt — a topic that only fails
    at 2am on cron night is the failure mode this repo keeps re-learning."""
    for f in sorted((distill.PROMPTS_DIR / "focus").glob("*.md")):
        assert len(distill.load_system_prompt(f.stem)) > 1000, f.stem


def test_prompt_keeps_consensus_field_pointed_at_the_corpus():
    """`challenges_consensus` must never become a licence to guess the reader's beliefs.
    The earlier field name (`contradicts`) was one word from inviting exactly that, and
    only an unrelated rule elsewhere in the prompt prevented it. Lock the narrowing."""
    text = distill.load_system_prompt()
    assert "challenges_consensus" in text
    assert "never about the reader" in text.lower()
    # Scope the ban to the SCHEMA block — rule 8 deliberately quotes the old name while
    # explaining why it was dropped, and that prose must not trip the check.
    schema = text.split("### Rules for Part 2")[0]
    assert "contradicts:" not in schema, "the old ambiguous field name is back in the schema"
