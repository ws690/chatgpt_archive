#!/bin/sh
set -eu
[ "$(id -u)" -eq 0 ] || { printf '%s\n' 'Run this installer with sudo.' >&2; exit 1; }
user=${SUDO_USER:-}
case "$user" in ''|*[!a-zA-Z0-9_-]*|root|admin)
    printf '%s\n' 'Run sudo from the unprivileged NAS backup account.' >&2; exit 1 ;;
esac
watchdog=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)/qnap-watchdog.sh
case "$watchdog" in *[!a-zA-Z0-9_./-]*)
    printf '%s\n' 'Use a project path without spaces or shell metacharacters.' >&2; exit 1 ;;
esac
[ -f "$watchdog" ]
state=$(CDPATH= cd -- "$(dirname -- "$watchdog")/../../state" && pwd)
case "$state" in *[!a-zA-Z0-9_./-]*)
    printf '%s\n' 'Use a state path without spaces or shell metacharacters.' >&2; exit 1 ;;
esac
installed=/etc/config/chatgpt-nas-watchdog.sh
spool=/etc/config/chatgpt-nas-watchdog-state
if [ -L "$installed" ] || [ -L "$spool" ]; then
    printf '%s\n' 'Unsafe QNAP watchdog path.' >&2
    exit 1
fi
mkdir -p "$spool"
chown 0:0 "$spool"
chmod 755 "$spool"
/usr/bin/install -o 0 -g 0 -m 700 "$watchdog" "$installed"
config=/etc/config/crontab
tag='# chatgpt-nas-watchdog'
legacy="*/5 * * * * /usr/bin/sudo -H -u $user /bin/sh $watchdog $tag"
line="*/5 * * * * /bin/sh $installed $state $spool $tag"
if grep -Fqx "$line" "$config"; then
    /usr/bin/crontab "$config"
    /etc/init.d/crond.sh restart
    exit 0
fi
if grep -Fq "$tag" "$config" && ! grep -Fqx "$legacy" "$config"; then
    printf '%s\n' 'A different ChatGPT watchdog entry exists. Review /etc/config/crontab first.' >&2
    exit 1
fi
previous=$(mktemp /etc/config/.chatgpt-cron-backup.XXXXXX)
cp -p "$config" "$previous"
updated=$(mktemp /etc/config/.chatgpt-cron-update.XXXXXX)
trap 'rm -f "$updated"' EXIT
cp -p "$config" "$updated"
awk -v legacy="$legacy" '$0 != legacy' "$previous" > "$updated"
printf '\n%s\n' "$line" >> "$updated"
mv -f "$updated" "$config"
if ! /usr/bin/crontab "$config" || ! /etc/init.d/crond.sh restart; then
    cp -p "$previous" "$config"
    /usr/bin/crontab "$config"
    /etc/init.d/crond.sh restart
    exit 1
fi
printf 'Watchdog scheduled. Prior cron configuration saved at %s\n' "$previous"
