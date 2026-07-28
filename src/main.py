"""Pipeline orchestrator: ingest → store raw → distill (via my_intern) → deliver.

Run once per invocation. Schedule weekly with cron (see README).

Flags (all optional — no flags = full pull→distill→deliver run):
  --ingest-only        pull Reddit + save raw JSON, then stop (no router needed)
  --from-raw YYYY-MM-DD  skip the pull; distill+deliver from a saved raw file
  --dry-run            do everything except deliver — print the digest to stdout
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import deliver, distill, ingest, storage


def run(ingest_only: bool = False, from_raw: str | None = None, dry_run: bool = False) -> int:
    if from_raw:
        when = dt.date.fromisoformat(from_raw)
        print(f"[1/4] Loading saved raw for {when}…", flush=True)
        pull = storage.load_raw(when)
        print(f"      {pull['post_count']} posts (skipping the live pull)", flush=True)
    else:
        print("[1/4] Pulling Reddit…", flush=True)
        pull = ingest.pull()
        print(f"      {pull['post_count']} posts across {len(pull['subreddits'])} subs", flush=True)

        print("[2/4] Saving raw JSON…", flush=True)
        raw_path = storage.save_raw(pull)
        print(f"      {raw_path}", flush=True)

    if ingest_only:
        print("ingest-only: stopping before distillation.", flush=True)
        return 0

    if pull["post_count"] == 0:
        print("      no posts met the threshold — stopping before distillation.", flush=True)
        return 0

    print("[3/4] Distilling via my_intern…", flush=True)
    digest = distill.distill(pull)

    if dry_run:
        print("[4/4] Dry run — digest below (not delivered):\n", flush=True)
        print(digest)
        return 0

    print("[4/4] Delivering…", flush=True)
    note = deliver.write_obsidian_note(digest, pull)
    print(f"      obsidian: {note or 'skipped (OBSIDIAN_VAULT_DIR unset)'}", flush=True)
    tg = deliver.push_telegram(digest)
    print(f"      telegram: {'sent' if tg else 'skipped (no token/chat id)'}", flush=True)

    print("done.", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ai_reddit_bot weekly digest pipeline")
    parser.add_argument("--ingest-only", action="store_true",
                        help="pull + save raw JSON, then stop (no router needed)")
    parser.add_argument("--from-raw", metavar="YYYY-MM-DD",
                        help="skip the pull; distill+deliver from a saved raw file")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything except deliver — print the digest")
    args = parser.parse_args()
    return run(ingest_only=args.ingest_only, from_raw=args.from_raw, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
