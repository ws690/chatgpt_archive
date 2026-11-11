#!/bin/sh
set -eu
umask 077
project=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
state=${1:-"$project/../state"}
spool=${2:-"$state"}
status_file="$state/extension-status.json"
incident="$spool/qnap-alert-incident"
logger=${CHATGPT_WATCHDOG_LOG_TOOL:-/sbin/log_tool}
touch "$spool/qnap-watchdog-heartbeat"

field() {
    sed -n "s/^[[:space:]]*\"$1\":[[:space:]]*\"\([a-z_]*\)\".*/\1/p" "$status_file" | head -n 1
}

issue=''
if [ ! -f "$status_file" ] || [ -L "$status_file" ]; then
    issue=status_missing
else
    status=$(field status)
    reason=$(field reason)
    now=$(date +%s)
    changed=$(stat -c %Y "$status_file")
    age=$((now - changed))
    case "$status" in
        attention_required|partial_capture_with_errors)
            case "$reason" in
                authentication_required|authentication_challenge|chatgpt_unreachable|route_error)
                    issue=$reason ;;
                run_cancelled) exit 0 ;;
                *) issue=backup_failed ;;
            esac ;;
        running) if [ "$age" -gt 2400 ]; then issue=backup_stalled; else exit 0; fi ;;
        partial_capture|up_to_date) if [ "$age" -gt 129600 ]; then issue=backup_stale; fi ;;
        *) issue=backup_status_unknown ;;
    esac
fi

if [ -z "$issue" ]; then
    rm -f "$incident"
    exit 0
fi
if [ -f "$incident" ] && [ "$(cat "$incident")" = "$issue" ]; then
    exit 0
fi
case "$issue" in
    authentication_required) message='ChatGPT sign-in is required on the NAS browser.' ;;
    authentication_challenge) message='ChatGPT requires manual verification on the NAS browser.' ;;
    chatgpt_unreachable|route_error) message='The NAS browser could not reach or load ChatGPT.' ;;
    backup_stalled) message='The NAS backup has been running for more than 40 minutes.' ;;
    backup_stale) message='The NAS has no successful ChatGPT backup check in the past 36 hours.' ;;
    status_missing|backup_status_unknown) message='The NAS backup status is missing or unreadable.' ;;
    *) message='A ChatGPT backup attempt failed. Check the NAS collector status.' ;;
esac
"$logger" -a "ChatGPT NAS backup alert: $message" -t 1
temp=$(mktemp "$spool/.qnap-alert-incident.XXXXXX")
trap 'rm -f "$temp"' EXIT
printf '%s\n' "$issue" > "$temp"
mv -f "$temp" "$incident"
