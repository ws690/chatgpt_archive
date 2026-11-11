#!/bin/sh
set -eu
export DISPLAY=:98
Xvfb :98 -screen 0 1280x900x24 -nolisten tcp &
xpid=$!
cpid=''
cleanup() {
  if [ -n "$cpid" ]; then
    kill "$cpid" 2>/dev/null || true
    wait "$cpid" 2>/dev/null || true
  fi
  kill "$xpid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' INT TERM
sleep 2
export HEADLESS=0
python /collector/collector.py "${1:-schedule}" &
cpid=$!
wait "$cpid"
