# Fraxis ERP Next — one image for the API, the outbox worker and the one-off CLIs (migrations, imports).
# Build context is the repo root; see deploy/README.md.

FROM node:22-slim AS web
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
# Lockfile-only layer: cached while package*.json is unchanged. The npm cache mount (BuildKit) survives failed builds,
# and fetch retries ride out a flaky network (a one-off `npm ci` exit 146 on the server, 06.10).
RUN --mount=type=cache,target=/root/.npm     npm config set fetch-retries 5 && npm config set fetch-retry-mintimeout 20000 && npm config set fetch-retry-maxtimeout 120000     && npm ci --no-audit --no-fund
COPY frontend/ ./
# Backend data the frontend imports (harness library seed, ETK marking catalog); tsc -b type-checks the tests that
# import them. test/architecture.test.mjs fails when a new import is not copied here.
COPY backend/data/harness-library/ /src/backend/data/harness-library/
COPY backend/data/etk-marking.json /src/backend/data/etk-marking.json
COPY backend/tests/techcards/harness_generated_sample.json /src/backend/tests/techcards/harness_generated_sample.json
COPY backend/tests/techcards/harness_generated_tails_sample.json /src/backend/tests/techcards/harness_generated_tails_sample.json
COPY backend/tests/techcards/generator_713k4_config.json /src/backend/tests/techcards/generator_713k4_config.json
# Cap the V8 heap so the tsc/vite build stays predictable on a small host instead of being OOM-killed.
ENV NODE_OPTIONS=--max-old-space-size=6144
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
# The entrypoint starts uvicorn only when the database is at this image's migration head (or ERP_AUTO_MIGRATE=1 on a
# first install); migrations on a live server go through deploy/update.sh (backup → rehearsal → check).
CMD ["sh", "/app/backend/docker-entrypoint.sh"]
