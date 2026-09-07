#!/usr/bin/env bash
# FluxSwarm — host monitoring probe. Exit 0=healthy, 1=down.
# Cron every 5 min:  */5 * * * * /opt/fluxswarm/deploy/monitor.sh || logger -t fluxswarm "DOWN"
# P4.3: fail closed unless the DB responds AND the Hermes runtime binary exists.
# A stale-but-alive app (e.g. DB hung, hermes.exe missing) must be reported as
# DOWN so an operator investigates, not "OK".
URL="${FLUXSWARM_MONITOR_URL:-http://127.0.0.1:8787/health}"
METRICS="${FLUXSWARM_MONITOR_URL:-http://127.0.0.1:8787/metrics}"
TRUST=$(curl -sf --max-time 10 "$URL" 2>/dev/null) || { echo "fluxswarm DOWN: $URL"; exit 1; }
echo "$TRUST" | grep -q '"ok":true' || { echo "fluxswarm unhealthy: $TRUST"; exit 1; }
echo "$TRUST" | grep -q '"db_ok": true' || { echo "fluxswarm DOWN (db_ok=false): $TRUST"; exit 1; }
echo "$TRUST" | grep -q '"hermes_bin_ok": true' || { echo "fluxswarm DOWN (hermes_bin_ok=false): $TRUST"; exit 1; }
MET=$(curl -sf --max-time 10 "$METRICS" 2>/dev/null) || MET=""
if [[ -n "$MET" ]]; then
  echo "fluxswarm OK (active=$(echo "$MET" | awk '/^fluxswarm_active_boards/{print $2}'), sealed=$(echo "$MET" | awk '/^fluxswarm_sealed_boards/{print $2}'))"
else
  echo "fluxswarm OK"
fi
exit 0