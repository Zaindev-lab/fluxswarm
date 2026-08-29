FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HERMES_HOME=/app/hermes_home

WORKDIR /app

# Hermes CLI + agent runtime. Mounted at runtime via volume (see docker-compose).
# The image expects Hermes installed at /app/hermes (bin/hermes) OR provided by
# the host mount; override with FLUXSWARM_HERMES_BIN if installed elsewhere.
ENV FLUXSWARM_HERMES_BIN=/app/hermes/bin/hermes

# Install Python deps first for better layer caching.
COPY backend/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY backend/ /app/backend/
WORKDIR /app/backend

EXPOSE 8787

# Expects Hermes installed at /app/hermes (bin/hermes) and HERMES_HOME mounted.
# Bound to 0.0.0.0 inside the container (the reverse proxy / compose port maps
# control external exposure; do NOT expose 8787 directly to the internet).
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8787"]
