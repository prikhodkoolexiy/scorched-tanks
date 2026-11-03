#!/bin/bash
# Scorched Tanks: запуск сервера и публичной ссылки через Tailscale Funnel.
cd "$(dirname "$0")"
PORT=8787

TS=""
if command -v tailscale >/dev/null 2>&1; then
  TS="$(command -v tailscale)"
elif [ -x "/Applications/Tailscale.app/Contents/MacOS/Tailscale" ]; then
  TS="/Applications/Tailscale.app/Contents/MacOS/Tailscale"
fi

PUBLIC_URL=""
CLEANUP=0
if [ -n "$TS" ]; then
  echo ""
  echo "Включаю публичную ссылку через Tailscale Funnel..."
  echo "Если появится ссылка login.tailscale.com, откройте её и включите Funnel. Это нужно один раз."
  echo ""
  if "$TS" funnel status 2>&1 | grep -qi "no serve config"; then CLEANUP=1; fi
  if "$TS" funnel --bg "$PORT"; then
    PUBLIC_URL="$("$TS" funnel status 2>/dev/null | grep -oE 'https://[A-Za-z0-9.-]+\.ts\.net' | head -1)"
  fi
  if [ -z "$PUBLIC_URL" ]; then
    echo "Funnel не включился. Игра будет доступна только в вашей сети и через Tailscale."
  fi
else
  echo "Tailscale не найден. Игра будет доступна только в вашей сети."
fi
export PUBLIC_URL PORT

cleanup() {
  if [ "$CLEANUP" = "1" ] && [ -n "$TS" ]; then
    "$TS" funnel reset >/dev/null 2>&1
    echo "Публичная ссылка выключена."
  fi
}
trap cleanup EXIT
trap 'exit 0' INT TERM HUP

if command -v node >/dev/null 2>&1; then
  RUN="node server.js"
elif command -v python3 >/dev/null 2>&1 && python3 -c "import sys" >/dev/null 2>&1; then
  RUN="python3 server.py"
else
  echo "Не найден ни Node.js, ни Python 3."
  echo "Поставьте любой из них: https://nodejs.org  или  xcode-select --install"
  echo "Игра на одном компьютере работает и без сервера: откройте index.html."
  read -p "Нажмите Enter, чтобы закрыть..."
  exit 1
fi

(sleep 1; open "http://localhost:$PORT") &
$RUN
