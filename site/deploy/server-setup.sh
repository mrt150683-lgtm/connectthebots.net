#!/usr/bin/env bash
# Connect the Bots: set up or update the static site on an Ubuntu 22.04/24.04 VPS.
# Run as root on the server, from an unpacked copy of site/ (deploy.sh does this for you).
#
#   sudo bash server-setup.sh --email you@example.com            # full setup with HTTPS
#   sudo bash server-setup.sh --email you@example.com --skip-tls # before DNS points here
#   --no-firewall leaves ufw alone (for a provider-managed firewall)
#
# Safe to run again: it updates files and configuration in place.
set -euo pipefail

DOMAIN="connectthebots.net"
EMAIL=""
SKIP_TLS=0
FIREWALL=1
WITH_APP=0
ENVIRONMENT="production"
START_APP=1
WEBROOT="/var/www/connectthebots"
STATSROOT="/var/www/connectthebots-stats"
LOG="/var/log/nginx/connectthebots.access.log"
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --email|--domain|--environment)
      [[ $# -ge 2 ]] || { echo "Missing option value" >&2; exit 2; }
      case "$1" in --email) EMAIL="$2";; --domain) DOMAIN="$2";; --environment) ENVIRONMENT="$2";; esac
      shift 2 ;;
    --skip-tls) SKIP_TLS=1; shift ;;
    --no-firewall) FIREWALL=0; shift ;;
    --with-app) WITH_APP=1; shift ;;
    --no-start-app) START_APP=0; shift ;;
    -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$DOMAIN" =~ ^[a-z0-9]([a-z0-9.-]*[a-z0-9])?$ && "$DOMAIN" != *..* ]] || die "invalid domain."
[[ -z "$EMAIL" || "$EMAIL" =~ ^[a-zA-Z0-9._+%-]+@[a-zA-Z0-9.-]+$ ]] || die "invalid certificate email."
[[ "$ENVIRONMENT" == production || "$ENVIRONMENT" == staging ]] || die "invalid environment."
[[ $WITH_APP -eq 0 || $SKIP_TLS -eq 0 || "$ENVIRONMENT" == staging ]] || die "HTTP app deployment requires --environment staging."
[[ $EUID -eq 0 ]] || die "run as root (sudo)."
grep -q '^ID=ubuntu' /etc/os-release || die "this script targets Ubuntu."
[[ -f "$SRC/index.html" ]] || die "site files not found next to this script ($SRC)."
[[ $SKIP_TLS -eq 1 || -n "$EMAIL" ]] || die "--email is required for Let's Encrypt (or pass --skip-tls)."
for target in "$WEBROOT" "$STATSROOT"; do
  [[ ! -L "$target" ]] || die "refusing an unexpected web-root symlink."
  [[ ! -e "$target" || -d "$target" ]] || die "web root is not a directory."
done

say "Installing packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q nginx goaccess ufw unattended-upgrades apache2-utils rsync curl
[[ $SKIP_TLS -eq 1 ]] || apt-get install -y -q certbot python3-certbot-nginx

if [[ $WITH_APP -eq 1 ]]; then
  say "Installing the hosted application"
  APP_ARGS=(--domain "$DOMAIN" --environment "$ENVIRONMENT")
  [[ $SKIP_TLS -eq 0 ]] || APP_ARGS+=(--skip-tls)
  [[ $START_APP -eq 1 ]] || APP_ARGS+=(--no-start)
  bash "$SRC/deploy/app-setup.sh" "${APP_ARGS[@]}"
fi

say "Automatic security updates"
dpkg-reconfigure -f noninteractive unattended-upgrades >/dev/null

if [[ $FIREWALL -eq 1 ]]; then
  say "Firewall: SSH, HTTP and HTTPS only"
  ufw allow OpenSSH >/dev/null
  ufw allow 'Nginx Full' >/dev/null
  ufw --force enable >/dev/null
fi

say "Publishing site to $WEBROOT"
mkdir -p "$WEBROOT" "$STATSROOT"
rsync -a --delete --exclude deploy/ --exclude tools/ --exclude README.md "$SRC/" "$WEBROOT/"
chown -R root:root "$WEBROOT"; chmod -R a+rX "$WEBROOT"

say "Password for the private stats page"
if [[ ! -f /etc/nginx/connectthebots.htpasswd ]]; then
  STATS_PASS="$(head -c 18 /dev/urandom | base64 | tr -d '/+=')"
  printf '%s\n' "$STATS_PASS" | htpasswd -ic /etc/nginx/connectthebots.htpasswd stats >/dev/null
  chmod 640 /etc/nginx/connectthebots.htpasswd; chown root:www-data /etc/nginx/connectthebots.htpasswd
  unset STATS_PASS
  echo "  Initial random password set. Choose your password with: sudo htpasswd /etc/nginx/connectthebots.htpasswd stats"
else
  echo "  kept existing password file"
fi

