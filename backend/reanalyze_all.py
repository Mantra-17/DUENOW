"""
reanalyze_all.py — ClassFlow Watcher
─────────────────────────────────────────────────────────────────────────────
One-shot script to re-run AI analysis on every task that has ai_success=False.

Run this whenever:
- You first set up the project (mock tasks have no AI analysis)
- The AI model changes and you want fresh classifications
- Some tasks failed AI analysis during import

Usage:
    .venv/bin/python reanalyze_all.py
    .venv/bin/python reanalyze_all.py --force   # re-analyze ALL tasks, even successful ones
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from dotenv import load_dotenv
load_dotenv()

from db import get_conn, init_db
from ai_helper import analyze_assignment
from psycopg2.extras import RealDictCursor

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("reanalyze")


def run(force: bool = False) -> None:
    # Validate env
    if not os.getenv("GEMINI_API_KEY"):
        logger.error("❌ GEMINI_API_KEY is not set. Add it to your .env file.")
        sys.exit(1)
    if not os.getenv("DATABASE_URL"):
        logger.error("❌ DATABASE_URL is not set.")
        sys.exit(1)

    init_db()

    # Fetch tasks that need analysis
    filter_clause = "" if force else "WHERE ai_success = FALSE"
    with get_conn() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(
                f"SELECT id, title, subject FROM assignments {filter_clause} ORDER BY due_date ASC NULLS LAST"
            )
            tasks = [dict(r) for r in cur.fetchall()]

    total = len(tasks)
    if total == 0:
        logger.info("✅ All tasks already have AI analysis — nothing to do!")
        logger.info("   Run with --force to re-analyze everything.")
        return

    logger.info(f"Found {total} task(s) {'without' if not force else ''} AI analysis → starting now")
    logger.info("─" * 60)

    success = 0
    failed  = 0

    for i, task in enumerate(tasks, 1):
        tid     = task["id"]
        title   = task["title"]
        subject = task["subject"]

        logger.info(f"[{i}/{total}] 🤖 {subject} | {title}")

        ai = analyze_assignment(title, subject)

        if ai.get("ai_success"):
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE assignments
                            SET summary           = %s,
                                difficulty        = %s,
                                estimated_minutes = %s,
                                classification    = %s,
                                model_used        = %s,
                                ai_success        = TRUE
                            WHERE id = %s
                            """,
                            (
                                ai["summary"],
                                ai["difficulty"],
                                ai["estimated_minutes"],
                                ai["classification"],
                                ai["model_used"],
                                tid,
                            ),
                        )
                    conn.commit()

                logger.info(
                    f"     ✅ {ai['classification']:12} | "
                    f"diff={ai['difficulty']}/10 | "
                    f"{ai['estimated_minutes']}min | "
                    f"model={ai['model_used']}"
                )
                logger.info(f"     📝 {ai['summary']}")
                success += 1

            except Exception as e:
                logger.error(f"     ❌ DB update failed: {e}")
                failed += 1
        else:
            logger.warning(f"     ⚠️  AI failed — keeping existing data")
            failed += 1

        # Small delay to avoid hammering the API
        if i < total:
            time.sleep(0.5)

        logger.info("")

    logger.info("─" * 60)
    logger.info(f"Done! ✅ {success}/{total} tasks re-analyzed | {failed} failed")

    if failed > 0:
        logger.warning(f"  {failed} task(s) failed — check your GEMINI_API_KEY and rate limits")
        logger.warning("  Re-run the script to retry failed tasks")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Re-analyze tasks with Gemini AI")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-analyze ALL tasks, even those already analyzed",
    )
    args = parser.parse_args()
    run(force=args.force)
