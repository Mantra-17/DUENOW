"""
run.py — ClassFlow Watcher entry point
Run from project root: python run.py  OR  python backend/api.py
"""
import os
import sys

# Add backend/ to path so imports (db, ai_helper etc.) resolve
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

from backend.api import app
from backend.db import init_db
import logging

logger = logging.getLogger("classflow.run")

if __name__ == "__main__":
    try:
        init_db()
    except Exception as exc:
        logger.critical(f"Failed to initialise database: {exc}", exc_info=True)
        raise SystemExit(1) from exc

    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    port  = int(os.getenv("PORT", "5001"))

    logger.info(f"🚀 ClassFlow starting on http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=debug)
