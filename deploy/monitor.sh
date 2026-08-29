#!/usr/bin/env bash
# FluxSwarm — host monitoring probe. Exit 0=healthy, 1=down.
# Cron every 5 min:  */5 * * * * /opt/fluxswarm/deploy/monitor.sh || logger -t fluxswarm "DOWN"
URL="${FLUXSWARM_MONITOR_URL:-http://127.0.0.1:8787/health}"
TRUST=$(curl -sf --max-time 10 "$URL" 2>/dev/null) || { echo "fluxswarm DOWN: $URL"; exit 1; }
echo "$TRUST" | grep -q '"ok":true' && { echo "fluxswarm OK"; exit 0; }
echo "fluxswarm unhealthy: $TRUST"; exit 1