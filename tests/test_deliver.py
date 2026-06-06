"""Unit tests for the message-chunking logic — no network.

Run from repo root:  pip install -r requirements-dev.txt && pytest
"""
from src.deliver import _chunks


def test_short_text_single_chunk():
    assert _chunks("hello\nworld\n", 4000) == ["hello\nworld\n"]


def test_chunks_respect_size():
    text = "".join(f"line {i}\n" for i in range(2000))
    chunks = _chunks(text, 200)
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks) == text          # nothing lost or reordered


def test_overlong_single_line_is_hard_split():
    text = "x" * 1000 + "\n"
    chunks = _chunks(text, 200)
    assert all(len(c) <= 200 for c in chunks)
    assert "".join(chunks) == text


def test_empty_text():
    assert _chunks("", 200) == []
