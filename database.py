from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from config import POSTGRESQL_DATABASE_DSN

async def get_database_session() -> AsyncSession:
    """DSN을 사용하여 PostgreSQL 데이터베이스에 연결"""
    dsn = POSTGRESQL_DATABASE_DSN
    if not dsn:
        raise ValueError("POSTGRESQL_DATABASE_DSN 환경변수가 설정되지 않았습니다")
    
    # Convert sync DSN to async DSN (postgresql -> postgresql+asyncpg)
    if dsn.startswith('postgresql://'):
        dsn = dsn.replace('postgresql://', 'postgresql+asyncpg://')
    elif dsn.startswith('postgres://'):
        dsn = dsn.replace('postgres://', 'postgresql+asyncpg://')

    engine = create_async_engine(dsn)
    AsyncSessionLocal = sessionmaker(
        class_=AsyncSession, autocommit=False, autoflush=False, expire_on_commit=False, bind=engine
    )
    return AsyncSessionLocal()