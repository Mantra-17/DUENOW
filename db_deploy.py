#!/usr/bin/env python3
"""
db_deploy.py — ClassFlow Watcher Cloud DB Deployment Tool
─────────────────────────────────────────────────────────────────────────────
Helper script to initialize the ClassFlow PostgreSQL schema on a remote database
(e.g., Neon, Supabase, or production RDS instance).

Usage:
    python db_deploy.py --url "postgresql://user:password@host:port/dbname"
    or
    python db_deploy.py (falls back to DATABASE_URL in .env)
"""

import os
import sys
import argparse
import logging

# Ensure backend modules can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'backend'))

# Set up logging for deploy output
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("classflow.deploy")

def main():
    parser = argparse.ArgumentParser(description="Deploy ClassFlow Database Schema to a target PostgreSQL database.")
    parser.path = os.path.dirname(__file__)
    
    parser.add_argument(
        "--url",
        help="Target PostgreSQL database Connection URI. Overrides DATABASE_URL in .env if specified."
    )
    args = parser.parse_args()

    # Load environment from .env if it exists
    if os.path.exists(os.path.join(parser.path, ".env")):
        from dotenv import load_dotenv
        load_dotenv(os.path.join(parser.path, ".env"))

    # Set connection URI
    if args.url:
        logger.info("Using database URL provided via CLI argument.")
        os.environ["DATABASE_URL"] = args.url.strip()
    elif os.getenv("DATABASE_URL"):
        logger.info("Using DATABASE_URL found in environment/.env.")
    else:
        logger.error(
            "❌ No database connection string provided!\n"
            "Please specify one using the --url argument, or set DATABASE_URL in your .env file.\n"
            "Example: python db_deploy.py --url \"postgresql://postgres:password@localhost:5432/classflow\""
        )
        sys.exit(1)

    # Clean the connection string of any surrounding quotes
    db_url = os.environ["DATABASE_URL"]
    if (db_url.startswith('"') and db_url.endswith('"')) or (db_url.startswith("'") and db_url.endswith("'")):
        os.environ["DATABASE_URL"] = db_url[1:-1]

    # Import database module (loads pool after env is set)
    try:
        from db import init_db, check_db_health
    except ImportError as e:
        logger.critical(f"❌ Failed to import database modules: {e}")
        sys.exit(1)

    # 1. Test connection
    logger.info("Connecting to target database and testing health...")
    if check_db_health():
        logger.info("✅ Database connection successful! Host is reachable.")
    else:
        logger.critical("❌ Database health check failed! Check your connection string, credentials, and firewall rules.")
        sys.exit(1)

    # 2. Deploy schema
    logger.info("Deploying tables, triggers, and indexes...")
    try:
        init_db()
        logger.info("🎉 Database deployment completed successfully! ClassFlow schema is ready in the cloud. ✅")
    except Exception as e:
        logger.critical(f"❌ Database deployment failed: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
