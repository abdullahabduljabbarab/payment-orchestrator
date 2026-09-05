import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.models import Base

TEST_DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL",
    "postgresql://ledger:ledger@localhost:5432/payments_test",
)


def _ensure_database() -> None:
    """Create the test database if it does not exist, so the suite is
    self-bootstrapping against a running PostgreSQL server."""
    url = make_url(TEST_DATABASE_URL)
    server = create_engine(
        url.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with server.connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"),
            {"n": url.database},
        ).scalar()
        if not exists:
            conn.execute(text(f'CREATE DATABASE "{url.database}"'))
    server.dispose()


_ensure_database()

engine = create_engine(TEST_DATABASE_URL)
TestSession = sessionmaker(bind=engine)


@pytest.fixture(autouse=True)
def setup_db():
    with engine.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS payment_events CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS provider_attempts CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS outbox_events CASCADE"))
        conn.execute(text("DROP TABLE IF EXISTS payments CASCADE"))
        conn.execute(text("DROP TYPE IF EXISTS paymentstate CASCADE"))
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.execute(text("DROP TYPE IF EXISTS paymentstate CASCADE"))


@pytest.fixture
def db():
    session = TestSession()
    try:
        yield session
    finally:
        session.close()
