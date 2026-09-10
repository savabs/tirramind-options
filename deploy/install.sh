#!/usr/bin/env bash
# Install or update the fit service on a fresh Debian or Ubuntu box.
#
#   ./deploy/install.sh user@host
#
# Idempotent: safe to run again to deploy a new revision. It never writes a
# secret and never opens a port to the world; the service binds loopback and
# something else terminates TLS in front of it.
set -euo pipefail

TARGET="${1:?usage: install.sh user@host}"
REPO="${TMO_REPO:-https://github.com/savabs/tirramind-options.git}"

ssh "$TARGET" REPO="$REPO" bash -s <<'REMOTE'
set -euo pipefail

apt-get update -qq
apt-get install -y -qq python3-venv python3-pip git

id -u tmo >/dev/null 2>&1 || useradd --system --home /opt/tmo --shell /usr/sbin/nologin tmo

if [ -d /opt/tmo/.git ]; then
  git -C /opt/tmo fetch --quiet origin main
  git -C /opt/tmo reset --hard --quiet origin/main
else
  git clone --quiet "$REPO" /opt/tmo
fi

[ -d /opt/tmo/.venv ] || python3 -m venv /opt/tmo/.venv
/opt/tmo/.venv/bin/pip install --quiet --upgrade pip
/opt/tmo/.venv/bin/pip install --quiet -r /opt/tmo/requirements.txt

chown -R tmo:tmo /opt/tmo

install -m 644 /opt/tmo/deploy/tmo-fit.service /etc/systemd/system/tmo-fit.service
systemctl daemon-reload
systemctl enable --now tmo-fit.service
systemctl restart tmo-fit.service

# Row counts are evidence. Wait for the surface to actually be warm.
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8080/health | grep -q '"warm": *true'; then
    echo "warm after ${i}0s"
    curl -fsS http://127.0.0.1:8080/health
    exit 0
  fi
  sleep 10
done
echo "FAILED: the service never warmed. Last 40 lines:" >&2
journalctl -u tmo-fit.service -n 40 --no-pager >&2
exit 1
REMOTE
