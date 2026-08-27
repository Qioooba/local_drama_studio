#!/usr/bin/env sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "uninstall.sh must run as root" >&2
  exit 1
fi

systemctl disable --now local-drama-studio.service 2>/dev/null || true
rm -f /etc/systemd/system/local-drama-studio.service
systemctl daemon-reload
rm -rf -- /opt/local-drama-studio
echo "Service and application binaries removed. /etc/local-drama-studio and /var/lib/local-drama-studio were preserved intentionally."
