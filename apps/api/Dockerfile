# Katalog Kilat API — multi-stage build.
# One container serves the API and the built SPA. Stage 1 compiles the SPA
# with Node; stage 2 is the runtime and contains no Node, no node_modules and
# no web source — only the built dist/ output and the Python service.
#
# The build context is the repository root, so every source path below
# is repo-relative: apps/web/... and apps/api/...

# --- Stage 1: build the SPA --------------------------------------------------
FROM node:26-alpine AS web

WORKDIR /web

# Dependency layer first: it stays cached when only the SPA source changes.
# No --omit=dev and no NODE_ENV=production here: vite and typescript are
# devDependencies and the build needs them.
# .npmrc rides along: npm ci needs legacy-peer-deps with TypeScript 7.
COPY apps/web/package.json apps/web/package-lock.json apps/web/.npmrc ./
RUN npm ci

COPY apps/web/ ./

# `npm run build` is `tsc --noEmit && vite build`: a type error fails the
# image build. That is intended, do not bypass it.
RUN npm run build

# --- Stage 2: runtime --------------------------------------------------------
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /srv

# Dev requirements include the runtime file plus pytest: the drift gate runs
# inside this container, the only valid Python environment.
COPY apps/api/requirements.txt apps/api/requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY apps/api/app/ ./app/

# Gate commands run from /srv: `python -m scripts.dump_openapi` and
# `pytest apps/api/tests -q`. The committed OpenAPI artifact is baked in so
# the drift test compares against it; pytest.ini puts /srv on sys.path.
COPY apps/api/scripts/ ./scripts/
COPY apps/api/pytest.ini ./
COPY apps/api/tests/ ./apps/api/tests/
COPY apps/api/openapi.json ./openapi.json

# The built SPA lands in /srv/static, which main.py resolves as the sibling
# directory of the app package.
COPY --from=web /web/dist ./static/

EXPOSE 8000

# No --reload: this is the container, not a dev server.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
