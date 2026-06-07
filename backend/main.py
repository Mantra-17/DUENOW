"""
main.py — ClassFlow Watcher (Sync Engine)
─────────────────────────────────────────────────────────────────────────────
Industry-grade Google Classroom polling daemon.

Features
────────
- Uses shared DB connection pool from db.py (no per-call connect()).
- UPSERT (INSERT … ON CONFLICT DO NOTHING) to be race-condition-safe.
- Graceful shutdown on SIGINT/SIGTERM — finishes current sync before exiting.
- Per-course error isolation — one bad course never stops others.
- AI analysis with full retry/backoff (handled in ai_helper.py).
- Configurable poll interval and exponential back-off on crash.
- Startup validation of all required env vars.
- Mock sync mode when credentials.json is absent (for local dev).
"""

from __future__ import annotations

import logging
import os
import signal
import sys
import time
from datetime import date

from dotenv import load_dotenv

load_dotenv()

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
import psycopg2

from db import get_conn, init_db
from ai_helper import analyze_assignment

# ─────────────────────────────────────────────────────────────────────────────
# Logging
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logging.getLogger("ai_helper").setLevel(logging.DEBUG)
logger = logging.getLogger("classflow.main")

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly",
]

POLL_INTERVAL_SECONDS = int(os.getenv("POLL_INTERVAL_SECONDS", "3600"))
TOKEN_PATH            = os.getenv("TOKEN_PATH", "token.json")
CREDENTIALS_PATH      = os.getenv("CREDENTIALS_PATH", "credentials.json")

# Crash backoff: starts at 5 min, doubles up to 30 min
CRASH_BACKOFF_INITIAL = 300   # 5 minutes
CRASH_BACKOFF_MAX     = 1800  # 30 minutes

# ─────────────────────────────────────────────────────────────────────────────
# Graceful shutdown
# ─────────────────────────────────────────────────────────────────────────────

_shutdown = False


def _handle_signal(signum: int, _frame) -> None:
    global _shutdown
    logger.info(f"Received signal {signum} — will stop after current sync.")
    _shutdown = True


try:
    signal.signal(signal.SIGINT,  _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)
except ValueError:
    logger.debug("Signal handlers could not be set. This is expected if not running in the main thread.")



# ─────────────────────────────────────────────────────────────────────────────
# Google Classroom auth
# ─────────────────────────────────────────────────────────────────────────────

def get_classroom_service():
    """
    Return an authenticated Google Classroom API service.
    Refreshes or re-creates the OAuth token as needed.
    Saves credentials to TOKEN_PATH for reuse across restarts.
    """
    creds: Credentials | None = None

    if os.path.exists(TOKEN_PATH):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
            logger.debug("Loaded credentials from token file.")
        except Exception as e:
            logger.warning(f"Could not load token file: {e} — re-authenticating.")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            logger.info("Access token expired — refreshing …")
            try:
                creds.refresh(Request())
                logger.info("Token refreshed successfully.")
            except Exception as e:
                logger.error(f"Token refresh failed: {e} — re-running OAuth flow.")
                creds = None

        if not creds:
            logger.info("No valid token — starting OAuth flow …")
            flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
            creds = flow.run_local_server(port=0)
            logger.info("OAuth flow completed.")

        # Persist token
        try:
            with open(TOKEN_PATH, "w") as f:
                f.write(creds.to_json())
            logger.debug("Saved credentials to token file.")
        except OSError as e:
            logger.warning(f"Could not save token file: {e}")

    return build("classroom", "v1", credentials=creds)


# ─────────────────────────────────────────────────────────────────────────────
# DB helpers
# ─────────────────────────────────────────────────────────────────────────────

def parse_due_date(due: dict | None) -> date | None:
    """Convert Classroom's {year, month, day} dict to a Python date, or None."""
    if not due:
        return None
    try:
        return date(int(due["year"]), int(due["month"]), int(due["day"]))
    except (KeyError, ValueError, TypeError):
        return None


