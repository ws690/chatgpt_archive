#!/bin/sh
set -eu
cd "$(dirname "$0")"
# QNAP's Docker wrapper changes HOME; preserve the actual user's config path.
export DOCKER_CONFIG="${DOCKER_CONFIG:-$HOME/.docker}"
if ! command -v docker >/dev/null 2>&1 && [ -x /sbin/getcfg ]; then
  task_container_path=$(/sbin/getcfg container-station Install_Path -f /etc/config/qpkg.conf)
  PATH="$task_container_path/bin:$PATH"
  export PATH
fi
if [ "${1:-}" = extension ]; then
  shift
  if [ "${1:-}" = setup ]; then
    shift
    exec docker compose -f compose.yaml -f compose.collector.yaml -f compose.extension.yaml -f compose.extension-setup.yaml "$@"
  fi
  exec docker compose -f compose.yaml -f compose.collector.yaml -f compose.extension.yaml "$@"
fi
if [ "${1:-}" = session ]; then
  shift
  if [ "${1:-}" = auth ]; then
    shift
    exec docker compose -f compose.yaml -f compose.collector.yaml -f compose.session.yaml -f compose.manual.yaml "$@"
  fi
  exec docker compose -f compose.yaml -f compose.collector.yaml -f compose.session.yaml "$@"
fi
if [ "${1:-}" = firefox ]; then
  shift
  if [ "${1:-}" = auth ]; then
    shift
    exec docker compose -f compose.yaml -f compose.collector.yaml -f compose.firefox.yaml -f compose.auth.yaml "$@"
  fi
  exec docker compose -f compose.yaml -f compose.collector.yaml -f compose.firefox.yaml "$@"
fi
if [ "${1:-}" = chrome ]; then
  shift
  if [ "${1:-}" = auth ]; then
    shift
    exec docker compose -f compose.yaml -f compose.collector.yaml -f compose.chrome.yaml -f compose.auth.yaml "$@"
  fi
  exec docker compose -f compose.yaml -f compose.collector.yaml -f compose.chrome.yaml "$@"
fi
if [ "${1:-}" = auth ]; then
  shift
  exec docker compose -f compose.yaml -f compose.collector.yaml -f compose.auth.yaml "$@"
fi
exec docker compose -f compose.yaml -f compose.collector.yaml "$@"
