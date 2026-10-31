#!/usr/bin/env bash
# Install a verified wheel into an immutable release; run only on Ubuntu 24.04.
# Called by server-setup.sh --with-app. Does not configure SSH, DNS or providers.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BUNDLE="$(cd "$HERE/../.." && pwd)/app"
DOMAIN="connectthebots.net"
ENVIRONMENT="production"
SKIP_TLS=0
START=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --domain|--environment|--bundle)
      [[ $# -ge 2 ]] || { echo "Missing option value" >&2; exit 2; }
      case "$1" in --domain) DOMAIN="$2";; --environment) ENVIRONMENT="$2";; --bundle) BUNDLE="$2";; esac
      shift 2 ;;
    --skip-tls) SKIP_TLS=1; shift ;;
    --no-start) START=0; shift ;;
    *) echo "Unknown app setup option" >&2; exit 2 ;;
  esac
done
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" != *..* ]] || { echo "Invalid domain" >&2; exit 2; }
[[ "$ENVIRONMENT" == production || "$ENVIRONMENT" == staging ]] || { echo "Invalid environment" >&2; exit 2; }
[[ $SKIP_TLS -eq 0 || "$ENVIRONMENT" == staging ]] || { echo "HTTP app setup is staging-only" >&2; exit 2; }
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }
grep -q '^VERSION_ID="24.04"' /etc/os-release || { echo "Application requires Ubuntu 24.04" >&2; exit 1; }
grep -q '^ID=ubuntu' /etc/os-release || { echo "Application requires Ubuntu" >&2; exit 1; }
for target in /var/lib/connectthebots /var/backups/connectthebots /etc/connectthebots /etc/connectthebots/app.env /opt/connectthebots /opt/connectthebots/releases; do
  [[ ! -L "$target" ]] || { echo "Refusing an unexpected application path symlink" >&2; exit 1; }
done
[[ ! -e /etc/connectthebots/app.env || -f /etc/connectthebots/app.env ]] || { echo "app.env must be a regular file" >&2; exit 1; }

mapfile -t VERIFIED < <(python3 "$HERE/verify_bundle.py" "$BUNDLE")
[[ ${#VERIFIED[@]} -eq 2 ]] || { echo "Application bundle verification failed" >&2; exit 1; }
WHEEL="${VERIFIED[0]}"
RELEASE="/opt/connectthebots/releases/${VERIFIED[1]}"
[[ ! -L "$RELEASE" && ! -L "$RELEASE/venv" ]] || { echo "Refusing an unexpected release path symlink" >&2; exit 1; }
BASE_URL="https://$DOMAIN"
[[ $SKIP_TLS -eq 0 ]] || BASE_URL="http://$DOMAIN"

getent group ctb >/dev/null || groupadd --system ctb
id -u ctb >/dev/null 2>&1 || useradd --system --gid ctb --home-dir /var/lib/connectthebots --shell /usr/sbin/nologin ctb
[[ "$(getent passwd ctb | cut -d: -f7)" == /usr/sbin/nologin ]] || { echo "Existing ctb user must have no login shell" >&2; exit 1; }
install -d -o ctb -g ctb -m 0750 /var/lib/connectthebots
if [[ -e /var/backups/connectthebots ]]; then
  [[ -d /var/backups/connectthebots && "$(stat -c %U /var/backups/connectthebots)" == ctb && "$(stat -c %a /var/backups/connectthebots)" == 700 ]] || { echo "Existing backup directory must be a private ctb-owned directory" >&2; exit 1; }
else
  install -d -o ctb -g ctb -m 0700 /var/backups/connectthebots
fi
install -d -o root -g root -m 0750 /etc/connectthebots
umask 077
python3 "$HERE/configure_app.py" --env-file /etc/connectthebots/app.env --environment "$ENVIRONMENT" --base-url "$BASE_URL"
chown root:root /etc/connectthebots/app.env
chmod 0600 /etc/connectthebots/app.env

export DEBIAN_FRONTEND=noninteractive
apt-get install -y -q python3.12-venv ca-certificates
install -d -o root -g root -m 0755 /opt/connectthebots /opt/connectthebots/releases "$RELEASE"
if [[ ! -f "$RELEASE/complete" ]]; then
  # The release identity includes both wheel and dependency-lock hashes.
  # Never install dependencies from an unpinned resolver on the server.
  python3.12 -m venv "$RELEASE/venv"
  "$RELEASE/venv/bin/python" -m pip install --disable-pip-version-check --require-hashes --only-binary=:all: -r "$BUNDLE/requirements-server.lock"
  "$RELEASE/venv/bin/python" -m pip install --disable-pip-version-check --no-deps "$BUNDLE/$WHEEL"
  "$RELEASE/venv/bin/python" -c 'import connectthebots_server, starlette, uvicorn'
  touch "$RELEASE/complete"
  chmod -R a+rX "$RELEASE"
fi
if [[ -e /opt/connectthebots/venv && ! -L /opt/connectthebots/venv ]]; then
  echo "Existing venv is not a managed release link; review before replacing it" >&2; exit 1
fi
# Finish any old worker before switching code/schema. A stopped in-flight SMTP
# attempt is recovered as uncertain, never silently resent by the next release.
for worker in notifications backup certificate; do
  if [[ -f "/etc/systemd/system/connectthebots-$worker.timer" ]]; then
    systemctl stop "connectthebots-$worker.timer" "connectthebots-$worker.service"
  fi
done
ln -sfn "$RELEASE/venv" /opt/connectthebots/venv.next
mv -Tf /opt/connectthebots/venv.next /opt/connectthebots/venv
install -o root -g root -m 0644 "$HERE/connectthebots-app.service" /etc/systemd/system/connectthebots-app.service
install -o root -g root -m 0644 "$HERE/connectthebots-notifications.service" /etc/systemd/system/connectthebots-notifications.service
install -o root -g root -m 0644 "$HERE/connectthebots-notifications.timer" /etc/systemd/system/connectthebots-notifications.timer
for worker in backup certificate; do
  install -o root -g root -m 0644 "$HERE/connectthebots-$worker.service" "/etc/systemd/system/connectthebots-$worker.service"
  install -o root -g root -m 0644 "$HERE/connectthebots-$worker.timer" "/etc/systemd/system/connectthebots-$worker.timer"
done
# The local-only backup unit keeps its network namespace disabled by default.
# A validated all-or-none offsite configuration opts in through one managed,
# credential-free override. Removing configuration removes only that override.
python3 "$HERE/configure_app.py" --env-file /etc/connectthebots/app.env --environment "$ENVIRONMENT" --base-url "$BASE_URL" \
  --backup-network-drop-in /etc/systemd/system/connectthebots-backup.service.d/40-ctb-offsite-network.conf \
  --backup-timer-drop-in /etc/systemd/system/connectthebots-backup.timer.d/40-ctb-heartbeat-schedule.conf
if [[ $START -eq 1 ]]; then
  systemctl daemon-reload
  systemctl enable connectthebots-app.service >/dev/null
  systemctl restart connectthebots-app.service
  healthy=0
  for attempt in $(seq 1 30); do
    if curl --silent --fail --max-time 2 http://127.0.0.1:8100/healthz >/dev/null; then healthy=1; break; fi
    sleep 1
  done
  [[ $healthy -eq 1 ]] || { echo "Application did not become healthy; review systemctl status connectthebots-app" >&2; exit 1; }
  systemctl enable --now connectthebots-notifications.timer connectthebots-backup.timer connectthebots-certificate.timer >/dev/null
fi
echo "Application wheel installed; sign-ups remain closed."
