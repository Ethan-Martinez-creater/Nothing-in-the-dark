"""In-memory SQLite database for fast agent-database tests.

Windows + aiosqlite 上文件 SQLite 的 ``create_schema()`` 需要 60s+（81 张
表的 DDL 逐条落盘 fsync），而内存库 + StaticPool 只需 ~0.6s。本对象复用
``Database`` 的接口子集（engine / session_factory / create_schema /
dispose），只用于只读 Repository / Service 测试，不用于并发写语义测试。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.infrastructure.database.models import Base


class MemoryDatabase:
    def __init__(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        self.session_factory = async_sessionmaker(
            self.engine, expire_on_commit=False
        )

    async def create_schema(self) -> None:
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def dispose(self) -> None:
        await self.engine.dispose()
