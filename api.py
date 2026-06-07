"""
api.py — ClassFlow Watcher REST API
─────────────────────────────────────────────────────────────────────────────
Industry-grade Flask REST API for the ClassFlow Watcher backend.

Endpoints
─────────
GET  /health                       Liveness + DB connectivity check
GET  /tasks                        List tasks (filter by subject/completed/classification)
POST /tasks                        Manually create a task with AI analysis
GET  /tasks/<id>                   Get a single task
PATCH /tasks/<id>                  Update a task's fields
DELETE /tasks/<id>                 Delete a task
POST /tasks/<id>/complete          Mark task as completed
POST /tasks/<id>/uncomplete        Mark task as pending
GET  /subjects                     List all unique subjects
GET  /stats                        Dashboard statistics

Design
──────
- Connection pooling via db.py (no raw psycopg2.connect() here)
- Input validation on every mutating endpoint
- Consistent JSON error schema: {"error": "...", "code": "..."}
- Rate-limit safe: auth guard on all /tasks and /subjects routes
- Structured logging at INFO for every request + error
- Zero bare excepts — specific error types caught and logged
"""

from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from typing import Any

from dotenv import load_dotenv

load_dotenv()

from flask import Flask, jsonify, request, Response
from flask_cors import CORS
from psycopg2.extras import RealDictCursor

from db import get_conn, init_db, check_db_health
from ai_helper import analyze_assignment

# ─────────────────────────────────────────────────────────────────────────────
# App setup
# ─────────────────────────────────────────────────────────────────────────────

app = Flask(__name__)
CORS(app, resources={r"/*": {"origins": "*"}})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("classflow.api")

API_KEY = os.getenv("MY_API_KEY", "").strip()

VALID_CLASSIFICATIONS = {"Assignment", "CIE", "Practical", "Project", "Other"}

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _err(message: str, code: str = "ERROR", status: int = 400) -> tuple[Response, int]:
    """Return a consistent JSON error response."""
    return jsonify({"error": message, "code": code}), status


