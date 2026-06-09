#!/usr/bin/env python3
"""Backfill CLI — process all unprocessed Postgres rows through the pipeline."""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

sys.path.insert(0, "transform_service")

from tqdm import tqdm

import classifier
import db
import extractor
import graph_builder
import jira_pusher
from extractor import LowConfidenceError


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Backfill unprocessed staging rows into Memgraph")
    p.add_argument("--source", choices=["EMAIL", "CALENDAR", "ALL"], default="ALL")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--since", type=str, default=None, help="YYYY-MM-DD — only rows after this date")
    p.add_argument("--verbose", action="store_true")
    return p.parse_args()


def run_emails(limit: int, dry_run: bool, verbose: bool) -> dict:
    emails = db.get_unprocessed_emails(limit=limit or 10_000)
    stats = {"total": len(emails), "processed": 0, "skipped": 0, "errors": 0, "duration": 0.0}
    t0 = time.monotonic()

    for email in tqdm(emails, desc="Emails", disable=not verbose):
        try:
            clf = classifier.classify(email)
            if not clf.is_meeting or clf.is_invite:
                if not dry_run:
                    db.mark_email_processed(email.message_id, success=True)
                stats["skipped"] += 1
                continue

            meeting = extractor.extract_sync(email)
            import asyncio
            asyncio.run(graph_builder.build_graph(meeting, email.message_id, dry_run=dry_run))

            if not dry_run:
                db.mark_email_processed(email.message_id, success=True)
            stats["processed"] += 1

            if verbose:
                print(f"  ✓ {email.subject[:60]}")

        except LowConfidenceError:
            stats["skipped"] += 1
        except Exception as exc:
            stats["errors"] += 1
            if not dry_run:
                db.mark_email_processed(email.message_id, success=False, error_msg=str(exc))
            if verbose:
                print(f"  ✗ {email.message_id}: {exc}")

    stats["duration"] = time.monotonic() - t0
    return stats


def main() -> None:
    args = parse_args()
    results = {}

    if args.source in ("EMAIL", "ALL"):
        results["Email"] = run_emails(args.limit or 10_000, args.dry_run, args.verbose)

    print("\n" + "─" * 60)
    print(f"{'Source':<12} {'Total':>6} {'Processed':>10} {'Skipped':>8} {'Errors':>7} {'Duration':>10}")
    print("─" * 60)
    for source, s in results.items():
        print(f"{source:<12} {s['total']:>6} {s['processed']:>10} {s['skipped']:>8} {s['errors']:>7} {s['duration']:>9.1f}s")
    print("─" * 60)
    if args.dry_run:
        print("(dry run — no writes performed)")


if __name__ == "__main__":
    main()
