#!/bin/sh
# Comparte la web por un túnel rápido de Cloudflare (URL temporal *.trycloudflare.com). Imprime la URL.
# Idempotente: reutiliza el servidor y el túnel si ya están en marcha. Para pararlos: scripts/share.sh stop
cd "$(dirname "$0")/.." || exit 1
LOG=/tmp/zarzuelo-tunnel.log
PORT=4173
if [ "$1" = "stop" ]; then
  [ -f /tmp/zarzuelo-preview.pid ] && kill "$(cat /tmp/zarzuelo-preview.pid)" 2>/dev/null
  [ -f /tmp/zarzuelo-tunnel.pid ] && kill "$(cat /tmp/zarzuelo-tunnel.pid)" 2>/dev/null
  rm -f /tmp/zarzuelo-preview.pid /tmp/zarzuelo-tunnel.pid; echo parado; exit 0
fi
.venv/bin/kb export >/dev/null 2>&1
(cd web && npm run build >/tmp/zarzuelo-build.log 2>&1) || { echo "fallo al compilar la web (ver /tmp/zarzuelo-build.log)"; exit 1; }
alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }
if ! alive /tmp/zarzuelo-preview.pid; then
  (cd web && setsid nohup npx vite preview --port $PORT --host 127.0.0.1 >/tmp/zarzuelo-preview.log 2>&1 & echo $! > /tmp/zarzuelo-preview.pid)
  sleep 4
fi
if ! alive /tmp/zarzuelo-tunnel.pid; then
  : > "$LOG"
  setsid nohup cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >"$LOG" 2>&1 &
  echo $! > /tmp/zarzuelo-tunnel.pid
fi
for i in $(seq 1 30); do
  URL=$(grep -o 'https://[a-z0-9-]*\.trycloudflare\.com' "$LOG" | head -1)
  [ -n "$URL" ] && { echo "$URL"; exit 0; }
  sleep 1
done
echo "no se obtuvo URL del túnel (ver $LOG)"; exit 1
