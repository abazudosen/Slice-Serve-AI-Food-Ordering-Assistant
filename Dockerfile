FROM python:3.12-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    DATABASE_PATH=/app/data/orders.sqlite3 \
    PORT=8000

WORKDIR /app

RUN pip install --no-cache-dir uv \
    && useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/data \
    && chown appuser:appuser /app/data

COPY --chown=appuser:appuser . .

RUN uv sync --frozen --no-dev

USER appuser

EXPOSE 8000

CMD ["sh", "-c", "uv run uvicorn main:app --host 0.0.0.0 --port ${PORT}"]
