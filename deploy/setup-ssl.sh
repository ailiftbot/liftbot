#!/usr/bin/env bash
# Free Let's Encrypt SSL for LiftBot
# Run ON the VPS as root:
#   bash deploy/setup-ssl.sh
#
# Requires: nginx already proxying http://DOMAIN → 127.0.0.1:8001
# DNS A record for DOMAIN must point to this server.

set -euo pipefail

DOMAIN="${DOMAIN:-liftbot.app}"
BACKEND="${BACKEND:-127.0.0.1:8001}"
EMAIL="${EMAIL:-admin@${DOMAIN}}"
APP_DIR="${APP_DIR:-}"

echo "==> Domain: ${DOMAIN}"
echo "==> Backend: ${BACKEND}"
echo "==> Email: ${EMAIL}"

# --- install certbot ---
if ! command -v certbot >/dev/null 2>&1; then
  if command -v dnf >/dev/null 2>&1; then
    dnf install -y certbot python3-certbot-nginx
  elif command -v yum >/dev/null 2>&1; then
    yum install -y certbot python3-certbot-nginx
  elif command -v apt-get >/dev/null 2>&1; then
    apt-get update -y
    apt-get install -y certbot python3-certbot-nginx
  else
    echo "Install certbot manually, then re-run." >&2
    exit 1
  fi
fi

# --- ensure HTTP vhost exists (needed for HTTP-01 challenge) ---
if [[ -d /etc/nginx/conf.d ]]; then
  CONF=/etc/nginx/conf.d/liftbot.conf
elif [[ -d /etc/nginx/sites-available ]]; then
  CONF=/etc/nginx/sites-available/liftbot.conf
  ln -sfn "$CONF" /etc/nginx/sites-enabled/liftbot.conf
else
  echo "nginx config dir not found" >&2
  exit 1
fi

cat > "$CONF" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    client_max_body_size 25M;

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        proxy_pass http://${BACKEND};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120s;
        proxy_buffering off;
    }
}
EOF

mkdir -p /var/www/certbot
rm -f /etc/nginx/conf.d/default.conf 2>/dev/null || true
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

nginx -t
systemctl reload nginx || nginx -s reload

# --- obtain certificate ---
certbot certonly --webroot \
  -w /var/www/certbot \
  -d "${DOMAIN}" \
  --email "${EMAIL}" \
  --agree-tos \
  --non-interactive \
  --keep-until-expiring

# --- write HTTPS + HTTP→HTTPS redirect ---
cat > "$CONF" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    location /.well-known/acme-challenge/ {
        root /var/www/certbot;
    }

    location / {
        return 301 https://\$host\$request_uri;
    }
}

server {
    listen 443 ssl http2;
    server_name ${DOMAIN};

    ssl_certificate     /etc/letsencrypt/live/${DOMAIN}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${DOMAIN}/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    client_max_body_size 25M;

    location / {
        proxy_pass http://${BACKEND};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
        proxy_buffering off;
    }
}
EOF

# ssl-dhparams may be missing on some installs — create if needed
if [[ ! -f /etc/letsencrypt/ssl-dhparams.pem ]]; then
  if [[ -f /usr/lib/python3*/site-packages/certbot/ssl-dhparams.pem ]]; then
    cp /usr/lib/python3*/site-packages/certbot/ssl-dhparams.pem /etc/letsencrypt/ssl-dhparams.pem
  else
    openssl dhparam -out /etc/letsencrypt/ssl-dhparams.pem 2048
  fi
fi

# options-ssl-nginx.conf
if [[ ! -f /etc/letsencrypt/options-ssl-nginx.conf ]]; then
  cat > /etc/letsencrypt/options-ssl-nginx.conf <<'SSLOPT'
ssl_session_cache shared:le_nginx_SSL:10m;
ssl_session_timeout 1440m;
ssl_session_tickets off;
ssl_protocols TLSv1.2 TLSv1.3;
ssl_prefer_server_ciphers off;
SSLOPT
fi

nginx -t
systemctl reload nginx || nginx -s reload

# --- renew timer ---
systemctl enable --now certbot-renew.timer 2>/dev/null || \
  systemctl enable --now certbot.timer 2>/dev/null || \
  (grep -q 'certbot renew' /etc/crontab 2>/dev/null || echo '0 3 * * * root certbot renew --quiet --deploy-hook "nginx -s reload"' >> /etc/crontab)

# --- update LiftBot .env to https if APP_DIR known or discoverable ---
if [[ -z "$APP_DIR" ]]; then
  for candidate in /root/liftbot /home/*/liftbot /opt/liftbot /var/www/liftbot; do
    if [[ -f "${candidate}/.env" ]]; then
      APP_DIR="$candidate"
      break
    fi
  done
fi

if [[ -n "${APP_DIR}" && -f "${APP_DIR}/.env" ]]; then
  echo "==> Updating ${APP_DIR}/.env for HTTPS"
  sed -i "s|^DJANGO_CSRF_TRUSTED_ORIGINS=.*|DJANGO_CSRF_TRUSTED_ORIGINS=https://${DOMAIN},http://${DOMAIN}|" "${APP_DIR}/.env"
  sed -i "s|^PUBLIC_APP_URL=.*|PUBLIC_APP_URL=https://${DOMAIN}|" "${APP_DIR}/.env"
  sed -i "s|^PUBLIC_WIDGET_URL=.*|PUBLIC_WIDGET_URL=https://${DOMAIN}/static/widget.js|" "${APP_DIR}/.env"
  sed -i "s|^PUBLIC_WIDGET_API_URL=.*|PUBLIC_WIDGET_API_URL=https://${DOMAIN}/api/widget|" "${APP_DIR}/.env"
  if grep -q '^DJANGO_ALLOWED_HOSTS=' "${APP_DIR}/.env"; then
    if ! grep -q "${DOMAIN}" "${APP_DIR}/.env"; then
      sed -i "s|^DJANGO_ALLOWED_HOSTS=.*|&,${DOMAIN}|" "${APP_DIR}/.env"
    fi
  fi
  if [[ -f "${APP_DIR}/docker-compose.yml" ]]; then
    (cd "${APP_DIR}" && docker compose up -d --force-recreate backend) || true
  fi
  grep -E 'ALLOWED_HOSTS|CSRF|PUBLIC_' "${APP_DIR}/.env"
else
  echo "==> Update LiftBot .env manually:"
  echo "  PUBLIC_APP_URL=https://${DOMAIN}"
  echo "  PUBLIC_WIDGET_URL=https://${DOMAIN}/static/widget.js"
  echo "  PUBLIC_WIDGET_API_URL=https://${DOMAIN}/api/widget"
  echo "  DJANGO_CSRF_TRUSTED_ORIGINS=https://${DOMAIN},http://${DOMAIN}"
fi

echo ""
echo "Done. Open: https://${DOMAIN}/"
echo "Renewal is automatic via certbot timer/cron."
