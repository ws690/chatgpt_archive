#!/bin/sh
set -eu
umask 077
if [ ! -f /state/vnc.pass ]; then
  echo 'First create a VNC password using the setup instructions.'
  exit 1
fi
export DISPLAY=:99
Xvfb :99 -screen 0 1280x900x24 -nolisten tcp &
xpid=$!
vpid=''
cpid=''
cleanup() {
  if [ -n "$cpid" ]; then
    kill "$cpid" 2>/dev/null || true
    wait "$cpid" 2>/dev/null || true
  fi
  kill "$xpid" ${vpid:+"$vpid"} 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 0' INT TERM
sleep 2
x11vnc -display :99 -rfbport 5900 -rfbauth /state/vnc.pass -forever -shared -noxdamage -quiet &
vpid=$!
python /collector/manual-login.py &
cpid=$!
wait "$cpid"
