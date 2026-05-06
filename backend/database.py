"""
database.py — Async SQLAlchemy engine + session.

Local dev:  SQLite via aiosqlite  (no server needed)
Production: Set DATABASE_URL to postgresql+asyncpg://...
"""
import os
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

# Default to SQLite for local dev; override with env var for PostgreSQL in Docker
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./facedetect.db",
)

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    # SQLite-specific: allow same connection across threads
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    from models import ROI  # noqa: F401
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
