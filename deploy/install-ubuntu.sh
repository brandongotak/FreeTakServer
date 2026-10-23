#!/bin/bash
# Native (non-Docker) FreeTAKServer install for Ubuntu 22.04 / 24.04.
# Run as root from the root of a clone of this repo:
#   sudo bash deploy/install-ubuntu.sh
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo bash deploy/install-ubuntu.sh)" >&2
  exit 1
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR=/opt/freetakserver
DATA_DIR=/opt/fts
ENV_DIR=/etc/freetakserver
ENV_FILE=$ENV_DIR/fts.env

# FTS pins dependencies that only build on Python 3.11, so use deadsnakes
# rather than the distro's 3.12.
echo "==> Installing Python 3.11"
apt-get update
apt-get install -y software-properties-common git curl
add-apt-repository -y ppa:deadsnakes/ppa
apt-get update
apt-get install -y python3.11 python3.11-venv python3.11-dev build-essential libxml2-dev libxslt1-dev

echo "==> Creating freetak user and directories"
id freetak &>/dev/null || useradd --system --create-home --shell /usr/sbin/nologin freetak
mkdir -p "$APP_DIR" "$DATA_DIR" "$ENV_DIR"
rm -rf "$APP_DIR/src"
cp -r "$REPO_DIR" "$APP_DIR/src"
chown -R freetak:freetak "$APP_DIR" "$DATA_DIR"

echo "==> Installing FreeTAKServer into $APP_DIR/venv"
sudo -u freetak python3.11 -m venv "$APP_DIR/venv"
PIP="$APP_DIR/venv/bin/pip"
sudo -u freetak "$PIP" install --upgrade pip setuptools wheel poetry-core
# ruamel.yaml is very ornery and has to be force-reinstalled alone
sudo -u freetak "$PIP" install --force-reinstall "ruamel.yaml<0.18"
sudo -u freetak "$PIP" install --no-build-isolation "$APP_DIR/src"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "==> Writing $ENV_FILE"
  # DigitalOcean metadata service first, then the first non-loopback address
  PUBLIC_IP=$(curl -fs --max-time 2 http://169.254.169.254/metadata/v1/interfaces/public/0/ipv4/address || true)
  [[ -z "$PUBLIC_IP" ]] && PUBLIC_IP=$(hostname -I | awk '{print $1}')
  gen() { python3.11 -c "import secrets; print(secrets.token_urlsafe(24))"; }
  sed -e "s|__PUBLIC_IP__|$PUBLIC_IP|g" \
      -e "s|__SECRET_KEY__|$(gen)|" \
      -e "s|__FED_PASSWORD__|$(gen)|" \
      -e "s|__CLIENT_CERT_PASSWORD__|$(gen)|" \
      -e "s|__WEBSOCKET_KEY__|$(gen)|" \
      "$REPO_DIR/deploy/fts.env.example" > "$ENV_FILE"
  chown root:freetak "$ENV_FILE"
  chmod 640 "$ENV_FILE"
else
  echo "==> Keeping existing $ENV_FILE"
fi

# Without FTSConfig.yaml + installation.json, FTS starts the interactive wizard,
# which would hang under systemd. Generate the defaults headlessly instead.
if [[ ! -f "$DATA_DIR/FTSConfig.yaml" ]]; then
  echo "==> Generating $DATA_DIR/FTSConfig.yaml"
  sudo -u freetak bash -c "set -a; source '$ENV_FILE'; set +a; cd '$DATA_DIR'; \
    '$APP_DIR/venv/bin/python' -c 'from FreeTAKServer.core.configuration.configuration_wizard import autogenerate_config; autogenerate_config()'"
fi

echo "==> Installing systemd service"
cp "$REPO_DIR/deploy/freetakserver.service" /etc/systemd/system/freetakserver.service
systemctl daemon-reload
systemctl enable --now freetakserver

cat <<EOF

FreeTAKServer is installed and running.
  Status:  systemctl status freetakserver
  Logs:    journalctl -u freetakserver -f
  Config:  $ENV_FILE (env vars) and $DATA_DIR/FTSConfig.yaml
  Data:    $DATA_DIR (certs, database, data packages) - back this up

Firewall ports to open: 8089 (TLS CoT), 8080 and 8443 (data packages),
8087 only if you need plain TCP CoT. Keep 19023 (REST API) closed.
EOF
