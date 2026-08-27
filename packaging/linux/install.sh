#!/usr/bin/env sh
set -eu

if [ "$(id -u)" -ne 0 ]; then
  echo "install.sh must run as root" >&2
  exit 1
fi

PACKAGE_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
VERSION=$(python3 -c "import json; print(json.load(open('$PACKAGE_ROOT/payload/version.json', encoding='utf-8'))['version'])")
INSTALL_ROOT=/opt/local-drama-studio
INSTANCE_ROOT=/var/lib/local-drama-studio
CONFIG_ROOT=/etc/local-drama-studio

if systemctl is-active --quiet local-drama-studio.service 2>/dev/null; then
  systemctl stop local-drama-studio.service
fi

if ! getent group localdrama >/dev/null 2>&1; then
  groupadd --system localdrama
fi
if ! id localdrama >/dev/null 2>&1; then
  useradd --system --gid localdrama --home-dir "$INSTANCE_ROOT" --shell /usr/sbin/nologin localdrama
fi

install -d -m 0755 "$INSTALL_ROOT/host" "$INSTALL_ROOT/versions/$VERSION"
install -d -o localdrama -g localdrama -m 0750 "$INSTANCE_ROOT" "$INSTANCE_ROOT/data" "$INSTANCE_ROOT/projects" "$INSTANCE_ROOT/backups" "$INSTANCE_ROOT/logs" "$INSTANCE_ROOT/runtime"
install -d -m 0750 "$CONFIG_ROOT"
if [ ! -e "$INSTALL_ROOT/versions/$VERSION/release-manifest.json" ]; then
  cp -a "$PACKAGE_ROOT/payload/." "$INSTALL_ROOT/versions/$VERSION/"
fi
install -m 0755 "$PACKAGE_ROOT/host/local-drama-host" "$INSTALL_ROOT/host/local-drama-host"
if [ ! -e "$INSTALL_ROOT/active-release.json" ]; then
  install -m 0644 "$PACKAGE_ROOT/active-release.json" "$INSTALL_ROOT/active-release.json"
fi
if [ ! -e "$CONFIG_ROOT/config.json" ]; then
  install -o root -g localdrama -m 0640 "$PACKAGE_ROOT/config.server.json" "$CONFIG_ROOT/config.json"
fi
install -m 0644 "$PACKAGE_ROOT/local-drama-studio.service" /etc/systemd/system/local-drama-studio.service
chown -R root:root "$INSTALL_ROOT"
LOCAL_DRAMA_INSTALL_ROOT="$INSTALL_ROOT" \
LOCAL_DRAMA_INSTANCE_ROOT="$INSTANCE_ROOT" \
LOCAL_DRAMA_CONFIG="$CONFIG_ROOT/config.json" \
  "$INSTALL_ROOT/host/local-drama-host" upgrade --bundle "$PACKAGE_ROOT/payload"
chown -R localdrama:localdrama "$INSTANCE_ROOT"
systemctl daemon-reload
systemctl enable local-drama-studio.service
systemctl restart local-drama-studio.service
echo "Local Drama Studio $VERSION installed; persistent data is in $INSTANCE_ROOT"
