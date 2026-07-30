"""Pipeline orchestrator: ingest → store raw → distill → deliver.

Run once per invocation. Schedule weekly with cron (see README).

Flags (all optional — no flags = full pull→distill→deliver run):
  --ingest-only          pull + save raw JSON, then stop (no LLM backend needed)
  --from-raw YYYY-MM-DD  skip the pull; distill+deliver from a saved raw file
  --dry-run              do everything except deliver — print the digest to stdout
  --backend NAME         override DISTILL_BACKEND for this run (router | opencode)
"""
from __future__ import annotations

import argparse
import datetime as dt
import sys

from . import config, deliver, distill, ingest, storage


def run(
    ingest_only: bool = False,
    from_raw: str | None = None,
    dry_run: bool = False,
    backend: str | None = None,
) -> int:
    if from_raw:
        when = dt.date.fromisoformat(from_raw)
        print(f"[1/4] Loading saved raw for {when}…", flush=True)
        pull = storage.load_raw(when)
        print(f"      {pull['post_count']} posts (skipping the live pull)", flush=True)
    else:
        print("[1/4] Pulling Reddit via Arctic Shift…", flush=True)
        pull = ingest.pull()
        print(
            f"      {pull['post_count']} posts across {len(pull['subreddits'])} subs",
            flush=True,
        )
        if pull.get("errors"):
            # Loud, not fatal: a partial pull still makes a digest, but it must never
            # be mistaken for a complete one.
            print(f"      ⚠️  {len(pull['errors'])} source error(s) — digest will be partial",
                  flush=True)

        print("[2/4] Saving raw JSON…", flush=True)
        raw_path = storage.save_raw(pull)
        print(f"      {raw_path}", flush=True)

    if ingest_only:
        print("ingest-only: stopping before distillation.", flush=True)
        return 0

    if pull["post_count"] == 0:
        print("      no posts met the threshold — stopping before distillation.", flush=True)
        return 0

    engine = (backend or config.DISTILL_BACKEND).lower()
    print(f"[3/4] Distilling via {engine}…", flush=True)
    try:
        digest, machine = distill.distill(pull, backend=engine)
        if machine:
            print(f"      digest {len(digest):,} chars + machine layer {len(machine):,} chars",
                  flush=True)
        else:
            # Not fatal — the prose is the part that must survive — but say it out loud,
            # because a missing machine layer means the vault gets nothing to act on.
            print("      ⚠️  no machine layer (model omitted the ===JARVIS=== marker)",
                  flush=True)
    except distill.DistillError as exc:
        # The raw pull is already saved, so this is recoverable: fix the backend and
        # re-run with --from-raw instead of re-hitting the archive for the same week.
        print(f"\n❌ Distillation failed:\n{exc}\n", file=sys.stderr)
        when = dt.date.today().isoformat()
        print(f"The raw pull is saved — retry without re-pulling:\n"
              f"  python -m src.main --from-raw {when} --dry-run", file=sys.stderr)
        return 1

    if dry_run:
        print("[4/4] Dry run — output below (not delivered):\n", flush=True)
        print(digest)
        if machine:
            print(f"\n{distill.SPLIT_MARKER}\n")
            print(machine)
        return 0

    print("[4/4] Delivering…", flush=True)
    note = deliver.write_obsidian_note(digest, pull)
    print(f"      obsidian: {note or 'skipped (OBSIDIAN_VAULT_DIR unset)'}", flush=True)
    signal = deliver.write_jarvis_signal(machine, pull)
    print(f"      signal:   {signal or 'skipped (OBSIDIAN_VAULT_DIR unset)'}", flush=True)
    try:
        mailed = deliver.send_email(digest, pull)
        print(f"      email:    {'sent to ' + config.EMAIL_TO if mailed else 'skipped (SMTP not configured)'}",
              flush=True)
    except Exception as exc:
        # The note is already on disk; losing the email must not fail the whole run.
        print(f"      email:    ❌ failed ({exc})", flush=True)

    if not note and not config.EMAIL_TO:
        print("      ⚠️  no sink configured — the digest went nowhere. Set "
              "OBSIDIAN_VAULT_DIR and/or SMTP_HOST+EMAIL_TO.", flush=True)

    print("done.", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ai_reddit_bot weekly digest pipeline")
    parser.add_argument("--ingest-only", action="store_true",
                        help="pull + save raw JSON, then stop (no LLM backend needed)")
    parser.add_argument("--from-raw", metavar="YYYY-MM-DD",
                        help="skip the pull; distill+deliver from a saved raw file")
    parser.add_argument("--dry-run", action="store_true",
                        help="do everything except deliver — print the digest")
    parser.add_argument("--backend", choices=["router", "opencode"],
                        help="override DISTILL_BACKEND for this run")
    args = parser.parse_args()
    return run(
        ingest_only=args.ingest_only,
        from_raw=args.from_raw,
        dry_run=args.dry_run,
        backend=args.backend,
    )


if __name__ == "__main__":
    sys.exit(main())
