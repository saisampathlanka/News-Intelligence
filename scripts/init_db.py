#!/usr/bin/env python3
"""
Database initialization and management script — NewsIntel v5

Commands:
  python scripts/init_db.py              Create all tables (safe — skips existing)
  python scripts/init_db.py --migrate    Run Alembic migration chain (recommended)
  python scripts/init_db.py --verify     Verify schema is complete
  python scripts/init_db.py --stats      Print database statistics
  python scripts/init_db.py --reset      ⚠ Drop and recreate all tables (destructive)
  python scripts/init_db.py --seed-admin Seed initial admin from env vars
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import inspect as sa_inspect, text
from backend.core.database import Base, engine
from backend.core.logging import logger, setup_logging

# Import all models so SQLAlchemy registers them before create_all()
from backend.models.article import Article                    # noqa
from backend.models.relations import RelatedArticle, TopicStats  # noqa
from backend.models.user import User                          # noqa

setup_logging()

# ── Table + index definitions used by verify_schema() ────────────────────────

REQUIRED_TABLES = ["articles", "related_articles", "topic_stats", "users"]

# Minimum required indexes per table (subset — run alembic for the full set)
REQUIRED_INDEXES = {
    "articles": [
        "ix_articles_content_hash",
        "ix_articles_is_processed",
        "ix_articles_topic",
        "ix_articles_source_name",
        "ix_articles_published_at",
    ],
    "related_articles": [
        "ix_related_articles_article_id",
    ],
    "users": [
        "ix_users_email",
    ],
}

# Minimum required columns per table
REQUIRED_COLUMNS = {
    "articles": [
        "id", "content_hash", "url", "title", "source_name",
        "is_processed", "topic", "bias_score", "bias_label",
        "bias_confidence", "bias_signals_json",  # added in migration 003
        "sentiment_score", "entities_json", "keywords_json",
    ],
    "users": [
        "id", "email", "hashed_password", "role",
        "api_key", "is_active", "created_at", "last_login",
    ],
    "related_articles": [
        "id", "article_id", "related_id", "similarity_score", "relation_type",
    ],
}


# ── Actions ───────────────────────────────────────────────────────────────────

def create_tables() -> None:
    """Create all tables from ORM metadata. Safe — skips existing tables."""
    logger.info("Creating database tables from ORM metadata...")
    Base.metadata.create_all(bind=engine)
    logger.info("✓ Tables created (or already existed)")


def drop_tables() -> None:
    """Drop ALL tables. Destructive — prompts for confirmation."""
    confirm = input("⚠  This will delete ALL data. Type 'yes' to confirm: ").strip()
    if confirm != "yes":
        logger.info("Aborted.")
        sys.exit(0)
    logger.warning("Dropping all tables...")
    Base.metadata.drop_all(bind=engine)
    logger.info("✓ Tables dropped")


def reset_database() -> None:
    """Drop and recreate all tables."""
    drop_tables()
    create_tables()
    logger.info("✓ Database reset complete")


def run_migrations() -> None:
    """Run Alembic migration chain (001 → 005). Recommended over create_tables()."""
    import subprocess
    logger.info("Running Alembic migrations...")
    try:
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            check=True, capture_output=True, text=True,
        )
        logger.info("✓ Migrations complete\n%s", result.stdout)
    except FileNotFoundError:
        logger.error("Alembic not installed. Run: pip install alembic")
        sys.exit(1)
    except subprocess.CalledProcessError as e:
        logger.error("Migration failed:\n%s\n%s", e.stdout, e.stderr)
        sys.exit(1)


def verify_schema() -> bool:
    """Verify all required tables, columns, and indexes are present."""
    logger.info("Verifying database schema...")
    inspector = sa_inspect(engine)
    existing_tables = set(inspector.get_table_names())
    ok = True

    for table in REQUIRED_TABLES:
        if table not in existing_tables:
            logger.error("  ✗ Missing table: %s", table)
            ok = False
            continue
        logger.info("  ✓ Table: %s", table)

        # Columns
        existing_cols = {c["name"] for c in inspector.get_columns(table)}
        for col in REQUIRED_COLUMNS.get(table, []):
            if col not in existing_cols:
                logger.warning("    ✗ Missing column: %s.%s", table, col)
                ok = False
            else:
                logger.debug("    ✓ Column: %s.%s", table, col)

        # Indexes
        existing_idx = {idx["name"] for idx in inspector.get_indexes(table)}
        for idx in REQUIRED_INDEXES.get(table, []):
            if idx not in existing_idx:
                logger.warning("    ✗ Missing index: %s", idx)
                # Non-fatal — run alembic upgrade head to add
            else:
                logger.debug("    ✓ Index: %s", idx)

    if ok:
        logger.info("✓ Schema verification passed")
    else:
        logger.warning(
            "Schema has gaps. Run: alembic upgrade head"
        )
    return ok


def show_stats() -> None:
    """Print database statistics for all tables."""
    logger.info("Database statistics:")
    with engine.connect() as conn:
        # Articles
        try:
            total     = conn.execute(text("SELECT COUNT(*) FROM articles")).scalar() or 0
            processed = conn.execute(text("SELECT COUNT(*) FROM articles WHERE is_processed = TRUE")).scalar() or 0
            logger.info("  Articles:   %d total, %d processed (%d%%)",
                        total, processed, int(processed / total * 100) if total else 0)

            if total > 0:
                topics = conn.execute(text(
                    "SELECT topic, COUNT(*) AS c FROM articles "
                    "WHERE topic IS NOT NULL GROUP BY topic ORDER BY c DESC LIMIT 8"
                )).all()
                for topic, count in topics:
                    logger.info("    %-20s %d articles", topic, count)

                bias = conn.execute(text(
                    "SELECT bias_label, COUNT(*) AS c FROM articles "
                    "WHERE bias_label IS NOT NULL GROUP BY bias_label ORDER BY c DESC"
                )).all()
                if bias:
                    logger.info("  Bias distribution:")
                    for label, count in bias:
                        logger.info("    %-15s %d", label, count)
        except Exception as e:
            logger.warning("  Could not query articles: %s", e)

        # Related articles
        try:
            rels = conn.execute(text("SELECT COUNT(*) FROM related_articles")).scalar() or 0
            logger.info("  Relationships: %d precomputed", rels)
        except Exception:
            pass

        # Users
        try:
            users       = conn.execute(text("SELECT COUNT(*) FROM users")).scalar() or 0
            admins      = conn.execute(text("SELECT COUNT(*) FROM users WHERE role = 'admin'")).scalar() or 0
            active      = conn.execute(text("SELECT COUNT(*) FROM users WHERE is_active = TRUE")).scalar() or 0
            with_apikey = conn.execute(text("SELECT COUNT(*) FROM users WHERE api_key IS NOT NULL")).scalar() or 0
            logger.info(
                "  Users: %d total (%d admin, %d active, %d with API key)",
                users, admins, active, with_apikey,
            )
        except Exception as e:
            logger.warning("  Could not query users: %s", e)


def seed_admin() -> None:
    """
    Create the first admin user from INITIAL_ADMIN_EMAIL / INITIAL_ADMIN_PASSWORD
    environment variables. Skips if a user with that email already exists.
    """
    email    = os.environ.get("INITIAL_ADMIN_EMAIL")
    password = os.environ.get("INITIAL_ADMIN_PASSWORD")

    if not email or not password:
        logger.error(
            "Set INITIAL_ADMIN_EMAIL and INITIAL_ADMIN_PASSWORD env vars first."
        )
        sys.exit(1)

    from backend.core.database import SessionLocal
    from backend.core.security import hash_password

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.email == email.strip().lower()).first()
        if existing:
            logger.info("User %s already exists (role=%s) — skipping seed.", email, existing.role)
            return

        admin = User(
            email=email.strip().lower(),
            hashed_password=hash_password(password),
            role="admin",
            is_active=True,
        )
        db.add(admin)
        db.commit()
        logger.info("✓ Admin user created: %s", email)
        logger.warning(
            "Remove INITIAL_ADMIN_EMAIL and INITIAL_ADMIN_PASSWORD from your "
            "environment after first login."
        )
    except Exception as e:
        db.rollback()
        logger.error("Failed to seed admin: %s", e)
        sys.exit(1)
    finally:
        db.close()


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="NewsIntel v5 database management",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--reset",      action="store_true", help="⚠ Drop and recreate all tables")
    parser.add_argument("--migrate",    action="store_true", help="Run Alembic migration chain (recommended)")
    parser.add_argument("--verify",     action="store_true", help="Verify schema completeness")
    parser.add_argument("--stats",      action="store_true", help="Print database statistics")
    parser.add_argument("--seed-admin", action="store_true", help="Create admin from INITIAL_ADMIN_* env vars")
    args = parser.parse_args()

    if args.migrate:
        run_migrations()
        verify_schema()
    elif args.reset:
        reset_database()
        verify_schema()
    elif args.verify:
        ok = verify_schema()
        sys.exit(0 if ok else 1)
    elif args.stats:
        show_stats()
    elif args.seed_admin:
        seed_admin()
    else:
        create_tables()
        verify_schema()


if __name__ == "__main__":
    main()
