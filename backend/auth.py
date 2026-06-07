"""
auth.py — ClassFlow Watcher Authentication & Sessions
─────────────────────────────────────────────────────────────────────────────
Handles user authentication via Google OAuth 2.0 (with fallback to Mock Login)
and provides session signing/validation using JWT-like secure signed tokens.
"""

from __future__ import annotations

import datetime
import logging
import os
import urllib.parse
from flask import request, g, jsonify
import requests
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from db import get_conn

logger = logging.getLogger("classflow.auth")

# Use MY_API_KEY (or fallback) as session signing secret
SECRET_KEY = os.getenv("MY_API_KEY", "ClassflowSecureSecretKey123").strip()
serializer = URLSafeTimedSerializer(SECRET_KEY)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_REDIRECT_URI = os.getenv("GOOGLE_REDIRECT_URI", "").strip()

# Fallback to credentials.json if environment variables are not set (for local dev)
if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
    import json
    for path in ["credentials.json", "../credentials.json", "backend/credentials.json"]:
        abs_path = os.path.normpath(os.path.join(os.path.dirname(__file__), path))
        if os.path.exists(abs_path):
            try:
                with open(abs_path, "r") as f:
                    data = json.load(f)
                    config = data.get("installed") or data.get("web")
                    if config:
                        if not GOOGLE_CLIENT_ID:
                            GOOGLE_CLIENT_ID = config.get("client_id", "").strip()
                        if not GOOGLE_CLIENT_SECRET:
                            GOOGLE_CLIENT_SECRET = config.get("client_secret", "").strip()
                        break
            except Exception as e:
                logger.warning(f"Could not parse fallback credentials file {abs_path}: {e}")

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.profile",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.student-submissions.me.readonly"
]

def generate_session_token(user_id: str) -> str:
    """Generate a signed session token valid for 30 days."""
    return serializer.dumps({"user_id": user_id})

def verify_session_token(token: str) -> str | None:
    """Verify signed session token. Returns user_id if valid, otherwise None."""
    try:
        # Token valid for 30 days (2592000 seconds)
        data = serializer.loads(token, max_age=2592000)
        return data.get("user_id")
    except (SignatureExpired, BadSignature):
        return None

def is_google_configured() -> bool:
    """Return True if Google OAuth credentials are set in environment."""
    return bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET)

def get_auth_url(state: str = None) -> str:
    """Get the URL to redirect to for Google OAuth or Mock Auth."""
    if not is_google_configured():
        # Redirect to a local mock login selector page
        base_url = request.root_url.rstrip('/')
        return f"{base_url}/auth/mock-select"
        
    redirect_uri = GOOGLE_REDIRECT_URI or f"{request.root_url.rstrip('/')}/auth/callback"
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true"
    }
    if state:
        params["state"] = state
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode(params)

def handle_oauth_callback(code: str) -> dict | None:
    """Exchange code for tokens and upsert user. Returns user profile dict or None."""
    redirect_uri = GOOGLE_REDIRECT_URI or f"{request.root_url.rstrip('/')}/auth/callback"
    token_url = "https://oauth2.googleapis.com/token"
    
    payload = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": redirect_uri,
        "grant_type": "authorization_code"
    }
    
    try:
        res = requests.post(token_url, data=payload, timeout=10)
        if res.status_code != 200:
            logger.error(f"Google token exchange failed: {res.status_code} - {res.text}")
            return None
            
        token_data = res.json()
        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")
        expires_in = token_data.get("expires_in", 3600)
        
        # Get user info using access token
        userinfo_url = "https://www.googleapis.com/oauth2/v3/userinfo"
        info_res = requests.get(userinfo_url, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        if info_res.status_code != 200:
            logger.error(f"Google userinfo fetch failed: {info_res.status_code}")
            return None
            
        user_info = info_res.json()
        google_id = user_info.get("sub")
        email = user_info.get("email")
        name = user_info.get("name")
        picture = user_info.get("picture")
        
        if not google_id or not email:
            logger.error("sub or email missing from Google userinfo response.")
            return None
            
        expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)
        
        with get_conn() as conn:
            with conn.cursor() as cur:
                if refresh_token:
                    cur.execute(
                        """
                        INSERT INTO users (id, email, name, picture, google_access_token, google_refresh_token, google_token_expiry)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE
                        SET email = EXCLUDED.email, name = EXCLUDED.name, picture = EXCLUDED.picture,
                            google_access_token = EXCLUDED.google_access_token,
                            google_refresh_token = EXCLUDED.google_refresh_token,
                            google_token_expiry = EXCLUDED.google_token_expiry,
                            updated_at = NOW()
                        """,
                        (google_id, email, name, picture, access_token, refresh_token, expiry)
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO users (id, email, name, picture, google_access_token, google_token_expiry)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id) DO UPDATE
                        SET email = EXCLUDED.email, name = EXCLUDED.name, picture = EXCLUDED.picture,
                            google_access_token = EXCLUDED.google_access_token,
                            google_token_expiry = EXCLUDED.google_token_expiry,
                            updated_at = NOW()
                        """,
                        (google_id, email, name, picture, access_token, expiry)
                    )
            conn.commit()
            
        return {"id": google_id, "email": email, "name": name, "picture": picture}
        
    except Exception as e:
        logger.error(f"OAuth callback handling crashed: {e}", exc_info=True)
        return None

def refresh_user_token(user_id: str) -> str | None:
    """Refresh user access token if expired. Returns active access token."""
    # If Google is not configured or user is a mock user, return mock token
    if not is_google_configured() or (user_id and user_id.startswith("mock-")):
        return "mock_access_token"

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT google_access_token, google_refresh_token, google_token_expiry FROM users WHERE id = %s",
                (user_id,)
            )
            row = cur.fetchone()
            
    if not row:
        return None
        
    access_token, refresh_token, expiry = row
    
    now = datetime.datetime.now(datetime.timezone.utc)
    # If not expired (with 5-minute buffer), return current access token
    if expiry > now + datetime.timedelta(minutes=5):
        return access_token
        
    if not refresh_token:
        logger.warning(f"No refresh token stored for user {user_id}; cannot refresh.")
        return None
        
    token_url = "https://oauth2.googleapis.com/token"
    payload = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token"
    }
    
    try:
        res = requests.post(token_url, data=payload, timeout=10)
        if res.status_code != 200:
            logger.error(f"Token refresh failed for {user_id}: {res.status_code} - {res.text}")
            return None
            
        data = res.json()
        new_access_token = data.get("access_token")
        expires_in = data.get("expires_in", 3600)
        new_expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=expires_in)
        
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE users SET google_access_token = %s, google_token_expiry = %s, updated_at = NOW() WHERE id = %s",
                    (new_access_token, new_expiry, user_id)
                )
            conn.commit()
            
        return new_access_token
    except Exception as e:
        logger.error(f"Token refresh request failed for {user_id}: {e}", exc_info=True)
        return None

def login_mock_user(mock_id: str, email: str, name: str, picture: str = None) -> str:
    """Register/login a mock user and return a session token."""
    expiry = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
    
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO users (id, email, name, picture, google_access_token, google_refresh_token, google_token_expiry)
                VALUES (%s, %s, %s, %s, 'mock_access_token', 'mock_refresh_token', %s)
                ON CONFLICT (id) DO UPDATE
                SET name = EXCLUDED.name, email = EXCLUDED.email, picture = EXCLUDED.picture, updated_at = NOW()
                """,
                (mock_id, email, name, picture, expiry)
            )
        conn.commit()
        
    return generate_session_token(mock_id)
