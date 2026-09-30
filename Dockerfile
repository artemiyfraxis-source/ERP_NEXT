# Fraxis ERP Next — one image for the API, the outbox worker and the one-off CLIs (migrations, imports).
# Build context is the repo root; see deploy/README.md.

FROM node:22-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build && npm run build:tcgen

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 TZ=Europe/Moscow
WORKDIR /app/backend
# Node.js runs the techcard generator form without a browser (app.workflows.techcards_all generate).
RUN apt-get update && apt-get install -y --no-install-recommends nodejs && rm -rf /var/lib/apt/lists/*
COPY backend/requirements.lock.txt ./
RUN pip install -r requirements.lock.txt
COPY backend/ ./
COPY scripts/ /app/scripts/
COPY --from=web /src/frontend/dist /app/frontend/dist
COPY --from=web /src/frontend/dist-tools /app/frontend/dist-tools
RUN useradd --system --uid 10001 erp && chown -R erp /app
USER erp
EXPOSE 8000
# main.py serves /app/frontend/dist itself (parents[2] of app/main.py), so one process covers site + API.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips='*'"]