say "nginx configuration"
cat > /etc/nginx/conf.d/connectthebots-log.conf <<'NGINX'
# Visitor IPs are shortened before they are written: the last IPv4 octet / last 80 IPv6 bits are dropped.
map $remote_addr $ctb_anon_ip {
  ~(?P<ip>\d+\.\d+\.\d+)\.\d+$  $ip.0;
  ~(?P<ip>[^:]+:[^:]+:[^:]+):   $ip::;
  default                       0.0.0.0;
}
# Keep COMBINED compatibility without retaining queries, unknown paths, referrers
# or user-agent strings. Even a static request can carry personal data there.
map $uri $ctb_log_path {
  default /other;
  / /;
  /index.html /;
  /signin /signin;
  /signin.html /signin;
  /app /example;
  /example /example;
  /dashboard.html /example;
  /about.html /about;
  /what.html /what;
  /film.html /film;
  ~^/email/ /email/;
  ~^/assets/ /assets/;
  /v/play /video-play;
  /v/sound /video-sound;
  /v/done /video-done;
}
log_format ctb_anon '$ctb_anon_ip - - [$time_local] "$request_method $ctb_log_path $server_protocol" $status $body_bytes_sent "-" "-"';
NGINX

# Keep dynamic routing opt-in until the whole hosted release is approved.
ROUTES='  location = /signin { try_files /signin.html =404; }
  location = /app { try_files /dashboard.html =404; }'
if [[ $WITH_APP -eq 1 ]]; then
  ROUTES='  location = /signin.html { return 302 /signin; }
  location = /.well-known/security.txt {
    proxy_pass http://127.0.0.1:8100;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header Forwarded "";
    client_max_body_size 64k;
    proxy_connect_timeout 5s;
    proxy_read_timeout 30s;
    access_log off;
    error_log /dev/null;
  }
  location ~ ^/(auth(/|$)|app(/|$)|api(/|$)|email(/|$)|board(/|$|\.json$)|goals(/|$)|healthz$|privacy$|terms$|contact$|signin$|leaderboard$|r/) {
    proxy_pass http://127.0.0.1:8100;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header Forwarded "";
    client_max_body_size 64k;
    proxy_connect_timeout 5s;
    proxy_read_timeout 30s;
    access_log off;
    error_log /dev/null;
  }'
fi

# The site's server body, shared by the plain HTTP and the HTTPS configuration.
IFS= read -r -d '' BODY <<NGINX || true
  root $WEBROOT;
  index index.html;
  access_log $LOG ctb_anon;
  error_log /dev/null;
  server_tokens off;
  charset utf-8;
  client_max_body_size 64k;

  gzip on;
  gzip_types text/css application/javascript image/svg+xml application/json;

  add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'" always;
  add_header X-Content-Type-Options "nosniff" always;
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;
  add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), interest-cohort=()" always;
  add_header Cross-Origin-Opener-Policy "same-origin" always;

$ROUTES
  location = /example { try_files /dashboard.html =404; }
  # Film play counter: the page posts an empty request when a film starts,
  # gets sound or finishes. Logged like any visit (shortened address, no
  # cookies or identifiers) and counted by connectthebots_server.traffic.
  location = /v/play { return 204; }
  location = /v/sound { return 204; }
  location = /v/done { return 204; }
  location / { try_files \$uri \$uri/ =404; }

  location ~ \.md\$ {
    default_type text/markdown;
    charset utf-8;
    charset_types text/markdown;
    add_header Content-Disposition "attachment" always;
    add_header X-Content-Type-Options "nosniff" always;
  }

  location ~ \.vtt\$ {
    default_type text/vtt;
    charset utf-8;
    add_header X-Content-Type-Options "nosniff" always;
  }

  location /assets/ {
    expires 7d;
    add_header Cache-Control "public" always;
    add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; media-src 'self'; frame-ancestors 'none'" always;
    add_header X-Content-Type-Options "nosniff" always;
  }

  location /stats/ {
    alias $STATSROOT/;
    auth_basic "Connect the Bots stats";
    auth_basic_user_file /etc/nginx/connectthebots.htpasswd;
    access_log off;
    add_header Cache-Control "no-store" always;
  }
NGINX

