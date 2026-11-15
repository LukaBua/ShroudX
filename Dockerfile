# AI-assisted: written with AI pair-programming assistant.
FROM node:22-alpine AS frontend-build
WORKDIR /frontend
COPY frontend/package*.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

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

COPY --chown=appuser:appuser frame.py coords.py flags.py data.py gaia.py motion.py movers.py processing.py tracking.py spectrum.py app.py pyproject.toml README.md /app/
COPY --from=frontend-build --chown=appuser:appuser /frontend/dist/ /app/frontend/dist/
COPY --chown=appuser:appuser frontend_compat/ /app/frontend_compat/
COPY --chown=appuser:appuser experiments/ /app/experiments/
COPY --chown=appuser:appuser static/ /app/static/
COPY --chown=appuser:appuser data/ /app/data/
COPY --chown=appuser:appuser scripts/ /app/scripts/
COPY --chown=appuser:appuser cache/ /app/cache/

USER appuser

EXPOSE 8000

CMD ["sh", "-c", "exec python -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"]
