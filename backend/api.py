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

import os, sys
# Allow importing sibling backend modules when run from project root
sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, jsonify, request, Response, send_from_directory, make_response, g, redirect
from flask_cors import CORS
from psycopg2.extras import RealDictCursor

from db import get_conn, init_db, check_db_health
from ai_helper import analyze_assignment
from auth import (
    verify_session_token,
    generate_session_token,
    get_auth_url,
    handle_oauth_callback,
    login_mock_user,
    is_google_configured
)



# App setup — static files served from ../frontend/
# ─────────────────────────────────────────────────────────────────────────────

FRONTEND_DIR = os.path.join(os.path.dirname(__file__), '..', 'frontend')

app = Flask(__name__, static_folder=FRONTEND_DIR, static_url_path='')
CORS(app, resources={r"/*": {"origins": "*"}})

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger("classflow.api")

# ─────────────────────────────────────────────────────────────────────────────
# VAPID setup for Web Push
# ─────────────────────────────────────────────────────────────────────────────
from py_vapid import Vapid
from cryptography.hazmat.primitives import serialization
import base64

VAPID_KEY_PATH = os.path.join(os.path.dirname(__file__), 'private_key.pem')
vapid_obj = Vapid()

if not os.path.exists(VAPID_KEY_PATH):
    logger.info("Generating a new VAPID private/public key pair...")
    vapid_obj.generate_keys()
    vapid_obj.save_key(VAPID_KEY_PATH)
else:
    logger.info(f"Loading existing VAPID keys from {VAPID_KEY_PATH}")
    vapid_obj = Vapid.from_file(VAPID_KEY_PATH)

# Derived base64url-encoded application server key (raw public key)
_pub_bytes = vapid_obj.public_key.public_bytes(
    encoding=serialization.Encoding.X962,
    format=serialization.PublicFormat.UncompressedPoint
)
VAPID_PUBLIC_KEY = base64.urlsafe_b64encode(_pub_bytes).decode().rstrip('=')

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
def check_authentication() -> Response | None:
    """
    Validate request authentication.
    Supports JWT Bearer token authentication for users,
    and fallback X-API-KEY header authentication for admin compatibility.
    """
    # Always allow preflight OPTIONS requests without auth
    if request.method == 'OPTIONS':
        return None

    # Always allow static files, health check, public auth endpoints (excluding /auth/me), and push public key
    if request.path in OPEN_PATHS or request.path.startswith('/static') \
            or request.path.startswith('/css') or request.path.startswith('/js') \
            or request.path.startswith('/fonts') or request.path.startswith('/icons') \
            or request.path in ('/favicon.ico', '/manifest.json', '/sw.js', '/notifications/vapid-key') \
            or (request.path.startswith('/auth/') and request.path != '/auth/me'):
        return None

    # 1. Bearer Token Session Validation
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
        user_id = verify_session_token(token)
        if user_id:
            g.user_id = user_id
            return None
        return _err("Session expired or invalid.", "UNAUTHORIZED", 401)

    # 2. Classic X-API-KEY Fallback (for CLI/scripts)
    client_key = request.headers.get("X-API-KEY", "")
    if API_KEY and client_key == API_KEY:
        g.user_id = "admin"  # Map admin key requests to fallback admin user scope
        return None

    return _err("Unauthorized. Provide a valid Authorization Bearer token.", "UNAUTHORIZED", 401)


# ─────────────────────────────────────────────────────────────────────────────
# Authentication Controllers (Google OAuth & Mock Sign-In)
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/auth/login", methods=["GET"])
def auth_login() -> Response:
    """Redirect user to either Google Consent Screen or Mock Login."""
    import urllib.parse
    
    # Capture requested redirect URL (essential for Capacitor mobile redirects)
    redirect_url = request.args.get("redirect_url", "") or request.referrer or request.root_url
    # Avoid infinite redirect loops if redirect_url is this exact endpoint
    if redirect_url.endswith("/auth/login") or redirect_url.endswith("/auth/login/"):
        redirect_url = request.root_url

    if not is_google_configured():
        base_url = request.root_url.rstrip('/')
        return redirect(f"{base_url}/auth/mock-select?redirect_url={urllib.parse.quote(redirect_url)}")
        
    return redirect(get_auth_url(state=redirect_url))


