#!/usr/bin/env bash
# Installs NOTORYX Ops as a service on Raspberry Pi OS or other Debian-based Linux.
# Run from the folder that holds the notoryx_ops wheel:  sudo ./install.sh
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run this with sudo." >&2
  exit 1
fi
WHEEL=$(ls notoryx_ops-*.whl 2>/dev/null | head -1 || true)
if [ -z "$WHEEL" ]; then
  echo "Can't find notoryx_ops-*.whl in this folder." >&2
  exit 1
fi

USER_NAME=notoryx-ops
APP_DIR=/opt/notoryx-ops
CONF_DIR=/etc/notoryx-ops
DATA_DIR=/var/lib/notoryx-ops

apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip bluez >/dev/null

id "$USER_NAME" >/dev/null 2>&1 || useradd --system --home "$DATA_DIR" --shell /usr/sbin/nologin "$USER_NAME"
usermod -aG dialout,bluetooth "$USER_NAME" || true
mkdir -p "$APP_DIR" "$CONF_DIR" "$DATA_DIR"

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install --quiet --upgrade pip
"$APP_DIR/venv/bin/pip" install --quiet "$WHEEL"

if [ ! -f "$CONF_DIR/config.toml" ]; then
  NOTORYX_OPS_CONFIG="$CONF_DIR/config.toml" "$APP_DIR/venv/bin/notoryx-ops" init
  sed -i "s#^data_dir = .*#data_dir = \"$DATA_DIR\"#" "$CONF_DIR/config.toml"
  echo "Wrote $CONF_DIR/config.toml. Edit it to set your radio, then: sudo systemctl restart notoryx-ops"
fi
chown -R "$USER_NAME" "$DATA_DIR" "$CONF_DIR"
chmod 600 "$CONF_DIR/config.toml"

cat > /etc/systemd/system/notoryx-ops.service <<UNIT
[Unit]
Description=NOTORYX Ops station
After=network-online.target bluetooth.target
Wants=network-online.target

[Service]
User=$USER_NAME
Environment=NOTORYX_OPS_CONFIG=$CONF_DIR/config.toml
ExecStart=$APP_DIR/venv/bin/notoryx-ops run
Restart=on-failure
RestartSec=10
NoNewPrivileges=true
ProtectSystem=full
ProtectHome=true

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable --now notoryx-ops
echo "NOTORYX Ops is running. Open http://$(hostname -I | awk '{print $1}'):8484 after setting host = \"0.0.0.0\" and an admin_token in $CONF_DIR/config.toml,"
echo "or http://127.0.0.1:8484 on the Pi itself."
