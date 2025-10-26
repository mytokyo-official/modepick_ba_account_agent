# Python 3.11 slim 이미지 사용
FROM python:3.11-slim

# 작업 디렉토리 설정
WORKDIR /app

# 시스템 패키지 업데이트 및 PostgreSQL 클라이언트 라이브러리 설치
RUN apt-get update && \
  apt-get install -y --no-install-recommends \
  gcc \
  libpq-dev \
  ca-certificates \
  && rm -rf /var/lib/apt/lists/*


# uv 설치
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Python 의존성 파일 복사
COPY pyproject.toml uv.lock* ./

# uv sync로 의존성 설치
RUN uv sync --frozen --no-dev

# 애플리케이션 코드 복사
COPY . .

# 비root 사용자 생성 및 전환 (보안 강화)
RUN useradd -m -u 1000 appuser && \
    chown -R appuser:appuser /app

USER appuser

# 애플리케이션 실행
CMD ["uv", "run", "python", "main.py"]