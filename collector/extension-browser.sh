#!/bin/sh
set -eu
umask 077
export DISPLAY=:99
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp >/tmp/display.log 2>&1 &
xpid=$!
vpid=''
bpid=''
cleanup() {
  if [ -n "$bpid" ]; then kill "$bpid" 2>/dev/null || true; wait "$bpid" 2>/dev/null || true; fi
  kill "$xpid" ${vpid:+"$vpid"} 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' INT TERM
sleep 2
if [ "${EXTENSION_SETUP:-0}" = 1 ]; then
  test -f /state/vnc.pass
  x11vnc -display :99 -rfbport 5900 -rfbauth /state/vnc.pass -forever -shared -noxdamage -quiet >/tmp/screen.log 2>&1 &
  vpid=$!
fi
python /collector/extension-browser.py &
bpid=$!
wait "$bpid"