def sync_to_db(assignments: list[dict], course_name: str, user_id: str) -> tuple[int, int]:
    """
    Phase 1 — fast sync: store ALL new assignments immediately with placeholder data.
    Phase 2 — AI analysis runs separately via run_ai_batch_analysis() after sync.
    Returns (new_count, skipped_count).
    """
    new_count  = 0
    skip_count = 0

    for item in assignments:
        assignment_id = item.get("id", "").strip()
        title         = item.get("title", "(No Title)").strip()

        if not assignment_id:
            logger.warning(f"  Skipping item with no id: {item}")
            continue

        unique_id = f"{user_id}:{assignment_id}"

        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT id FROM assignments WHERE id = %s AND user_id = %s", (unique_id, user_id)
                    )
                    if cur.fetchone() is not None:
                        logger.debug(f'  Already stored: "{title}"')
                        skip_count += 1
                        continue

            due_date = parse_due_date(item.get("dueDate"))

            # Store with placeholder values — AI fills in the real data in Phase 2
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO assignments
                            (id, user_id, subject, title, due_date,
                             summary, difficulty, estimated_minutes,
                             classification, model_used, ai_success)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO NOTHING
                        """,
                        (
                            unique_id,
                            user_id,
                            course_name,
                            title,
                            due_date,
                            "AI analysis pending…",  # placeholder — filled by Phase 2
                            5,                        # placeholder difficulty
                            60,                       # placeholder minutes
                            "Other",                  # placeholder classification
                            None,                     # no model yet
                            False,                    # ai_success=False → Phase 2 picks it up
                        ),
                    )
                    inserted = cur.rowcount
                conn.commit()

            if inserted:
                new_count += 1
                logger.info(f'  ✅ Stored: "{title}"')
            else:
                skip_count += 1

        except psycopg2.Error as e:
            logger.error(f'  ❌ DB error on "{title}": {e}', exc_info=True)
        except Exception as e:
            logger.error(f'  ❌ Error on "{title}": {type(e).__name__}: {e}', exc_info=True)

    logger.info(f"  '{course_name}': {new_count} new stored, {skip_count} already existed.")
    return new_count, skip_count



# ─────────────────────────────────────────────────────────────────────────────
# Mock sync (for local dev without credentials.json)
# ─────────────────────────────────────────────────────────────────────────────

MOCK_COURSES = [
    {
        "name": "DSA",
        "courseWork": [
            {"id": "mock-dsa-avl",  "title": "Practical 5: Implement AVL Tree operations",       "dueDate": {"year": 2026, "month": 6, "day": 15}},
            {"id": "mock-dsa-cie1", "title": "CIE 1: Written test on Linked Lists & Stacks",     "dueDate": {"year": 2026, "month": 6, "day": 10}},
            {"id": "mock-dsa-sort", "title": "Assignment 2: Analysis of Sorting Algorithms",      "dueDate": {"year": 2026, "month": 6, "day": 20}},
            {"id": "mock-dsa-heap", "title": "Practical 6: Implement Min-Heap and Priority Queue","dueDate": {"year": 2026, "month": 6, "day": 22}},
        ],
    },
    {
        "name": "DAA",
        "courseWork": [
            {"id": "mock-daa-greedy",  "title": "Practical 4: Greedy Algorithms vs Dynamic Programming", "dueDate": {"year": 2026, "month": 6, "day": 18}},
            {"id": "mock-daa-proj1",   "title": "Project Phase 1 Submission: NP-Hard Problem",            "dueDate": {"year": 2026, "month": 6, "day": 25}},
            {"id": "mock-daa-cie2",    "title": "CIE 2: Divide and Conquer + Graph Algorithms",            "dueDate": {"year": 2026, "month": 6, "day": 28}},
        ],
    },
    {
        "name": "SE",
        "courseWork": [
            {"id": "mock-se-srs",  "title": "Assignment 1: Software Requirements Specification document", "dueDate": {"year": 2026, "month": 6, "day": 8}},
            {"id": "mock-se-uml",  "title": "Practical 3: Create UML diagrams for ClassFlow Watcher",    "dueDate": {"year": 2026, "month": 6, "day": 12}},
            {"id": "mock-se-test", "title": "Assignment 3: Write Unit Tests for a REST API",              "dueDate": {"year": 2026, "month": 7, "day": 3}},
        ],
    },
    {
        "name": "MP",
        "courseWork": [
            {"id": "mock-mp-8085", "title": "Practical 2: 8085 Assembly program for 16-bit addition", "dueDate": {"year": 2026, "month": 6, "day": 9}},
            {"id": "mock-mp-int",  "title": "Practical 3: Interrupt-driven I/O with 8085",            "dueDate": {"year": 2026, "month": 6, "day": 16}},
        ],
    },
    {
        "name": "CN",
        "courseWork": [
            {"id": "mock-cn-sock", "title": "Practical 4: TCP Socket Chat Application in Python", "dueDate": {"year": 2026, "month": 6, "day": 14}},
            {"id": "mock-cn-cie3", "title": "CIE 3: Network Layer Protocols and Routing",         "dueDate": {"year": 2026, "month": 6, "day": 30}},
        ],
    },
]


def run_mock_sync(user_id: str) -> None:
    logger.info(f"Running in MOCK sync mode for user {user_id}.")
    total_new = 0
    for course in MOCK_COURSES:
        name  = course["name"]
        items = course["courseWork"]
        logger.info(f"  ─── {name} (MOCK: {len(items)} items)")
        new, _ = sync_to_db(items, name, user_id)
        total_new += new
    logger.info(f"Mock sync complete for user {user_id} — {total_new} new tasks stored.")


# ─────────────────────────────────────────────────────────────────────────────
# Google Classroom fetch
# ─────────────────────────────────────────────────────────────────────────────

def run_classroom_sync(user_id: str, access_token: str) -> None:
    """Fetch all active courses and their courseWork from Google Classroom using user access token."""
    logger.info(f"Fetching course list from Google Classroom for user {user_id} …")

    try:
        creds = Credentials(token=access_token)
        service = build("classroom", "v1", credentials=creds)
    except Exception as e:
        logger.error(f"  ❌ Could not build Classroom service for user {user_id}: {e}", exc_info=True)
        return

    try:
        results = service.courses().list(pageSize=50).execute()
        courses = results.get("courses", [])
    except Exception as e:
        logger.error(f"  ❌ Failed to list courses for user {user_id}: {e}", exc_info=True)
        return

    active_courses = [
        c for c in courses
        if c.get("courseState") in ("ACTIVE", "PROVISIONED")
    ]
    logger.info(f"  Found {len(active_courses)}/{len(courses)} active course(s) for user {user_id}.")

    total_new = 0
    for course in active_courses:
        name = course.get("name", "Unknown")
        cid  = course.get("id", "")
        logger.info(f"  ─── {name} (id={cid})")

        try:
            work  = service.courses().courseWork().list(courseId=cid).execute()
            items = work.get("courseWork", [])
            logger.info(f"    Found {len(items)} courseWork item(s).")
            if items:
                new, _ = sync_to_db(items, name, user_id)
                total_new += new
        except Exception as e:
            logger.error(f'  ❌ Could not fetch work for "{name}": {e}', exc_info=True)

    logger.info(f"  Classroom sync complete for user {user_id} — {total_new} new tasks stored.")


def run_ai_batch_analysis(delay_seconds: float = 6.0) -> None:
    """
    Phase 2: AI-analyze all tasks with ai_success=False.
    Paced at delay_seconds apart to stay within free-tier limits (~10 RPM).
    """
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id, title, subject FROM assignments "
                    "WHERE ai_success = FALSE ORDER BY due_date ASC NULLS LAST"
                )
                pending = cur.fetchall()
    except Exception as e:
        logger.error(f"AI batch: could not fetch pending tasks: {e}")
        return

    if not pending:
        logger.info("AI batch: all tasks already analyzed ✅")
        return

    logger.info(f"🤖 AI batch: analyzing {len(pending)} task(s) at {delay_seconds}s/task …")
    success = failed = 0

    for i, (tid, title, subject) in enumerate(pending, 1):
        if _shutdown:
            break

        logger.info(f"  [{i}/{len(pending)}] {subject} | {title}")
        ai = analyze_assignment(title, subject)

        if ai.get("ai_success"):
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            """
                            UPDATE assignments
                            SET summary=%s, difficulty=%s, estimated_minutes=%s,
                                classification=%s, model_used=%s, ai_success=TRUE
                            WHERE id=%s
                            """,
                            (ai["summary"], ai["difficulty"], ai["estimated_minutes"],
                             ai["classification"], ai["model_used"], tid),
                        )
                    conn.commit()
                success += 1
                logger.info(
                    f"     ✅ {ai['classification']:12} | "
                    f"diff={ai['difficulty']}/10 | {ai['estimated_minutes']}min"
                )
            except Exception as e:
                logger.error(f"     ❌ DB update failed: {e}")
                failed += 1
        else:
            logger.warning(f"     ⚠️  AI failed — retries next cycle")
            failed += 1

        if i < len(pending) and not _shutdown:
            time.sleep(delay_seconds)

    logger.info(f"🤖 AI batch done: {success} analyzed, {failed} will retry next cycle")


def get_assignments() -> None:
    """
    Loop through all registered users in the database.
    Phase 1: Sync all data from Classroom or mock for each user.
    Phase 2: AI-analyze all pending tasks globally.
    """
    from auth import refresh_user_token

    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT id, name FROM users")
                users = cur.fetchall()
    except Exception as e:
        logger.error(f"Failed to fetch users for sync cycle: {e}")
        return

    if not users:
        logger.info("No registered users found in database. Skipping sync cycle.")
        return

    logger.info(f"Starting sync cycle for {len(users)} registered user(s)...")

    for user_id, name in users:
        if _shutdown:
            break
        
        logger.info(f"Syncing data for user: {name} (id={user_id})")
        if user_id.startswith("mock-"):
            try:
                run_mock_sync(user_id)
            except Exception as e:
                logger.error(f"Failed mock sync for user {name} ({user_id}): {e}", exc_info=True)
        else:
            try:
                token = refresh_user_token(user_id)
                if token:
                    run_classroom_sync(user_id, token)
                else:
                    logger.error(f"Could not refresh access token for Google user {name} ({user_id}). Sync skipped.")
            except Exception as e:
                logger.error(f"Failed Google Classroom sync for user {name} ({user_id}): {e}", exc_info=True)

    # Phase 2: AI-analyze all tasks globally that are pending (ai_success=False)
    if os.getenv("GEMINI_API_KEY"):
        run_ai_batch_analysis(delay_seconds=6.0)
    else:
        logger.warning("GEMINI_API_KEY not set — skipping AI analysis phase")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

def main() -> None:
    logger.info("══════════════════════════════════════════════════════")
    logger.info("  ClassFlow Watcher  —  Sync Engine Starting")
    logger.info("══════════════════════════════════════════════════════")

    # Validate required env vars
    missing = [v for v in ("DATABASE_URL", "GEMINI_API_KEY") if not os.getenv(v)]
    if missing:
        logger.critical(f"❌ Missing required environment variables: {missing}")
        sys.exit(1)

    # Initialise DB schema
    try:
        init_db()
    except Exception as e:
        logger.critical(f"❌ DB init failed: {e}", exc_info=True)
        sys.exit(1)

    crash_backoff = CRASH_BACKOFF_INITIAL

    while not _shutdown:
        logger.info("⏳ Starting sync cycle …")

        try:
            get_assignments()
            crash_backoff = CRASH_BACKOFF_INITIAL  # Reset backoff on success
            logger.info(
                f"✅ Sync complete. Next run in "
                f"{POLL_INTERVAL_SECONDS // 60} min …"
            )

            # Interruptible sleep: check _shutdown every second
            for _ in range(POLL_INTERVAL_SECONDS):
                if _shutdown:
                    break
                time.sleep(1)

        except Exception as e:
            logger.critical(
                f"💥 Sync cycle crashed: {type(e).__name__}: {e}", exc_info=True
            )
            logger.info(f"Retrying in {crash_backoff // 60} min …")
            time.sleep(crash_backoff)
            crash_backoff = min(crash_backoff * 2, CRASH_BACKOFF_MAX)

    logger.info("🛑 Shutdown signal received — ClassFlow Watcher stopped cleanly.")


if __name__ == "__main__":
    main()
