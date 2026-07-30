"""Unit tests for delivery — no network.

The Telegram chunking tests that used to live here were removed with Telegram itself
(2026-07-29). What replaced them tests the two current sinks: the Obsidian note's
structure, and email's refusal to half-configure itself.

Run from repo root:  pip install -r requirements-dev.txt && pytest
"""
import datetime as dt

import pytest

from src import config, deliver

WHEN = dt.date(2026, 7, 29)


def _run(**over):
    run = {
        "source": "arctic_shift",
        "subreddits": ["LocalLLaMA", "ClaudeAI"],
        "post_count": 24,
        "errors": [],
    }
    run.update(over)
    return run


# ── Obsidian note ─────────────────────────────────────────────────────────────

def test_obsidian_skipped_when_unconfigured(monkeypatch):
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", "")
    assert deliver.write_obsidian_note("# digest", _run(), WHEN) is None


def test_obsidian_note_written_with_frontmatter(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", str(tmp_path))
    path = deliver.write_obsidian_note("## The Week in 3 Lines\n- a\n", _run(), WHEN)

    assert path == tmp_path / "AI Reddit Digest 2026-07-29.md"
    text = path.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "name: ai-reddit-digest-2026-07-29" in text
    assert "post_count: 24" in text
    assert "subreddits: [LocalLLaMA, ClaudeAI]" in text
    assert "## The Week in 3 Lines" in text  # body survives intact


def test_obsidian_note_has_alias_matching_name(monkeypatch, tmp_path):
    """The two-resolver rule: this vault links by `name:`, Obsidian resolves by
    filename/alias. A note carrying only one of them ghosts in the graph, which
    Jarvis's graph_health.py treats as a hard failure — so assert both agree."""
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", str(tmp_path))
    text = deliver.write_obsidian_note("x", _run(), WHEN).read_text(encoding="utf-8")
    assert "name: ai-reddit-digest-2026-07-29" in text
    assert "aliases: [ai-reddit-digest-2026-07-29]" in text


def test_partial_pull_is_visible_in_the_note(monkeypatch, tmp_path):
    """A digest built from 5 of 7 subs looks identical to a complete one unless the
    failure is written down. That silence is the bug this guards."""
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", str(tmp_path))
    run = _run(errors=["LangChain: Arctic Shift unreachable after 4 attempts"])
    text = deliver.write_obsidian_note("x", run, WHEN).read_text(encoding="utf-8")
    assert "Partial pull" in text
    assert "LangChain" in text


def test_clean_pull_has_no_partial_warning(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", str(tmp_path))
    text = deliver.write_obsidian_note("x", _run(), WHEN).read_text(encoding="utf-8")
    assert "Partial pull" not in text


def test_vault_dir_is_created_if_missing(monkeypatch, tmp_path):
    target = tmp_path / "library" / "reddit"
    monkeypatch.setattr(config, "OBSIDIAN_VAULT_DIR", str(target))
    path = deliver.write_obsidian_note("x", _run(), WHEN)
    assert path.exists()


# ── Email ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("host,to,frm", [
    ("", "a@b.com", "c@d.com"),      # no host
    ("smtp.x.com", "", "c@d.com"),   # no recipient
    ("smtp.x.com", "a@b.com", ""),   # no sender
])
def test_email_skipped_when_partially_configured(monkeypatch, host, to, frm):
    """Half-configured email must return False, not raise and not silently 'succeed'."""
    monkeypatch.setattr(config, "SMTP_HOST", host)
    monkeypatch.setattr(config, "EMAIL_TO", to)
    monkeypatch.setattr(config, "EMAIL_FROM", frm)
    assert deliver.send_email("# digest", _run(), WHEN) is False


def test_email_sends_when_configured(monkeypatch):
    """Verify the message we hand to SMTP, without touching a network."""
    sent = {}

    class FakeSMTP:
        def __init__(self, host, port):
            sent["host"], sent["port"] = host, port

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def ehlo(self):
            pass

        def starttls(self, context=None):
            sent["starttls"] = True

        def login(self, u, p):
            sent["login"] = u

        def send_message(self, msg):
            sent["msg"] = msg

    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_PORT", 587)
    monkeypatch.setattr(config, "SMTP_USER", "me@example.com")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "pw")
    monkeypatch.setattr(config, "EMAIL_FROM", "me@example.com")
    monkeypatch.setattr(config, "EMAIL_TO", "you@example.com")
    monkeypatch.setattr(deliver.smtplib, "SMTP", FakeSMTP)

    assert deliver.send_email("## body here", _run(), WHEN) is True
    assert sent["starttls"] is True
    assert sent["login"] == "me@example.com"
    msg = sent["msg"]
    assert msg["To"] == "you@example.com"
    assert "2026-07-29" in msg["Subject"]
    body = msg.get_content()
    assert "## body here" in body
    assert "LocalLLaMA" in body  # header context travels with the digest


def test_email_port_465_uses_implicit_tls(monkeypatch):
    """Port 465 must use SMTP_SSL, not STARTTLS — getting this backwards fails at
    connect time against Gmail, and only in production."""
    used = {}

    class FakeSMTPSSL:
        def __init__(self, host, port, context=None):
            used["ssl"] = True

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def login(self, u, p):
            pass

        def send_message(self, msg):
            pass

    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "SMTP_PORT", 465)
    monkeypatch.setattr(config, "SMTP_USER", "me@example.com")
    monkeypatch.setattr(config, "SMTP_PASSWORD", "pw")
    monkeypatch.setattr(config, "EMAIL_FROM", "me@example.com")
    monkeypatch.setattr(config, "EMAIL_TO", "you@example.com")
    monkeypatch.setattr(deliver.smtplib, "SMTP_SSL", FakeSMTPSSL)

    assert deliver.send_email("x", _run(), WHEN) is True
    assert used.get("ssl") is True
