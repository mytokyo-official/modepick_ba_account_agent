import ssl

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from config import POSTGRESQL_DATABASE_DSN, APP_ENV

async def get_database_session() -> AsyncSession:
    """DSN을 사용하여 PostgreSQL 데이터베이스에 연결"""
    dsn = POSTGRESQL_DATABASE_DSN
    if not dsn:
        raise ValueError("POSTGRESQL_DATABASE_DSN 환경변수가 설정되지 않았습니다")

    CA_CERT_PATH = "./ap-northeast-2-bundle.pem"
    ssl_context = ssl.create_default_context(cafile=CA_CERT_PATH)


    if APP_ENV == "dev":
        connect_args = {}
    else:
        connect_args = {"ssl": ssl_context}

    engine = create_async_engine(dsn,     connect_args=connect_args,)
    AsyncSessionLocal = sessionmaker(
        class_=AsyncSession, autocommit=False, autoflush=False, expire_on_commit=False, bind=engine
    )
    return AsyncSessionLocal()