# FluxSwarm Hermes sandbox runner image (friend-of-the-project pattern).
#
# The sandbox image contains ONLY the Hermes Agent CLI + the ECC skill seed —
# no backend, no DB, no secrets baked in. It is purpose-built to be launched
# by `hermes_docker.py` with a hardened default profile:
#
#   --network=none  (no egress: agent tools cannot phone home)
#   --cap-drop ALL --cap-add DAC_OVERRIDE,CHOWN,FOWNER
#   --security-opt no-new-privileges
#   --pids-limit 256 --memory=2g --cpus=1.5
#   --read-only  root fs read-only + tmpfs for /tmp, /var/tmp, /run
#
# Provider credentials are injected per-container via env vars at dispatch
# time (never in the image). The kanban workspace is bind-mounted READ-ONLY;
# agents write only under the tmpfs. Outcomes are copied back to the host by
# the dispatcher (docker cp), never by a writable host mount.
#
# Build (context = repo root):
#   docker build -f deploy/docker/hermes-runner.Dockerfile -t fluxswarm/hermes-runner:latest .
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    DEBIAN_FRONTEND=noninteractive \
    UV_COMPILE_BYTECODE=0 \
    HERMES_HOME=/app/hermes_home \
    FLUXSWARM_HERMES_BIN=/usr/local/bin/hermes

WORKDIR /app

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

# --- Upgrade system SQLite to 3.51.3+ BEFORE Hermes is installed (WAL-reset
#     corruption fix). Python's _sqlite3 (base interpreter + Hermes venv) links
#     libsqlite3.so.0 dynamically, so replacing it in /usr/local/lib covers both.
RUN apt-get update && apt-get install -y --no-install-recommends wget unzip \
    && wget -q https://www.sqlite.org/2026/sqlite-amalgamation-3510300.zip \
    && unzip -q sqlite-amalgamation-3510300.zip \
    && cd sqlite-amalgamation-3510300 \
    && gcc -O2 -DSQLITE_ENABLE_FTS5 -DSQLITE_ENABLE_JSON1 -fPIC -shared \
         -Wl,-soname,libsqlite3.so.0 -o libsqlite3.so.0 sqlite3.c \
    && cp libsqlite3.so.0 /usr/local/lib/ && ldconfig \
    && cd .. && rm -rf sqlite-amalgamation-3510300* \
    && apt-get remove -y wget unzip && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

# --- Hermes Agent (pinned source, minimal curated deps) ---
COPY deploy/docker/hermes-src/ /usr/local/lib/hermes-agent/
COPY deploy/docker/hermes-minimal-requirements.txt /app/hermes-minimal-requirements.txt
WORKDIR /usr/local/lib/hermes-agent
RUN uv venv venv \
    && . ./venv/bin/activate \
    && uv pip install --python venv/bin/python --no-deps -e . \
    && uv pip install --python venv/bin/python -r /app/hermes-minimal-requirements.txt \
    && ln -sf /usr/local/lib/hermes-agent/venv/bin/hermes /usr/local/bin/hermes \
    && ln -sf /usr/local/lib/hermes-agent/venv/bin/hermes-agent /usr/local/bin/hermes-agent \
    && ln -sf /usr/local/lib/hermes-agent/venv/bin/hermes-acp /usr/local/bin/hermes-acp \
    && hermes --version

# --- ECC profiles/skills/config seed (static, secret-free) ---
WORKDIR /app
COPY deploy/docker/hermes-home/ /app/hermes_home/
RUN mkdir -p /app/hermes_home/kanban/boards /app/hermes_home/logs \
    /app/hermes_home/memories /app/hermes_home/state /app/hermes_home/pairing \
    /workspace /workspace/outcomes

# Run as an unprivileged user (hardening: the container must never run code as
# root; the dispatcher also drops all capabilities).
RUN useradd --create-home --uid 1000 hermes \
    && chown -R hermes:hermes /app/hermes_home /workspace

USER hermes
WORKDIR /workspace

# The ENTRYPOINT receives the `hermes kanban ...` argv from the dispatcher.
ENTRYPOINT ["/usr/local/bin/hermes", "kanban"]

# --network=none with a fixed internal port is a no-op here; kept explicit so a
# broken override is obvious.
EXPOSE 0