#!/usr/bin/env bash
# sudo bash install.sh  -- installs the fallback AP service. The AP password is read from ./ap-password (created by
# the setup, mode 600) into /etc/wifi-fallback-ap.conf (root-only), and ./ap-password is then deleted.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo "run with sudo"; exit 1; }
cd "$(dirname "$0")"

install -m 755 -o root -g root wifi-fallback-ap /usr/local/sbin/wifi-fallback-ap
install -m 644 -o root -g root wifi-fallback-ap.service /etc/systemd/system/wifi-fallback-ap.service

if [ -f ap-password ]; then
    umask 077
    printf 'AP_PASSWORD=%q\n' "$(cat ap-password)" > /etc/wifi-fallback-ap.conf
    chmod 600 /etc/wifi-fallback-ap.conf
    shred -u ap-password 2>/dev/null || rm -f ap-password
fi
[ -f /etc/wifi-fallback-ap.conf ] || { echo "no /etc/wifi-fallback-ap.conf with AP_PASSWORD"; exit 1; }

systemctl daemon-reload
systemctl enable --now wifi-fallback-ap.service
sleep 2
systemctl --no-pager --lines=5 status wifi-fallback-ap.service || true
/usr/local/sbin/wifi-fallback-ap --status
