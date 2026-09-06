FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl procps postgresql-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip

WORKDIR /workspace

# Keep the runner image buildable/unused here; parity with deploy/docker runs.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1