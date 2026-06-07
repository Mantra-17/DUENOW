"""
ai_helper.py — ClassFlow Watcher
─────────────────────────────────────────────────────────────────────────────
Industry-grade AI analysis module for classifying and analysing college tasks.

Features
--------
- Correct, up-to-date Gemini model IDs with ordered preference.
- Exponential backoff with jitter on rate-limit (HTTP 429).
- Full response validation and type coercion.
- Structured logging at every decision point.
- Never crashes the caller — always returns a valid dict.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from typing import TypedDict

import requests

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Types
# ─────────────────────────────────────────────────────────────────────────────

class AIResult(TypedDict):
    classification: str
    summary: str
    difficulty: int
    estimated_minutes: int
    model_used: str
    ai_success: bool


FALLBACK_RESULT: AIResult = {
    "classification": "Other",
    "summary": "AI categorization pending — could not reach Gemini API.",
    "difficulty": 1,
    "estimated_minutes": 30,
    "model_used": "fallback",
    "ai_success": False,
}

# ─────────────────────────────────────────────────────────────────────────────
# Gemini config
# ─────────────────────────────────────────────────────────────────────────────

# Verified working model IDs on the Gemini REST API (v1beta).
# Ordered by daily-quota availability — lite models have separate quotas.
# NOTE: gemini-2.5-flash-preview-* and gemini-1.5-flash are 404 on REST API.
MODEL_IDS: list[str] = [
    "gemini-2.5-flash-lite",    # ✅ Separate daily quota — try first
    "gemini-flash-lite-latest", # ✅ Alias for flash-lite, separate quota
    "gemma-4-26b-a4b-it",       # ✅ Gemma 4 (fresh quota pool)
    "gemma-4-31b-it",           # ✅ Gemma 4 31B (fresh quota pool)
    "gemini-3.5-flash",         # High quality, 20/day free tier
    "gemini-flash-latest",      # Stable alias for latest Flash
    "gemini-2.5-flash",         # Standard Gemini 2.5 Flash
    "gemini-2.0-flash",         # Standard Gemini 2.0 Flash
    "gemini-2.0-flash-lite",    # Lightweight Gemini 2.0
]

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"

# Backoff constants
MAX_RETRIES_PER_MODEL = 3     # Max attempts per model on transient errors
BASE_BACKOFF_SECONDS  = 2.0   # Exponential backoff starting point
MAX_BACKOFF_SECONDS   = 60.0  # Cap backoff so we don't wait forever
REQUEST_TIMEOUT       = 30    # HTTP timeout per call

# Valid classifications — enforced at validation step
VALID_CLASSIFICATIONS = frozenset({"Assignment", "CIE", "Practical", "Project", "Other"})

# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

PROMPT_TEMPLATE = """\
You are an intelligent academic assistant helping Indian engineering students manage their college workload.

Analyze the following college task and return ONLY a raw JSON object — no markdown fences, no explanation, no extra text.

Subject: {subject}
Task title: {title}

Rules:
1. "classification" MUST be exactly one of: Assignment, CIE, Practical, Project, Other
   - Assignment = homework or written task
   - CIE = Continuous Internal Evaluation / internal exam / quiz
   - Practical = lab experiment or practical file
   - Project = multi-week project or mini-project
   - Other = anything else (seminar, viva, etc.)
2. "summary" = 1 clear, actionable sentence (max 25 words) telling the student what to do
3. "difficulty" = integer 1-10 (1=trivial, 10=extremely hard)
4. "estimated_minutes" = realistic total study+work time in minutes (integer, minimum 15)

