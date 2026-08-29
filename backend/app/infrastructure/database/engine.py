from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.infrastructure.database.models import Base


class Database:
    def __init__(self, database_url: str) -> None:
        if database_url.startswith("sqlite"):
            database_path = make_url(database_url).database
            if database_path and database_path != ":memory:":
                Path(database_path).parent.mkdir(parents=True, exist_ok=True)

        # SQLite 默认 busy timeout 为 0：并发写（如 graph worker 与
        # materializer 同时落库）会立刻抛 "database is locked"。
        # 给予 30s 等待窗口，PostgreSQL 不受影响。
        connect_args = {"timeout": 30} if database_url.startswith("sqlite") else {}
        self.engine: AsyncEngine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            connect_args=connect_args,
        )
        self.session_factory = async_sessionmaker(
            bind=self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
