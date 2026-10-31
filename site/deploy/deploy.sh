#!/usr/bin/env bash
# Upload a site release over SSH. No production deploy without owner approval.
# Static: deploy.sh user@host --email operator@example.test --dry-run
# App: deploy.sh user@host --with-app --wheel dist/agentmatch-0.1.0-py3-none-any.whl
#      --environment staging --domain staging.example.test --skip-tls --no-firewall
set -euo pipefail
HOST="${1:-}"
[[ "$HOST" =~ ^([a-zA-Z0-9_][a-zA-Z0-9_.-]*@)?[a-zA-Z0-9]([a-zA-Z0-9.-]*[a-zA-Z0-9])?$ ]] || { echo "Supply an SSH hostname, optionally user@hostname" >&2; exit 2; }
shift
DRY=0
WITH_APP=0
WHEEL=""
ENVIRONMENT="production"
DOMAIN="connectthebots.net"
EMAIL=""
SKIP_TLS=0
FIREWALL=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --email|--domain|--environment|--wheel)
      [[ $# -ge 2 ]] || { echo "Missing option value" >&2; exit 2; }
      case "$1" in --email) EMAIL="$2";; --domain) DOMAIN="$2";; --environment) ENVIRONMENT="$2";; --wheel) WHEEL="$2";; esac
      shift 2 ;;
    --dry-run) DRY=1; shift ;;
    --with-app) WITH_APP=1; shift ;;
    --skip-tls) SKIP_TLS=1; shift ;;
    --no-firewall) FIREWALL=0; shift ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
 done
[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" != *..* ]] || { echo "Invalid domain" >&2; exit 2; }
[[ -z "$EMAIL" || "$EMAIL" =~ ^[a-zA-Z0-9._+%-]+@[a-zA-Z0-9.-]+$ ]] || { echo "Invalid certificate email" >&2; exit 2; }
[[ "$ENVIRONMENT" == production || "$ENVIRONMENT" == staging ]] || { echo "Invalid environment" >&2; exit 2; }
[[ $SKIP_TLS -eq 1 || -n "$EMAIL" || $DRY -eq 1 ]] || { echo "--email is needed for HTTPS" >&2; exit 2; }
[[ $WITH_APP -eq 0 || $SKIP_TLS -eq 0 || "$ENVIRONMENT" == staging ]] || { echo "HTTP app deployment is staging-only" >&2; exit 2; }
SITE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ROOT="$(cd "$SITE/.." && pwd)"
for file in index.html signin.html dashboard.html assets/site.css deploy/server-setup.sh; do
  [[ -f "$SITE/$file" ]] || { echo "Missing site file: $file" >&2; exit 1; }
done
SETUP_ARGS=(--domain "$DOMAIN" --environment "$ENVIRONMENT")
[[ -z "$EMAIL" ]] || SETUP_ARGS+=(--email "$EMAIL")
[[ $SKIP_TLS -eq 0 ]] || SETUP_ARGS+=(--skip-tls)
[[ $FIREWALL -eq 1 ]] || SETUP_ARGS+=(--no-firewall)
if [[ $WITH_APP -eq 1 ]]; then
  [[ -f "$WHEEL" && "$(basename "$WHEEL")" =~ ^agentmatch-[A-Za-z0-9_.+]+-py3-none-any\.whl$ ]] || { echo "--with-app requires --wheel pointing to a built agentmatch wheel" >&2; exit 2; }
  [[ -f "$ROOT/requirements-server.lock" ]] || { echo "Missing requirements-server.lock" >&2; exit 1; }
  SETUP_ARGS+=(--with-app)
fi
bash -n "$SITE/deploy/server-setup.sh"
bash -n "$SITE/deploy/app-setup.sh"
if [[ $DRY -eq 1 ]]; then
  printf 'Dry run. Site: %s\nTarget: %s\n' "$SITE" "$HOST"
  if [[ $WITH_APP -eq 1 ]]; then printf 'Wheel: %s\nLock: %s\n' "$WHEEL" "$ROOT/requirements-server.lock"; fi
  printf 'Would upload a fresh release bundle and run server-setup.sh'
  printf ' %q' "${SETUP_ARGS[@]}"
  printf '\nNo files uploaded and no server changes made.\n'
  exit 0
fi
PACK="$(mktemp -d /tmp/ctb-deploy.XXXXXXXX)"
cleanup() { [[ "$PACK" =~ ^/tmp/ctb-deploy\.[a-zA-Z0-9]+$ ]] && rm -rf -- "$PACK"; }
trap cleanup EXIT
mkdir "$PACK/site"
tar -C "$SITE" --exclude=tools --exclude=.git --exclude='.env*' --exclude=app.env --exclude='*.key' --exclude='*.pem' --exclude='*.db' -cf - . | tar -C "$PACK/site" -xf -
MEMBERS=(site)
if [[ $WITH_APP -eq 1 ]]; then
  mkdir "$PACK/app"
  cp -- "$WHEEL" "$PACK/app/"
  cp -- "$ROOT/requirements-server.lock" "$PACK/app/requirements-server.lock"
  (cd "$PACK/app" && sha256sum -- "$(basename "$WHEEL")" requirements-server.lock > SHA256SUMS)
  MEMBERS+=(app)
fi
tar -C "$PACK" -czf "$PACK/release.tar.gz" "${MEMBERS[@]}"
STAGE="$(ssh -- "$HOST" 'mktemp -d /tmp/connectthebots-deploy.XXXXXXXX')"
[[ "$STAGE" =~ ^/tmp/connectthebots-deploy\.[a-zA-Z0-9]+$ ]] || { echo "Server returned an unexpected staging path" >&2; exit 1; }
scp -- "$PACK/release.tar.gz" "$HOST:$STAGE/release.tar.gz"
# Variable remote arguments have the strict character sets validated above.
# The unique directory is retained for inspection. No recursive remote deletion.
REMOTE="tar -xzf $STAGE/release.tar.gz -C $STAGE && sudo bash $STAGE/site/deploy/server-setup.sh ${SETUP_ARGS[*]}"
ssh -t -- "$HOST" "$REMOTE"