Return ONLY this JSON structure (numeric fields FIRST so they are never cut off):
{{"classification": "...", "difficulty": 5, "estimated_minutes": 60, "summary": "..."}}\
"""


# ─────────────────────────────────────────────────────────────────────────────
# Main function
# ─────────────────────────────────────────────────────────────────────────────

def analyze_assignment(title: str, subject: str) -> AIResult:
    """
    Classify and summarise a single assignment using Gemini.

    Returns a fully-validated AIResult dict.
    Always returns a valid dict — never raises.
    """
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        logger.error("GEMINI_API_KEY is not set — AI analysis skipped.")
        return FALLBACK_RESULT

    prompt = PROMPT_TEMPLATE.format(
        subject=subject.strip(),
        title=title.strip(),
    )

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,        # Very low — deterministic JSON output
            "maxOutputTokens": 1024,   # Enough for our JSON + thinking tokens
            "topP": 0.8,
            "topK": 10,
        },
    }

    for model_id in MODEL_IDS:
        result = _try_model(model_id, api_key, payload)
        if result is not None:
            result["model_used"] = model_id
            result["ai_success"] = True
            logger.info(
                f"AI analysis OK via {model_id}: "
                f"type={result['classification']} "
                f"diff={result['difficulty']}/10 "
                f"est={result['estimated_minutes']}min"
            )
            return result

    logger.error("All Gemini models exhausted — using fallback result.")
    return FALLBACK_RESULT


# ─────────────────────────────────────────────────────────────────────────────
# Private helpers
# ─────────────────────────────────────────────────────────────────────────────

def _try_model(model_id: str, api_key: str, payload: dict) -> AIResult | None:
    """
    Attempt to get a valid response from one Gemini model.
    Returns parsed AIResult on success, None if this model should be skipped.
    Uses exponential backoff with jitter on transient errors.
    """
    url = f"{GEMINI_BASE}/{model_id}:generateContent?key={api_key}"

    for attempt in range(1, MAX_RETRIES_PER_MODEL + 1):
        try:
            logger.debug(f"  → {model_id} attempt {attempt}/{MAX_RETRIES_PER_MODEL}")
            response = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)

        except requests.Timeout:
            logger.warning(f"  Timeout on {model_id} attempt {attempt}")
            _backoff(attempt)
            continue

        except requests.ConnectionError as e:
            logger.warning(f"  Connection error on {model_id}: {e}")
            _backoff(attempt)
            continue

        except requests.RequestException as e:
            logger.warning(f"  Request error on {model_id}: {e}")
            return None  # Non-retryable network issue

        # ── Handle HTTP status codes ──────────────────────────────────────────
        if response.status_code == 404:
            logger.debug(f"  {model_id}: HTTP 404 — model does not exist, skipping.")
            return None  # Model not available, try next

        if response.status_code == 400:
            logger.warning(f"  {model_id}: HTTP 400 — bad request: {response.text[:300]}")
            return None  # Bad prompt/config, no point retrying

        if response.status_code == 403:
            logger.error(f"  {model_id}: HTTP 403 — API key invalid or quota exceeded.")
            return None

        if response.status_code == 429:
            # Distinguish between per-minute rate limit (retry) vs daily quota (skip model)
            try:
                err_body = response.json()
                err_msg = err_body.get("error", {}).get("message", "")
            except Exception:
                err_msg = response.text

            is_daily_quota = (
                "GenerateRequestsPerDayPerProjectPerModel" in err_msg
                or ("limit: 0" in err_msg and "quota" in err_msg.lower())
            )

            if is_daily_quota:
                logger.warning(
                    f"  {model_id}: Daily quota exhausted — skipping model."
                )
                return None  # Skip immediately, don't waste retries

            # Per-minute rate limit — use exponential backoff and retry
            retry_after = _parse_retry_after(response)
            wait = retry_after or _backoff_seconds(attempt)
            logger.warning(f"  {model_id}: Rate limited (429). Waiting {wait:.1f}s …")
            time.sleep(wait)
            continue

        if response.status_code == 503 or response.status_code == 500:
            logger.warning(f"  {model_id}: HTTP {response.status_code} — server error, retrying …")
            _backoff(attempt)
            continue

        if response.status_code != 200:
            logger.warning(
                f"  {model_id}: Unexpected HTTP {response.status_code}: "
                f"{response.text[:200]}"
            )
            continue

        # ── Parse response ─────────────────────────────────────────────────────
        try:
            data = response.json()
        except json.JSONDecodeError:
            logger.warning(f"  {model_id}: Could not parse response as JSON")
            continue

        raw_text = _extract_text(data, model_id)
        if raw_text is None:
            continue

        parsed = _parse_and_validate(raw_text, model_id)
        if parsed is not None:
            return parsed

        # Malformed JSON in response — retry won't help, skip model
        return None

    logger.warning(f"  {model_id}: All {MAX_RETRIES_PER_MODEL} attempts failed.")
    return None


def _extract_text(data: dict, model_id: str) -> str | None:
    """Safely navigate Gemini's nested response structure."""
    try:
        # Check for content filter / safety block
        candidate = data["candidates"][0]
        finish_reason = candidate.get("finishReason", "")
        if finish_reason in ("SAFETY", "RECITATION", "OTHER"):
            logger.warning(f"  {model_id}: Content blocked — finishReason={finish_reason}")
            return None

        return candidate["content"]["parts"][0]["text"]

    except (KeyError, IndexError, TypeError) as e:
        logger.warning(f"  {model_id}: Unexpected response shape: {e} | data={str(data)[:300]}")
        return None