# We write the whole nginx site ourselves and certbot only obtains certificates
# (certonly). Letting certbot edit this file clashed with our www redirect and
# made it roll back HTTPS. www always goes to the apex so hosted routes keep one
# cookie and Origin authority, including POSTs.
CERTDIR="/etc/letsencrypt/live/$DOMAIN"
write_site() {
  if [[ $SKIP_TLS -eq 0 && -f "$CERTDIR/fullchain.pem" && -f "$CERTDIR/privkey.pem" ]]; then
    local tls="  ssl_certificate $CERTDIR/fullchain.pem;
  ssl_certificate_key $CERTDIR/privkey.pem;"
    if [[ -f /etc/letsencrypt/options-ssl-nginx.conf ]]; then
      tls+="
  include /etc/letsencrypt/options-ssl-nginx.conf;"
    else
      tls+="
  ssl_protocols TLSv1.2 TLSv1.3;
  ssl_session_cache shared:ctb_ssl:10m;"
    fi
    [[ ! -f /etc/letsencrypt/ssl-dhparams.pem ]] || tls+="
  ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;"
    cat > /etc/nginx/sites-available/connectthebots <<NGINX
server {
  listen 80;
  listen [::]:80;
  server_name $DOMAIN www.$DOMAIN;
  server_tokens off;
  access_log off;
  return 308 https://$DOMAIN\$request_uri;
}

server {
  listen 443 ssl;
  listen [::]:443 ssl;
  server_name www.$DOMAIN;
  server_tokens off;
  access_log off;
$tls
  add_header Strict-Transport-Security "max-age=31536000" always;
  return 308 https://$DOMAIN\$request_uri;
}

server {
  listen 443 ssl;
  listen [::]:443 ssl;
  server_name $DOMAIN;
$tls
  add_header Strict-Transport-Security "max-age=31536000" always;
$BODY
}
NGINX
  else
    cat > /etc/nginx/sites-available/connectthebots <<NGINX
server {
  listen 80;
  listen [::]:80;
  server_name $DOMAIN www.$DOMAIN;
  if (\$host = www.$DOMAIN) { return 308 \$scheme://$DOMAIN\$request_uri; }
$BODY
}
NGINX
  fi
  ln -sf /etc/nginx/sites-available/connectthebots /etc/nginx/sites-enabled/connectthebots
  rm -f /etc/nginx/sites-enabled/default
  nginx -t
}
write_site
systemctl enable --now nginx >/dev/null
systemctl reload nginx

say "Log retention: 30 days"
cat > /etc/logrotate.d/connectthebots <<LOGROTATE
$LOG {
  daily
  rotate 30
  missingok
  notifempty
  compress
  delaycompress
  create 0640 www-data adm
  sharedscripts
  postrotate
    [ -s /run/nginx.pid ] && kill -USR1 \$(cat /run/nginx.pid)
  endscript
}
LOGROTATE

say "Visit statistics (GoAccess, rebuilt every 10 minutes)"
cat > /usr/local/bin/connectthebots-stats <<STATS
#!/bin/sh
# Builds the private visit report from the anonymised nginx log. No cookies, no tracking script.
touch $LOG
zcat -f $LOG* | goaccess - --log-format=COMBINED --ignore-crawlers --anonymize-ip \\
  --html-report-title="Connect the Bots visits" -o $STATSROOT/index.html >/dev/null 2>&1
STATS
chmod 755 /usr/local/bin/connectthebots-stats
cat > /etc/systemd/system/connectthebots-stats.service <<'UNIT'
[Unit]
Description=Build Connect the Bots visit report
[Service]
Type=oneshot
ExecStart=/usr/local/bin/connectthebots-stats
UNIT
cat > /etc/systemd/system/connectthebots-stats.timer <<'UNIT'
[Unit]
Description=Rebuild Connect the Bots visit report every 10 minutes
[Timer]
OnBootSec=2min
OnUnitActiveSec=10min
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now connectthebots-stats.timer >/dev/null
/usr/local/bin/connectthebots-stats || true

if [[ $SKIP_TLS -eq 0 ]]; then
  say "HTTPS certificate for $DOMAIN and www.$DOMAIN"
  MYIP="$(curl -fsS4 https://api.ipify.org || true)"
  DNSIP="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}')"
  if [[ -n "$MYIP" && "$MYIP" != "$DNSIP" ]]; then
    die "$DOMAIN resolves to '${DNSIP:-nothing}', but this server is $MYIP. Point the A records for $DOMAIN and www at $MYIP, wait for DNS, then rerun."
  fi
  # Let's Encrypt prefers IPv6: an AAAA record left at a registrar's parking page makes validation fail.
  for name in "$DOMAIN" "www.$DOMAIN"; do
    for v6 in $(getent ahostsv6 "$name" | awk '{print $1}' | grep -v '^::ffff:' | sort -u); do
      ip -6 addr show | grep -q "inet6 $v6/" || die "$name has an AAAA record ($v6) that is not this server. Delete it (or point it here), wait for DNS, then rerun."
    done
  done
  # Obtain or keep the certificate without letting certbot edit the site.
  certbot certonly --nginx -d "$DOMAIN" -d "www.$DOMAIN" --agree-tos -m "$EMAIL" --non-interactive --keep-until-expiring
  # Older setups installed with certbot --nginx; renewals must not edit the site either.
  RENEWAL="/etc/letsencrypt/renewal/$DOMAIN.conf"
  [[ ! -f "$RENEWAL" ]] || sed -i '/^installer *=/d' "$RENEWAL"
  write_site
  systemctl reload nginx
fi

say "Done"
echo "  Site:  http$([[ $SKIP_TLS -eq 0 ]] && echo s)://$DOMAIN/"
echo "  Stats: http$([[ $SKIP_TLS -eq 0 ]] && echo s)://$DOMAIN/stats/  (user: stats)"
