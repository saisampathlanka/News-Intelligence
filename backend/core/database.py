from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from config.settings import settings


def _build_engine():
    url = settings.DATABASE_URL
    kwargs = {"pool_pre_ping": True, "echo": settings.DEBUG}

    if "sqlite" in url:
        kwargs["connect_args"] = {"check_same_thread": False}
    else:
        # PostgreSQL: use connection pooling suitable for free-tier
        kwargs["pool_size"] = 5
        kwargs["max_overflow"] = 10
        kwargs["pool_timeout"] = 30

    return create_engine(url, **kwargs)


engine = _build_engine()

if "sqlite" in settings.DATABASE_URL:
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA journal_mode=WAL")
        dbapi_conn.execute("PRAGMA foreign_keys=ON")


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
