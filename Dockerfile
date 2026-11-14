# AI-assisted: written with AI pair-programming assistant.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SPHX_MODE=auto \
    SPHX_CACHE_DIR=/app/cache \
    SPHX_REQUEST_TIMEOUT=30.0 \
    SPHX_IRSA_CONCURRENCY=2 \
    PORT=8000

WORKDIR /app

RUN groupadd --gid 10001 appuser && \
    useradd --uid 10001 --gid 10001 --create-home --shell /usr/sbin/nologin appuser

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/requirements.txt

COPY --chown=appuser:appuser frame.py coords.py flags.py data.py processing.py app.py pyproject.toml README.md /app/
COPY --chown=appuser:appuser static/ /app/static/
COPY --chown=appuser:appuser data/ /app/data/
COPY --chown=appuser:appuser scripts/ /app/scripts/
COPY --chown=appuser:appuser cache/ /app/cache/

USER appuser

EXPOSE 8000

CMD ["sh", "-c", "exec python -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
