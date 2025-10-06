from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from config import POSTGRESQL_DATABASE_DSN

async def get_database_session() -> AsyncSession:
    """DSN을 사용하여 PostgreSQL 데이터베이스에 연결"""
    dsn = POSTGRESQL_DATABASE_DSN
    if not dsn:
        raise ValueError("POSTGRESQL_DATABASE_DSN 환경변수가 설정되지 않았습니다")

    engine = create_async_engine(dsn)
    AsyncSessionLocal = sessionmaker(
        class_=AsyncSession, autocommit=False, autoflush=False, expire_on_commit=False, bind=engine
    )
    return AsyncSessionLocal()