@app.route("/auth/callback", methods=["GET"])
def auth_callback() -> Response:
    """Handle Google OAuth redirect callback and exchange authorization code."""
    code = request.args.get("code")
    state = request.args.get("state", "")
    
    if not code:
        return _err("Authorization code missing.", "INVALID_CALLBACK", 400)

    profile = handle_oauth_callback(code)
    if not profile:
        return _err("Google authentication failed.", "AUTH_FAILED", 400)

    # Generate persistent session token
    token = generate_session_token(profile["id"])
    
    # Redirect back to original client page (PWA web or local Capacitor webview)
    redirect_target = state or request.root_url
    if "?" in redirect_target:
        return redirect(f"{redirect_target.rstrip('/')}&token={token}")
    else:
        sep = "" if redirect_target.endswith("/") else "/"
        return redirect(f"{redirect_target}{sep}?token={token}")


@app.route("/auth/mock-select", methods=["GET"])
def auth_mock_select() -> str:
    """Serve visual mock account login selector page for development."""
    import urllib.parse
    redirect_url = request.args.get("redirect_url", "") or request.root_url
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>ClassFlow Mock Login</title>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;600&display=swap" rel="stylesheet">
        <style>
            body {{
                margin: 0;
                padding: 0;
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
                background: #0f172a;
                color: #e2e8f0;
                font-family: 'Outfit', sans-serif;
            }}
            .card {{
                background: rgba(30, 41, 59, 0.7);
                border: 1px solid rgba(255, 255, 255, 0.1);
                backdrop-filter: blur(16px);
                border-radius: 24px;
                padding: 40px;
                max-width: 400px;
                width: 90%;
                box-shadow: 0 20px 40px rgba(0,0,0,0.3);
                text-align: center;
            }}
            h2 {{
                font-size: 28px;
                margin-bottom: 8px;
                background: linear-gradient(135deg, #38bdf8, #818cf8);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
            }}
            p {{
                color: #94a3b8;
                font-size: 14px;
                margin-bottom: 30px;
                line-height: 1.5;
            }}
            .btn {{
                display: block;
                width: 100%;
                padding: 14px;
                margin-bottom: 16px;
                background: rgba(255, 255, 255, 0.05);
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 12px;
                color: #f1f5f9;
                font-size: 16px;
                font-weight: 600;
                cursor: pointer;
                transition: all 0.2s;
            }}
            .btn:hover {{
                background: linear-gradient(135deg, #38bdf8, #818cf8);
                border-color: transparent;
                transform: translateY(-2px);
                box-shadow: 0 4px 12px rgba(56, 189, 248, 0.3);
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <h2>Select Mock Account</h2>
            <p>Google Client credentials are not configured.<br>Select a mock account to test multi-user dashboard segregation.</p>
            
            <button class="btn" onclick="login('mock-mantra', 'mantra@charusat.edu.in', 'Mantra Patel')">
                Sign in as Mantra Patel (Student A)
            </button>
            
            <button class="btn" onclick="login('mock-friend', 'friend@charusat.edu.in', 'Siddharth Shah')">
                Sign in as Siddharth Shah (Student B)
            </button>
        </div>
        <script>
            function login(id, email, name) {{
                const form = document.createElement('form');
                form.method = 'POST';
                form.action = '/auth/mock-login';
                
                const inputs = {{ id, email, name, redirect_url: {repr(redirect_url)} }};
                for (const key in inputs) {{
                    const input = document.createElement('input');
                    input.type = 'hidden';
                    input.name = key;
                    input.value = inputs[key];
                    form.appendChild(input);
                }}
                
                document.body.appendChild(form);
                form.submit();
            }}
        </script>
    </body>
    </html>
    """
    return html


@app.route("/auth/mock-login", methods=["POST"])
def auth_mock_login() -> Response:
    """Authenticate a mock developer account and redirect back to PWA."""
    user_id = request.form.get("id")
    email = request.form.get("email")
    name = request.form.get("name")
    redirect_url = request.form.get("redirect_url") or request.root_url
    
    if not user_id or not email or not name:
        return _err("Missing required mock credentials.", "INVALID_INPUT", 400)
        
    token = login_mock_user(user_id, email, name)
    
    if "?" in redirect_url:
        return redirect(f"{redirect_url.rstrip('/')}&token={token}")
    else:
        sep = "" if redirect_url.endswith("/") else "/"
        return redirect(f"{redirect_url}{sep}?token={token}")


@app.route("/auth/me", methods=["GET"])
def auth_me() -> tuple[Response, int]:
    """Retrieve logged-in user profile details."""
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT id, email, name, picture FROM users WHERE id = %s", (g.user_id,))
                row = cur.fetchone()
        if not row:
            return _err("User profile not found.", "NOT_FOUND", 404)
        return jsonify({"user": dict(row)}), 200
    except Exception as e:
        logger.error(f"GET /auth/me failed: {e}", exc_info=True)
        return _err("Database query failed.", "DB_ERROR", 500)



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

    query = "SELECT * FROM assignments WHERE user_id = %s"
    params: list[Any] = [g.user_id]

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
        f"{g.user_id}:{subject}:{title}".encode()
    ).hexdigest()[:16]

    # AI analysis
    logger.info(f"POST /tasks — analysing: '{title}' [{subject}] for user {g.user_id}")
    ai = analyze_assignment(title, subject)

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Check for duplicate
                cur.execute("SELECT id FROM assignments WHERE id = %s AND user_id = %s", (task_id, g.user_id))
                if cur.fetchone():
                    return _err(
                        "A task with this title and subject already exists.",
                        "DUPLICATE",
                        409,
                    )

                cur.execute(
                    """
                    INSERT INTO assignments
                        (id, user_id, subject, title, due_date, summary, difficulty,
                         estimated_minutes, classification, model_used, ai_success)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (
                        task_id,
                        g.user_id,
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
                cur.execute("SELECT * FROM assignments WHERE id = %s AND user_id = %s", (task_id, g.user_id))
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
    values = list(updates.values()) + [task_id, g.user_id]

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"UPDATE assignments SET {set_clause} WHERE id = %s AND user_id = %s RETURNING *",
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
                    "DELETE FROM assignments WHERE id = %s AND user_id = %s RETURNING id", (task_id, g.user_id)
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
                    "UPDATE assignments SET is_completed = %s WHERE id = %s AND user_id = %s RETURNING *",
                    (completed, task_id, g.user_id),
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
                    WHERE user_id = %s
                    GROUP BY subject
                    ORDER BY subject ASC
                    """,
                    (g.user_id,)
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
                    WHERE user_id = %s
                    """,
                    (g.user_id,)
                )
                summary = dict(cur.fetchone())

                # Classification breakdown
                cur.execute(
                    """
                    SELECT classification, COUNT(*) AS count
                    FROM assignments
                    WHERE classification IS NOT NULL AND user_id = %s
                    GROUP BY classification
                    ORDER BY count DESC
                    """,
                    (g.user_id,)
                )
                by_class = {r["classification"]: r["count"] for r in cur.fetchall()}

                # Subject breakdown
                cur.execute(
                    """
                    SELECT subject, COUNT(*) AS count
                    FROM assignments
                    WHERE user_id = %s
                    GROUP BY subject
                    ORDER BY count DESC
                    """,
                    (g.user_id,)
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
                    "SELECT id, title, subject FROM assignments WHERE id = %s AND user_id = %s", (task_id, g.user_id)
                )
                row = cur.fetchone()

        if row is None:
            return _err(f"Task '{task_id}' not found.", "NOT_FOUND", 404)

        title   = row["title"]
        subject = row["subject"]

    except Exception as e:
        logger.error(f"POST /tasks/{task_id}/reanalyze — fetch failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)

    logger.info(f"POST /tasks/{task_id}/reanalyze — re-analysing: '{title}' for user {g.user_id}")
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
                    WHERE id = %s AND user_id = %s
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
                        g.user_id,
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
    filter_clause = "WHERE user_id = %s" if force else "WHERE user_id = %s AND ai_success = FALSE"

    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(
                    f"SELECT id, title, subject FROM assignments {filter_clause} ORDER BY due_date ASC NULLS LAST",
                    (g.user_id,)
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

    logger.info(f"POST /admin/reanalyze-all — {len(tasks)} tasks (force={force}) for user {g.user_id}")

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
                            WHERE id=%s AND user_id=%s
                            """,
                            (ai["summary"], ai["difficulty"], ai["estimated_minutes"],
                             ai["classification"], ai["model_used"], task["id"], g.user_id),
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
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route("/manifest.json", methods=["GET"])
def manifest() -> Response:
    """Serve the web app manifest."""
    return send_from_directory(FRONTEND_DIR, 'manifest.json')


@app.route("/sw.js", methods=["GET"])
def service_worker() -> Response:
    """Serve the Service Worker with appropriate headers."""
    response = make_response(send_from_directory(FRONTEND_DIR, 'sw.js'))
    response.headers['Content-Type'] = 'application/javascript'
    response.headers['Service-Worker-Allowed'] = '/'
    # Ensure service worker is not cached aggressively
    response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Push Notifications Endpoints & Scheduler
# ─────────────────────────────────────────────────────────────────────────────

@app.route("/notifications/vapid-key", methods=["GET"])
def get_vapid_key() -> tuple[Response, int]:
    """Return the VAPID public key to client."""
    return jsonify({"public_key": VAPID_PUBLIC_KEY}), 200


@app.route("/notifications/subscribe", methods=["POST"])
def subscribe() -> tuple[Response, int]:
    """Store client push subscription."""
    body = request.get_json(silent=True)
    if not body or "subscription" not in body:
        return _err("Request body must contain 'subscription' object.", "INVALID_BODY")
    
    sub = body["subscription"]
    endpoint = sub.get("endpoint")
    keys = sub.get("keys", {})
    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    
    if not endpoint or not p256dh or not auth:
        return _err("Subscription endpoint, p256dh, and auth keys are required.", "MISSING_FIELD")
        
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO push_subscriptions (user_id, endpoint, p256dh, auth)
                    VALUES (%s, %s, %s, %s)
                    ON CONFLICT (endpoint) DO UPDATE
                    SET user_id = EXCLUDED.user_id, p256dh = EXCLUDED.p256dh, auth = EXCLUDED.auth
                    RETURNING id
                    """,
                    (g.user_id, endpoint, p256dh, auth)
                )
                sub_id = cur.fetchone()[0]
            conn.commit()
            
        logger.info(f"Registered push subscription for user {g.user_id}: id={sub_id}, endpoint={endpoint[:50]}...")
        return jsonify({"status": "success", "id": sub_id}), 200
        
    except Exception as e:
        logger.error(f"POST /notifications/subscribe failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


@app.route("/notifications/unsubscribe", methods=["POST"])
def unsubscribe() -> tuple[Response, int]:
    """Remove client push subscription."""
    body = request.get_json(silent=True)
    if not body or "endpoint" not in body:
        return _err("Request body must contain 'endpoint'.", "INVALID_BODY")
    
    endpoint = body["endpoint"]
    
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM push_subscriptions WHERE endpoint = %s AND user_id = %s RETURNING id",
                    (endpoint, g.user_id)
                )
                deleted = cur.fetchone()
            conn.commit()
            
        if not deleted:
            return jsonify({"status": "not_found", "message": "Subscription not found."}), 200
            
        logger.info(f"Unregistered push subscription for user {g.user_id}: endpoint={endpoint[:50]}...")
        return jsonify({"status": "success"}), 200
        
    except Exception as e:
        logger.error(f"POST /notifications/unsubscribe failed: {e}", exc_info=True)
        return _err("Internal server error.", "DB_ERROR", 500)


@app.route("/admin/test-push", methods=["POST"])
def test_push() -> tuple[Response, int]:
    """Trigger a test push notification to all subscribed clients."""
    from pywebpush import webpush, WebPushException
    import json
    
    body = request.get_json(silent=True) or {}
    title = body.get("title", "ClassFlow Alert 🚀")
    message = body.get("message", "Test push notification successfully received!")
    url = body.get("url", "/")
    
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute("SELECT * FROM push_subscriptions WHERE user_id = %s", (g.user_id,))
                subs = [dict(r) for r in cur.fetchall()]
    except Exception as e:
        logger.error(f"Failed to fetch subscriptions: {e}")
        return _err("Database query failed.", "DB_ERROR", 500)
        
    if not subs:
        return jsonify({"status": "no_subscriptions", "message": "No active subscriptions found in database."}), 200
        
    sent_count = 0
    fail_count = 0
    cleaned_count = 0
    
    payload = json.dumps({
        "title": title,
        "body": message,
        "url": url,
        "tag": "classflow-test"
    })
    
    for sub in subs:
        try:
            webpush(
                subscription_info={
                    "endpoint": sub["endpoint"],
                    "keys": {
                        "p256dh": sub["p256dh"],
                        "auth": sub["auth"]
                    }
                },
                data=payload,
                vapid_private_key=VAPID_KEY_PATH,
                vapid_claims={"sub": "mailto:classflow-admin@example.com"}
            )
            sent_count += 1
        except WebPushException as ex:
            logger.warning(f"Push notification failed for {sub['endpoint'][:40]}: {ex}")
            fail_count += 1
            # Clean up dead subscriptions (404 Not Found or 410 Gone)
            if ex.response is not None and ex.response.status_code in (404, 410):
                try:
                    with get_conn() as conn:
                        with conn.cursor() as cur:
                            cur.execute("DELETE FROM push_subscriptions WHERE id = %s", (sub["id"],))
                        conn.commit()
                    cleaned_count += 1
                except Exception as db_ex:
                    logger.error(f"Failed to delete dead subscription {sub['id']}: {db_ex}")
                    
    logger.info(f"Test push finished: {sent_count} sent, {fail_count} failed, {cleaned_count} cleaned from DB")
    return jsonify({
        "status": "done",
        "total": len(subs),
        "sent": sent_count,
        "failed": fail_count,
        "cleaned": cleaned_count
    }), 200


def send_upcoming_deadline_notifications():
    """Query assignments due in the next 24 hours and notify active subscribers."""
    from pywebpush import webpush, WebPushException
    import json
    
    logger.info("Checking for upcoming deadlines due in next 24 hours...")
    try:
        with get_conn() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                # Query assignments due in the next 24 hours that are pending and not yet notified
                cur.execute(
                    """
                    SELECT id, user_id, title, subject, due_date
                    FROM assignments
                    WHERE NOT is_completed
                      AND NOT deadline_notified
                      AND due_date IS NOT NULL
                      AND due_date >= CURRENT_DATE
                      AND due_date <= CURRENT_DATE + INTERVAL '1 day'
                    """
                )
                tasks = [dict(r) for r in cur.fetchall()]
                
        if not tasks:
            return
            
        logger.info(f"Found {len(tasks)} tasks due soon for user notification checks...")
        
        for task in tasks:
            # Skip if user_id is null
            if not task.get("user_id"):
                continue
                
            # Fetch subscriptions specifically for this user
            with get_conn() as conn:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute("SELECT * FROM push_subscriptions WHERE user_id = %s", (task["user_id"],))
                    subs = [dict(r) for r in cur.fetchall()]
                    
            if not subs:
                continue
                
            payload = json.dumps({
                "title": f"Deadline Approaching: {task['subject']} ⏳",
                "body": f"'{task['title']}' is due on {task['due_date']}!",
                "url": f"/#card-{task['id']}",
                "tag": f"cf-deadline-{task['id']}"
            })
            
            for sub in subs:
                try:
                    webpush(
                        subscription_info={
                            "endpoint": sub["endpoint"],
                            "keys": {
                                "p256dh": sub["p256dh"],
                                "auth": sub["auth"]
                            }
                        },
                        data=payload,
                        vapid_private_key=VAPID_KEY_PATH,
                        vapid_claims={"sub": "mailto:classflow-admin@example.com"}
                    )
                except WebPushException as ex:
                    logger.warning(f"Push notification failed for {sub['endpoint'][:40]}: {ex}")
                    if ex.response is not None and ex.response.status_code in (404, 410):
                        try:
                            with get_conn() as conn:
                                with conn.cursor() as cur:
                                    cur.execute("DELETE FROM push_subscriptions WHERE id = %s", (sub["id"],))
                                conn.commit()
                        except Exception as db_ex:
                            logger.error(f"Failed to delete dead subscription {sub['id']}: {db_ex}")
                            
            # Mark task as notified
            try:
                with get_conn() as conn:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE assignments SET deadline_notified = TRUE WHERE id = %s",
                            (task["id"],)
                        )
                    conn.commit()
            except Exception as db_ex:
                logger.error(f"Failed to mark task {task['id']} as notified: {db_ex}")
                
    except Exception as e:
        logger.error(f"Deadline notification sender error: {e}", exc_info=True)


def start_deadline_scheduler():
    """Launch background thread to periodically check for upcoming deadlines."""
    import threading
    import time
    
    def run_scheduler():
        time.sleep(10)
        logger.info("Starting upcoming deadlines background scheduler thread...")
        while True:
            try:
                send_upcoming_deadline_notifications()
            except Exception as e:
                logger.error(f"Scheduler exception: {e}")
            time.sleep(3600)
            
    thread = threading.Thread(target=run_scheduler, daemon=True)
    thread.start()


def start_sync_watcher():
    """Launch background thread to periodically run Classroom Watcher sync loop."""
    import threading
    import time
    
    poll_interval = int(os.getenv("POLL_INTERVAL_SECONDS", "3600"))
    
    def run_sync_loop():
        # Wait 15 seconds to ensure database connectivity and Flask server are ready
        time.sleep(15)
        logger.info("Starting Classroom Watcher sync loop background thread...")
        
        # Import inside the thread to avoid circular imports during module load
        try:
            from main import get_assignments
        except Exception as e:
            logger.critical(f"Failed to import get_assignments from main: {e}", exc_info=True)
            return
            
        while True:
            try:
                logger.info("⏳ Starting background sync cycle ...")
                get_assignments()
                logger.info(f"✅ Background sync complete. Next run in {poll_interval // 60} min...")
            except Exception as e:
                logger.error(f"Background sync cycle crashed: {e}", exc_info=True)
            time.sleep(poll_interval)
            
    thread = threading.Thread(target=run_sync_loop, daemon=True)
    thread.start()


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

    # Start deadline notification background scheduler
    start_deadline_scheduler()

    # Start classroom sync background thread
    start_sync_watcher()

    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    port  = int(os.getenv("PORT", "5001"))

    logger.info(f"Starting ClassFlow API on port {port} (debug={debug})")
    app.run(host="0.0.0.0", port=port, debug=debug)