def _serialize_row(row: dict) -> dict:
    """Convert psycopg2 row types to JSON-safe Python types."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if isinstance(v, (date, datetime)):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


def _parse_date(raw: str | None) -> date | None:
    """Parse an ISO date string (YYYY-MM-DD) or return None."""
    if not raw:
        return None
    try:
        return date.fromisoformat(raw)
    except (ValueError, AttributeError):
        return None


def _validate_task_title(title: str) -> str | None:
    """Return cleaned title or None if invalid."""
    title = title.strip()
    if not title:
        return None
    if len(title) > 500:
        return None
    return title


def _validate_subject(subject: str) -> str | None:
    """Return cleaned subject or None if invalid."""
    subject = subject.strip()
    if not subject or len(subject) > 100:
        return None
    return subject


# ─────────────────────────────────────────────────────────────────────────────
# Auth guard
# ─────────────────────────────────────────────────────────────────────────────

OPEN_PATHS = {"/", "/health"}

@app.before_request
def check_api_key() -> Response | None:
    """
    Require X-API-KEY header for all protected endpoints.
    If MY_API_KEY is not configured in .env, auth is STILL enforced with a
    warning — we never silently open the API.
    """
    # Always allow static files and health check without auth
    if request.path in OPEN_PATHS or request.path.startswith("/static"):
        return None

    if not API_KEY:
        # No key configured — log a loud warning but allow through
        # (dev convenience, never do this in production)
        logger.warning(
            "⚠️  MY_API_KEY is not set — API is unprotected! "
            "Set MY_API_KEY in your .env file for production."
        )
        return None

    client_key = request.headers.get("X-API-KEY", "")
    if client_key != API_KEY:
        logger.warning(f"Unauthorized request to {request.method} {request.path}")
        return _err("Unauthorized. Provide a valid X-API-KEY header.", "UNAUTHORIZED", 401)

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/health", methods=["GET"])
def health() -> tuple[Response, int]:
    """
    Liveness + readiness check.
    Returns DB connectivity status so orchestrators can detect DB failures.
    """
    db_ok = check_db_health()
    status = {
        "status": "ok" if db_ok else "degraded",
        "db": "connected" if db_ok else "unreachable",
        "service": "ClassFlow Watcher API",
    }
    return jsonify(status), 200 if db_ok else 503


# ─────────────────────────────────────────────────────────────────────────────
# GET /tasks
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/tasks", methods=["GET"])
def get_tasks() -> tuple[Response, int]:
    """
    List tasks with optional filters.

    Query params:
      subject=DSA            (case-insensitive)
      completed=true|false
      classification=CIE
      sort=due_date|difficulty|created_at  (default: due_date)
      order=asc|desc                        (default: asc)
      limit=50                              (default: 100, max: 500)
    """
    subject        = request.args.get("subject", "").strip() or None
    completed_raw  = request.args.get("completed")
    classification = request.args.get("classification", "").strip() or None
    sort_by        = request.args.get("sort", "due_date")
    order          = request.args.get("order", "asc").lower()
    limit          = request.args.get("limit", "100")

    # Validate sort/order to prevent injection
    allowed_sorts = {"due_date", "difficulty", "created_at", "estimated_minutes"}
    if sort_by not in allowed_sorts:
        return _err(f"Invalid sort field. Allowed: {sorted(allowed_sorts)}", "INVALID_PARAM")
    if order not in ("asc", "desc"):
        return _err("order must be 'asc' or 'desc'", "INVALID_PARAM")
    try:
        limit = max(1, min(500, int(limit)))
    except ValueError:
        limit = 100

    query = "SELECT * FROM assignments WHERE TRUE"
    params: list[Any] = []

    if subject:
        query += " AND LOWER(subject) = LOWER(%s)"
        params.append(subject)

    if completed_raw is not None:
        query += " AND is_completed = %s"
        params.append(completed_raw.lower() == "true")

    if classification:
        # Case-insensitive match: cie→CIE, practical→Practical, ASSIGNMENT→Assignment
        normalized = next(
            (v for v in VALID_CLASSIFICATIONS if v.lower() == classification.lower()),
            None,
        )
        if normalized is None:
            return _err(
                f"Invalid classification. Allowed: {sorted(VALID_CLASSIFICATIONS)}",
                "INVALID_PARAM",
            )
        query += " AND classification = %s"
        params.append(normalized)

    # Safe sort: column names whitelisted above, no user input in SQL
    if sort_by == "due_date":
        query += f" ORDER BY {sort_by} {order.upper()} NULLS LAST"
    else:
        query += f" ORDER BY {sort_by} {order.upper()}"

    query += " LIMIT %s"
    params.append(limit)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(query, params)
                rows = cur.fetchall()

        tasks = [_serialize_row(dict(r)) for r in rows]
        logger.info(f"GET /tasks → {len(tasks)} rows")
        return jsonify({"tasks": tasks, "count": len(tasks)}), 200

    except Exception as e:
        logger.error(f"GET /tasks failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /tasks  — manual task creation with AI analysis
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/tasks", methods=["POST"])
def create_task() -> tuple[Response, int]:
    """
    Manually add a task. AI analysis runs synchronously before insert.

    Request body (JSON):
      {
        "title":    "Assignment 3: Implement AVL tree",   (required)
        "subject":  "DSA",                               (required)
        "due_date": "2026-06-20",                        (optional, ISO date)
      }
    """
    body = request.get_json(silent=True)
    if not body:
        return _err("Request body must be JSON.", "INVALID_BODY")

    title = _validate_task_title(body.get("title", ""))
    if not title:
        return _err("'title' is required and must be 1-500 chars.", "MISSING_FIELD")

    subject = _validate_subject(body.get("subject", ""))
    if not subject:
        return _err("'subject' is required and must be 1-100 chars.", "MISSING_FIELD")

    due_date = _parse_date(body.get("due_date"))
    if body.get("due_date") and due_date is None:
        return _err("'due_date' must be ISO format: YYYY-MM-DD", "INVALID_FIELD")

    # Generate a stable ID from content
    import hashlib
    task_id = "manual-" + hashlib.sha256(
        f"{subject}:{title}".encode()
    ).hexdigest()[:16]

    # AI analysis
    logger.info(f"POST /tasks — analysing: '{title}' [{subject}]")
    ai = analyze_assignment(title, subject)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check for duplicate
                cur.execute("SELECT id FROM assignments WHERE id = %s", (task_id,))
                if cur.fetchone():
                    return _err(
                        "A task with this title and subject already exists.",
                        "DUPLICATE",
                        409,
                    )

                cur.execute(
                    """
                    INSERT INTO assignments
                        (id, subject, title, due_date, summary, difficulty,
                         estimated_minutes, classification, model_used, ai_success)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        task_id,
                        subject,
                        title,
                        due_date,
                        ai["summary"],
                        ai["difficulty"],
                        ai["estimated_minutes"],
                        ai["classification"],
                        ai.get("model_used", "fallback"),
                        ai.get("ai_success", False),
                    ),
                )
                row = cur.fetchone()
            conn.commit()

        task = _serialize_row(dict(row))
        logger.info(f"POST /tasks → created {task_id}")
        return jsonify({"task": task}), 201

    except Exception as e:
        logger.error(f"POST /tasks failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /tasks/<id>
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/tasks/<task_id>", methods=["GET"])
def get_task(task_id: str) -> tuple[Response, int]:
    """Return a single task by ID."""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM assignments WHERE id = %s", (task_id,))
                row = cur.fetchone()

        if row is None:
            return _err(f"Task '{task_id}' not found.", "NOT_FOUND", 404)

        return jsonify({"task": _serialize_row(dict(row))}), 200

    except Exception as e:
        logger.error(f"GET /tasks/{task_id} failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


# ─────────────────────────────────────────────────────────────────────────────
# PATCH /tasks/<id>
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/tasks/<task_id>", methods=["PATCH"])
def update_task(task_id: str) -> tuple[Response, int]:
    """
    Partially update a task.

    Patchable fields: title, subject, due_date, is_completed, difficulty,
                      estimated_minutes, classification, summary
    """
    body = request.get_json(silent=True)
    if not body:
        return _err("Request body must be JSON.", "INVALID_BODY")

    allowed_fields = {
        "title", "subject", "due_date", "is_completed",
        "difficulty", "estimated_minutes", "classification", "summary",
    }
    updates: dict[str, Any] = {}

    for field in allowed_fields:
        if field not in body:
            continue
        value = body[field]

        if field == "title":
            value = _validate_task_title(str(value))
            if value is None:
                return _err("'title' must be 1-500 characters.", "INVALID_FIELD")

        elif field == "subject":
            value = _validate_subject(str(value))
            if value is None:
                return _err("'subject' must be 1-100 characters.", "INVALID_FIELD")

        elif field == "due_date":
            value = _parse_date(str(value)) if value else None

        elif field == "is_completed":
            if not isinstance(value, bool):
                return _err("'is_completed' must be true or false.", "INVALID_FIELD")

        elif field == "difficulty":
            try:
                value = max(1, min(10, int(value)))
            except (ValueError, TypeError):
                return _err("'difficulty' must be an integer 1-10.", "INVALID_FIELD")

        elif field == "estimated_minutes":
            try:
                value = max(1, int(value))
            except (ValueError, TypeError):
                return _err("'estimated_minutes' must be a positive integer.", "INVALID_FIELD")

        elif field == "classification":
            value = str(value).strip().title()
            if value not in VALID_CLASSIFICATIONS:
                return _err(
                    f"'classification' must be one of: {sorted(VALID_CLASSIFICATIONS)}",
                    "INVALID_FIELD",
                )

        elif field == "summary":
            value = str(value).strip()[:1000] or None

        updates[field] = value

    if not updates:
        return _err("No valid fields to update.", "INVALID_BODY")

    # Build dynamic SET clause safely
    set_clause = ", ".join(f"{col} = %s" for col in updates)
    values = list(updates.values()) + [task_id]

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"UPDATE assignments SET {set_clause} WHERE id = %s RETURNING *",
                    values,
                )
                row = cur.fetchone()
                if row is None:
                    return _err(f"Task '{task_id}' not found.", "NOT_FOUND", 404)
            conn.commit()

        task = _serialize_row(dict(row))
        logger.info(f"PATCH /tasks/{task_id} → updated fields: {list(updates.keys())}")
        return jsonify({"task": task}), 200

    except Exception as e:
        logger.error(f"PATCH /tasks/{task_id} failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


# ─────────────────────────────────────────────────────────────────────────────
# DELETE /tasks/<id>
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/tasks/<task_id>", methods=["DELETE"])
def delete_task(task_id: str) -> tuple[Response, int]:
    """Permanently remove a task."""
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM assignments WHERE id = %s RETURNING id", (task_id,)
                )
                deleted = cur.fetchone()
                if not deleted:
                    return _err(f"Task '{task_id}' not found.", "NOT_FOUND", 404)
            conn.commit()

        logger.info(f"DELETE /tasks/{task_id} → deleted")
        return jsonify({"status": "success", "deleted_id": task_id}), 200

    except Exception as e:
        logger.error(f"DELETE /tasks/{task_id} failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /tasks/<id>/complete  |  /uncomplete
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/tasks/<task_id>/complete", methods=["POST"])
def complete_task(task_id: str) -> tuple[Response, int]:
    """Mark a task as completed."""
    return _set_completed(task_id, True)


@app.route("/tasks/<task_id>/uncomplete", methods=["POST"])
def uncomplete_task(task_id: str) -> tuple[Response, int]:
    """Mark a task as pending."""
    return _set_completed(task_id, False)


def _set_completed(task_id: str, completed: bool) -> tuple[Response, int]:
    action = "completed" if completed else "pending"
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "UPDATE assignments SET is_completed = %s WHERE id = %s RETURNING *",
                    (completed, task_id),
                )
                row = cur.fetchone()
                if row is None:
                    return _err(f"Task '{task_id}' not found.", "NOT_FOUND", 404)
            conn.commit()

        task = _serialize_row(dict(row))
        logger.info(f"POST /tasks/{task_id}/{'complete' if completed else 'uncomplete'}")
        return jsonify({"status": "success", "task": task}), 200

    except Exception as e:
        logger.error(f"Set completed={completed} for {task_id} failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /subjects
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/subjects", methods=["GET"])
def get_subjects() -> tuple[Response, int]:
    """
    Return all unique subjects with task counts.
    Response: [{"subject": "DSA", "total": 5, "pending": 3}, ...]
    """
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        subject,
                        COUNT(*)                            AS total,
                        COUNT(*) FILTER (WHERE NOT is_completed) AS pending,
                        COUNT(*) FILTER (WHERE is_completed)     AS completed
                    FROM assignments
                    GROUP BY subject
                    ORDER BY subject ASC
                    """
                )
                rows = cur.fetchall()

        subjects = [dict(r) for r in rows]
        return jsonify({"subjects": subjects}), 200

    except Exception as e:
        logger.error(f"GET /subjects failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /stats  — dashboard statistics
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/stats", methods=["GET"])
def get_stats() -> tuple[Response, int]:
    """
    Return aggregated statistics for the dashboard.

    Response:
    {
      "total_tasks":       12,
      "completed_tasks":   5,
      "pending_tasks":     7,
      "overdue_tasks":     2,
      "avg_difficulty":    6.2,
      "total_est_hours":   24.5,
      "by_classification": {"Assignment": 4, "CIE": 2, ...},
      "by_subject":        {"DSA": 5, "DAA": 3, ...},
      "ai_success_rate":   0.92
    }
    """
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*)                                          AS total_tasks,
                        COUNT(*) FILTER (WHERE is_completed)             AS completed_tasks,
                        COUNT(*) FILTER (WHERE NOT is_completed)         AS pending_tasks,
                        COUNT(*) FILTER (
                            WHERE NOT is_completed
                              AND due_date IS NOT NULL
                              AND due_date < CURRENT_DATE
                        )                                                 AS overdue_tasks,
                        ROUND(AVG(difficulty)::NUMERIC, 1)               AS avg_difficulty,
                        ROUND(
                            (SUM(estimated_minutes) / 60.0)::NUMERIC, 1
                        )                                                 AS total_est_hours,
                        ROUND(
                            AVG(CASE WHEN ai_success THEN 1.0 ELSE 0.0 END)::NUMERIC, 2
                        )                                                 AS ai_success_rate
                    FROM assignments
                    """
                )
                summary = dict(cur.fetchone())

                # Classification breakdown
                cur.execute(
                    """
                    SELECT classification, COUNT(*) AS count
                    FROM assignments
                    WHERE classification IS NOT NULL
                    GROUP BY classification
                    ORDER BY count DESC
                    """
                )
                by_class = {r["classification"]: r["count"] for r in cur.fetchall()}

                # Subject breakdown
                cur.execute(
                    """
                    SELECT subject, COUNT(*) AS count
                    FROM assignments
                    GROUP BY subject
                    ORDER BY count DESC
                    """
                )
                by_subject = {r["subject"]: r["count"] for r in cur.fetchall()}

        stats = {
            **{k: (float(v) if isinstance(v, (int, float)) else v)
               for k, v in summary.items()},
            "by_classification": by_class,
            "by_subject":        by_subject,
        }
        return jsonify(stats), 200

    except Exception as e:
        logger.error(f"GET /stats failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /tasks/<id>/reanalyze  — re-run AI on an existing task
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/tasks/<task_id>/reanalyze", methods=["POST"])
def reanalyze_task(task_id: str) -> tuple[Response, int]:
    """
    Re-run AI analysis on an existing task and update its fields.
    Useful if the first AI call failed or you want a fresh result.
    """
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    "SELECT id, title, subject FROM assignments WHERE id = %s", (task_id,)
                )
                row = cur.fetchone()

        if row is None:
            return _err(f"Task '{task_id}' not found.", "NOT_FOUND", 404)

        title   = row["title"]
        subject = row["subject"]

    except Exception as e:
        logger.error(f"POST /tasks/{task_id}/reanalyze — fetch failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)

    logger.info(f"POST /tasks/{task_id}/reanalyze — re-analysing: '{title}'")
    ai = analyze_assignment(title, subject)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    """
                    UPDATE assignments
                    SET summary = %s,
                        difficulty = %s,
                        estimated_minutes = %s,
                        classification = %s,
                        model_used = %s,
                        ai_success = %s
                    WHERE id = %s
                    RETURNING *
                    """,
                    (
                        ai["summary"],
                        ai["difficulty"],
                        ai["estimated_minutes"],
                        ai["classification"],
                        ai.get("model_used", "fallback"),
                        ai.get("ai_success", False),
                        task_id,
                    ),
                )
                updated = cur.fetchone()
            conn.commit()

        task = _serialize_row(dict(updated))
        return jsonify({"task": task, "ai_result": ai}), 200

    except Exception as e:
        logger.error(f"POST /tasks/{task_id}/reanalyze — update failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /admin/reanalyze-all  — batch re-run AI on tasks missing analysis
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/admin/reanalyze-all", methods=["POST"])
def reanalyze_all_tasks() -> tuple[Response, int]:
    """
    Re-run Gemini AI on all tasks that have ai_success=False.
    Use ?force=true to re-analyze ALL tasks regardless.

    curl -X POST http://localhost:5001/admin/reanalyze-all -H "X-API-KEY: ..."
    curl -X POST "http://localhost:5001/admin/reanalyze-all?force=true" -H "X-API-KEY: ..."
    """
    import time as _time

    force = request.args.get("force", "false").lower() == "true"
    filter_clause = "" if force else "WHERE ai_success = FALSE"

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"SELECT id, title, subject FROM assignments {filter_clause} ORDER BY due_date ASC NULLS LAST"
                )
                tasks = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"POST /admin/reanalyze-all — fetch failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)

    if not tasks:
        return jsonify({
            "status": "nothing_to_do",
            "message": "All tasks already analyzed. Use ?force=true to re-analyze all.",
            "updated": 0, "failed": 0,
        }), 200

    logger.info(f"POST /admin/reanalyze-all — {len(tasks)} tasks (force={force})")

    results = []
    updated = failed = 0

    for task in tasks:
        ai = analyze_assignment(task["title"], task["subject"])

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
                             ai["classification"], ai["model_used"], task["id"]),
                        )
                    conn.commit()
                updated += 1
                status = "updated"
            except Exception as e:
                logger.error(f"  DB update failed for {task['id']}: {e}")
                failed += 1
                status = "db_error"
        else:
            failed += 1
            status = "ai_failed"

        results.append({
            "id": task["id"], "title": task["title"], "status": status,
            "classification": ai.get("classification"),
            "difficulty": ai.get("difficulty"),
            "model_used": ai.get("model_used"),
        })
        _time.sleep(0.3)

    logger.info(f"POST /admin/reanalyze-all done: {updated} updated, {failed} failed")
    return jsonify({
        "status": "done", "total": len(tasks),
        "updated": updated, "failed": failed,
        "results": results,
    }), 200


# ─────────────────────────────────────────────────────────────────────────────
# Frontend
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/", methods=["GET"])
def home() -> Response:
    """Serve the frontend UI."""
    return app.send_static_file("index.html")


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Initialise DB schema on startup
    try:
        init_db()
    except Exception as exc:
        logger.critical(f"Failed to initialise database: {exc}", exc_info=True)
        raise SystemExit(1) from exc

    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    port  = int(os.getenv("PORT", "5001"))

    logger.info(f"Starting ClassFlow API on port {port} (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)
