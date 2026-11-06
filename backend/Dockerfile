# Production image (Railway builds this; local dev keeps using
# infra/Dockerfile.backend + docker compose with --reload).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# WeasyPrint (PDFs) needs pango/cairo at runtime.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev curl \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b \
    libcairo2 libgdk-pixbuf-2.0-0 libffi8 shared-mime-info \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml alembic.ini ./
COPY alembic ./alembic
COPY app ./app

RUN pip install --upgrade "pip>=26.2.1" && pip install .

EXPOSE 8000

# The web process only serves traffic.  Schema/data/Stripe mutations belong to
# Railway's pre-deploy release job (app.scripts.release), so restarts and
# horizontal replicas can never race them. $PORT comes from Railway.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-2}"]
