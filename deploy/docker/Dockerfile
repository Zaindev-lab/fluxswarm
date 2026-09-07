# FluxSwarm Friends-Beta self-contained Linux/Docker image.
#
# Build with (build context = repo root):
#   docker build -f deploy/docker/Dockerfile -t fluxswarm-beta .
#
# The image is fully self-contained on Linux:
#   - FluxSwarm backend (FastAPI/uvicorn)
#   - Hermes Agent CLI v0.21.0 (pinned 87ad7aa) installed on Linux from source
#     via uv (editable, minimal curated deps -- no Windows host, no hermes.exe)
#   - ECC profiles/skills + Hermes runtime seed under /app/hermes_home
#
# No Windows host-mount is required. FLUXSWARM_HERMES_BIN points at the real
# Linux hermes executable inside the image.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    UV_COMPILE_BYTECODE=0 \
    HERMES_HOME=/app/hermes_home

WORKDIR /app

# --- system build/runtime deps + uv ---
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        git \
        build-essential \
        python3-dev \
        libmagic1 \
    && rm -rf /var/lib/apt/lists/* \
    && python -m pip install --upgrade pip \
    && pip install uv

# --- Hermes Agent v0.21.0 (pinned commit 87ad7aa) fetched from the upstream
#     repository at build time instead of vendored in this repo. The pinned
#     SHA makes the archive immutable and reproducible; HTTPS only. curl
#     --retry tolerates flaky builders (Render, CI). To update upstream: bump
#     the SHA below to the new commit AND rebuild. The editable install stays
#     --no-deps, then the curated minimal runtime deps are installed.
COPY deploy/docker/hermes-minimal-requirements.txt /app/hermes-minimal-requirements.txt
RUN curl --retry 8 --retry-all-errors --fail --location --silent --show-error \
        -o /tmp/hermes-src.tar.gz \
        https://codeload.github.com/NousResearch/hermes-agent/tar.gz/87ad7aa0b072e621c5d4e437b7345471e66f395d \
    && mkdir -p /usr/local/lib \
    && tar -xzf /tmp/hermes-src.tar.gz -C /usr/local/lib \
    && mv /usr/local/lib/hermes-agent-* /usr/local/lib/hermes-agent \
    && rm /tmp/hermes-src.tar.gz \
    && test -f /usr/local/lib/hermes-agent/pyproject.toml
WORKDIR /usr/local/lib/hermes-agent
RUN uv venv venv \
    && . ./venv/bin/activate \
    && uv pip install --python venv/bin/python --no-deps -e . \
    && uv pip install --python venv/bin/python -r /app/hermes-minimal-requirements.txt \
    && ln -sf /usr/local/lib/hermes-agent/venv/bin/hermes /usr/local/bin/hermes \
    && ln -sf /usr/local/lib/hermes-agent/venv/bin/hermes-agent /usr/local/bin/hermes-agent \
    && ln -sf /usr/local/lib/hermes-agent/venv/bin/hermes-acp /usr/local/bin/hermes-acp \
    && hermes --version

# --- FluxSwarm backend python deps (into the base image python, separate from
#     the Hermes venv so the two toolchains never collide) ---
WORKDIR /app
COPY backend/requirements.txt /app/backend-requirements.txt
RUN uv pip install --system -r /app/backend-requirements.txt

# --- Seed HERMES_HOME (clean, secret-free ECC profiles/skills/config for Linux)
COPY deploy/docker/hermes-home/ /app/hermes_home/
RUN mkdir -p /app/hermes_home/kanban/boards /app/hermes_home/logs /app/hermes_home/memories /app/hermes_home/state /app/hermes_home/pairing

# --- FluxSwarm application (data/ is runtime state, provided by a volume,
#     never baked into the image) ---
WORKDIR /app
COPY backend/ /app/backend/
COPY deploy/docker/entrypoint.sh /usr/local/bin/fluxswarm-entrypoint.sh
RUN chmod +x /usr/local/bin/fluxswarm-entrypoint.sh \
    && mkdir -p /app/backend/data

WORKDIR /app/backend

ENV FLUXSWARM_HERMES_BIN=/usr/local/bin/hermes \
    FLUXSWARM_DEMO_MODE=1 \
    REDIS_URL= \
    FLUXSWARM_PAYMENTS=0

# P0.0: deliberately NO baked FLUXSWARM_DEFAULT_PROVIDER / *_MODEL. Baking a
# provider (OpenRouter free or otherwise) as the image default defeats the
# "no silent free tier / no operator default" rule (provider.py Phase 3): the
# free-tier demo surface picks a healthy provider from provider_pool at launch,
# and production must supply a paid key via the environment (envguard). An
# operator who explicitly wants a default injects it in the deployment env.

# The platform may provide PORT; the entrypoint binds 0.0.0.0:$PORT.
ENV PORT=8787

EXPOSE 8787

ENTRYPOINT ["/usr/local/bin/fluxswarm-entrypoint.sh"]