def _parse_and_validate(raw_text: str, model_id: str) -> AIResult | None:
    """
    Extract, parse, and validate JSON from raw Gemini text output.
    Returns a clean AIResult or None if validation fails.
    """
    # Strip markdown code fences (```json ... ```)
    cleaned = re.sub(r"```(?:json)?\s*|\s*```", "", raw_text).strip()

    # Extract the first JSON object in the output.
    # Strategy 1: find a complete { ... } block
    json_match = re.search(r"\{[^{}]*\}", cleaned, re.DOTALL)
    if json_match:
        try:
            result = json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            logger.warning(f"  {model_id}: JSON parse error: {e} | raw={raw_text!r}")
            return None
    else:
        # Strategy 2: the response was truncated mid-JSON.
        # Extract whatever key-value pairs are present and try to parse them.
        result = _recover_partial_json(cleaned, model_id, raw_text)
        if result is None:
            return None


    # ── Field validation ───────────────────────────────────────────────────────
    required = {"classification", "summary", "difficulty", "estimated_minutes"}
    missing  = required - result.keys()
    if missing:
        logger.warning(f"  {model_id}: Missing fields {missing} in: {result}")
        return None

    # Coerce and clamp values
    try:
        raw_class = str(result["classification"]).strip()
        # Case-insensitive match against valid classifications
        # e.g. 'cie', 'CIE', 'Cie' all → 'CIE'
        classification = next(
            (v for v in VALID_CLASSIFICATIONS if v.lower() == raw_class.lower()),
            None,
        )
        if classification is None:
            # Fallback: try title-case match for common variants
            title_class = raw_class.title()
            classification = next(
                (v for v in VALID_CLASSIFICATIONS if v.lower() == title_class.lower()),
                "Other",
            )
            if classification == "Other":
                logger.warning(f"  {model_id}: Invalid classification '{raw_class}' — using 'Other'")

        summary = str(result["summary"]).strip()
        if not summary:
            summary = "No summary available."

        difficulty = max(1, min(10, int(float(str(result["difficulty"])))))
        estimated_minutes = max(15, int(float(str(result["estimated_minutes"]))))

    except (ValueError, TypeError) as e:
        logger.warning(f"  {model_id}: Type coercion failed: {e} | result={result}")
        return None

    return AIResult(
        classification=classification,
        summary=summary,
        difficulty=difficulty,
        estimated_minutes=estimated_minutes,
        model_used=model_id,
        ai_success=True,
    )


def _backoff(attempt: int) -> None:
    """Sleep for exponential backoff duration."""
    time.sleep(_backoff_seconds(attempt))


def _backoff_seconds(attempt: int) -> float:
    """Calculate backoff with full jitter."""
    # Full jitter: sleep = random(0, min(cap, base * 2^attempt))
    cap = min(MAX_BACKOFF_SECONDS, BASE_BACKOFF_SECONDS * (2 ** attempt))
    return random.uniform(0, cap)


def _recover_partial_json(text: str, model_id: str, raw: str) -> dict | None:
    """
    Recover key-value pairs from a truncated JSON string.

    Example input: '{"classification": "CIE", "summary": "Prepare for the'
    We extract: classification=CIE (string), summary is partial (skip)
    Then fill in defaults for any missing numeric fields from the partial result.
    """
    logger.warning(f"  {model_id}: Truncated JSON — attempting partial recovery")

    extracted: dict = {}

    # Extract complete string values: "key": "value" (complete)
    for m in re.finditer(r'"(\w+)"\s*:\s*"([^"]*)"', text):
        extracted[m.group(1)] = m.group(2)

    # Extract numeric values: "key": 5
    for m in re.finditer(r'"(\w+)"\s*:\s*(-?\d+(?:\.\d+)?)', text):
        try:
            extracted[m.group(1)] = int(float(m.group(2)))
        except ValueError:
            pass

    if not extracted:
        logger.warning(f"  {model_id}: Could not recover any fields from: {raw!r}")
        return None

    logger.info(f"  {model_id}: Partial recovery got fields: {list(extracted.keys())}")
    return extracted


def _parse_retry_after(response: requests.Response) -> float | None:
    """Parse Retry-After header from a 429 response."""
    header = response.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    return None
