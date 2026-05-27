FROM python:3.12-slim

WORKDIR /app

COPY backend/pyproject.toml ./
RUN pip install --no-cache-dir \
        "fastapi>=0.115" \
        "uvicorn[standard]>=0.30" \
        "jinja2>=3.1" \
        "sse-starlette>=2.1" \
        "sqlalchemy[asyncio]>=2.0" \
        "asyncpg>=0.29" \
        "pydantic-settings>=2.4" \
        "httpx>=0.27" \
        "python-multipart>=0.0.9"

COPY backend/app ./app
COPY protocol ./protocol

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
