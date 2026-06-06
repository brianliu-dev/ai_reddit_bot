"""Pipeline orchestrator: ingest → store raw → distill (via my_intern) → deliver.

Run once per invocation. Schedule weekly with cron (see README).
"""
from __future__ import annotations

import sys

from . import deliver, distill, ingest, storage


def run() -> int:
    print("[1/4] Pulling Reddit…", flush=True)
    pull = ingest.pull()
    print(f"      {pull['post_count']} posts across {len(pull['subreddits'])} subs", flush=True)

    print("[2/4] Saving raw JSON…", flush=True)
    raw_path = storage.save_raw(pull)
    print(f"      {raw_path}", flush=True)

    if pull["post_count"] == 0:
        print("      no posts met the threshold — stopping before distillation.", flush=True)
        return 0

    print("[3/4] Distilling via my_intern…", flush=True)
    digest = distill.distill(pull)

    print("[4/4] Delivering…", flush=True)
    note = deliver.write_obsidian_note(digest, pull)
    print(f"      obsidian: {note or 'skipped (OBSIDIAN_VAULT_DIR unset)'}", flush=True)
    tg = deliver.push_telegram(digest)
    print(f"      telegram: {'sent' if tg else 'skipped (no token/chat id)'}", flush=True)

    print("done.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(run())
