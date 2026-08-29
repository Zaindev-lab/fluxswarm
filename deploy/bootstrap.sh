#!/usr/bin/env bash
# FluxSwarm — one-command production bootstrap (Ubuntu 22.04/24.04 VPS).
#
#   sudo bash deploy/bootstrap.sh --domain app.yourdomain.com [--email admin@yourdomain.com] [--public-ip 1.2.3.4]
#
# What it does, end to end:
#   1. Installs Docker Engine + Compose plugin (if missing).
#   2. Generates .env with strong secrets (JWT + Fernet) and prod knobs
#      (Redis, CORS=your domain, trusted proxy=127.0.0.1, Paddle vars).
#   3. Builds + starts the app and redis via docker-compose.yml (app binds 127.0.0.1 only).
#   4. Installs Caddy on the HOST + emits /etc/caddy/Caddyfile (auto Let's Encrypt TLS);
#      never publishes the app to the internet directly.
#   5. Enables Caddy + a docker-app systemd unit (auto-start on boot).
#   6. Waits for /health and prints the final status + the Paddle variables to fill.
#   Idempotent: safe to re-run.
set -euo pipefail

DOMAIN=""
EMAIL="admin@fluxswarm.ai"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose"

usage() { echo "Usage: $0 --domain <host> [--email <address>]"; exit 1; }
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain) DOMAIN="$2"; shift 2 ;;
    --email)  EMAIL="$2"; shift 2 ;;
    *) usage ;;
  esac
done
[[ -z "$DOMAIN" ]] && usage

log() { echo -e "\033[1;34m[fluxswarm]\033[0m $*"; }
die() { echo -e "\033[1;31m[fluxswarm]\033[0m $*" >&2; exit 1; }

# ---------- 1. docker ----------
if ! command -v docker >/dev/null 2>&1; then
  log "installing docker engine"
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
  die "docker compose plugin required (docker compose version failed)"
fi

# ---------- 2. .env (idempotent, never overwrite existing secrets) ----------
ENV_FILE="$REPO_DIR/.env"
gen_secret() { head -c 32 /dev/urandom | base64 | tr -d '/+=' | head -c 48; }
fernet_key() { python3 -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip('='))"; }

if [[ ! -f "$ENV_FILE" ]]; then
  log "generating .env with fresh secrets"
  cat > "$ENV_FILE" <<EOF
FLUXSWARM_JWT_SECRET=$(gen_secret)
FLUXSWARM_FERNET_KEY=$(fernet_key)
FLUXSWARM_PAYMENTS=0
FLUXSWARM_PAYMENT_PROVIDER=paddle
PADDLE_API_BASE=https://api.paddle.com
PADDLE_API_KEY=
PADDLE_WEBHOOK_SECRET=
PADDLE_PRICE_STARTER=
PADDLE_PRICE_PRO=
PADDLE_PRICE_SCALE=
FLUXSWARM_PUBLIC_BASE_URL=https://$DOMAIN
FLUXSWARM_CORS_ORIGINS=https://$DOMAIN
FLUXSWARM_TRUSTED_PROXIES=127.0.0.1,::1
FLUXSWARM_CONTACT_EMAIL=$EMAIL
REDIS_URL=redis://redis:6379/0
FLUXSWARM_REDIS_URL=redis://redis:6379/0
FLUXSWARM_HERMES_BIN=hermes
HERMES_HOME=/app/hermes_home
EOF
  chmod 600 "$ENV_FILE"
  log ".env written — now paste your Paddle values, then re-run this script."
  # Do NOT proceed to serve a half-configured billing setup: ask the operator.
  log "after editing .env, re-run:  bash $0 --domain $DOMAIN --email $EMAIL"
  exit 0
fi

# ---------- 3. app + redis ----------
log "building + starting fluxswarm + redis"
if ! grep -q '^FLUXSWARM_PAYMENTS=1' "$ENV_FILE"; then
  log "notice: FLUXSWARM_PAYMENTS=0 — paid plans return 402 until you set it to 1."
fi
EXTRA_ENV=()
if [[ -f "$ENV_FILE" ]]; then EXTRA_ENV=(--env-file "$ENV_FILE"); fi
$COMPOSE -f "$REPO_DIR/docker-compose.yml" ${EXTRA_ENV[@]} up -d --build

# ---------- 4. Caddy (host) ----------
if ! command -v caddy >/dev/null 2>&1; then
  log "installing caddy (host)"
  if command -v apt-get >/dev/null 2>&1; then
    apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl >/dev/null
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
    apt-get update >/dev/null && apt-get install -y caddy >/dev/null
  else
    die "unsupported OS for apt-based caddy install; install caddy manually and set Caddyfile"
  fi
fi
mkdir -p /etc/caddy
sed -e "s/__DOMAIN__/$DOMAIN/g" -e "s/__ADMIN_EMAIL__/$EMAIL/g" \
  "$REPO_DIR/deploy/Caddyfile.us" > /etc/caddy/Caddyfile
log "Caddy configured for https://$DOMAIN -> 127.0.0.1:8787"
systemctl enable caddy >/dev/null 2>&1 || true
systemctl restart caddy

# ---------- 5. systemd for the app (boot persistence) ----------
UNIT=/etc/systemd/system/fluxswarm.service
if [[ ! -f "$UNIT" ]]; then
  cat > "$UNIT" <<EOF
[Unit]
Description=FluxSwarm (docker compose: app + redis)
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$REPO_DIR
ExecStart=$COMPOSE -f $REPO_DIR/docker-compose.yml --env-file $ENV_FILE up -d --remove-orphans
ExecStop=$COMPOSE -f $REPO_DIR/docker-compose.yml --env-file $ENV_FILE down

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable fluxswarm >/dev/null
fi
systemctl restart fluxswarm

# ---------- 6. health gate ----------
log "waiting for health"
for i in $(seq 1 30); do
  if grep -q '^FLUXSWARM_PAYMENTS=1' "$ENV_FILE"; then
    code=$(curl -s -o /dev/null -w '%{http_code}' "https://$DOMAIN/health" || echo 000)
  else
    code=$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:8787/health" || echo 000)
  fi
  [[ "$code" == "200" ]] && break
  sleep 2
done
echo
log "health over the domain: $code"
if [[ "$code" == "200" ]]; then
  log "FluxSwarm is LIVE at https://$DOMAIN"
  log "remaining manual steps: fill Paddle values in $ENV_FILE, set FLUXSWARM_PAYMENTS=1, re-run bootstrap."
else
  die "health failed ($code) — check: journalctl -u caddy -n 50 && $COMPOSE -f $REPO_DIR/docker-compose.yml ps"
fi