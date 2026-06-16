import os
import pathlib
import sys

import pytest
import pytest_asyncio

_BACKEND = pathlib.Path(__file__).resolve().parents[1]
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

DATABASE_URL = os.environ.get("DATABASE_URL")


@pytest_asyncio.fixture
async def session():
    """Per-test AsyncSession against the real test Postgres.

    Skips when DATABASE_URL is unset (local runs without a DB). Ensures tables
    exist (idempotent over alembic-migrated schema), truncates the tables the
    service touches, yields a session, disposes the engine afterward.
    """
    if not DATABASE_URL:
        pytest.skip("DATABASE_URL not set; skipping DB integration tests")

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )
    from app.db import Base
    import app.models.task  # noqa: F401 — register tables on Base.metadata
    import app.models.outbox  # noqa: F401

    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(text("TRUNCATE tasks, outbox_events RESTART IDENTITY CASCADE"))
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with maker() as s:
            yield s
    finally:
        await engine.dispose()
