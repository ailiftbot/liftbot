#!/usr/bin/env bash
# Run ON the VPS (147.93.31.196) as root:
#   curl -fsSL … | bash
# or copy this file and: bash setup-vps-nginx.sh
#
# Proxies http://liftbot.brandinglift.com → 127.0.0.1:8001 (LiftBot gunicorn)

set -euo pipefail

DOMAIN="${DOMAIN:-liftbot.brandinglift.com}"
BACKEND="${BACKEND:-127.0.0.1:8001}"
CONF_DIR=""

if [[ -d /etc/nginx/conf.d ]]; then
  CONF_DIR=/etc/nginx/conf.d
elif [[ -d /etc/nginx/sites-available ]]; then
  CONF_DIR=/etc/nginx/sites-available
else
  echo "nginx config directory not found" >&2
  exit 1
fi

CONF_FILE="${CONF_DIR}/liftbot.conf"

cat > "$CONF_FILE" <<EOF
server {
    listen 80;
    server_name ${DOMAIN};

    client_max_body_size 25M;

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

if [[ -d /etc/nginx/sites-enabled ]]; then
  ln -sfn "$CONF_FILE" /etc/nginx/sites-enabled/liftbot.conf
fi

# Remove default welcome site if it steals all Hosts
rm -f /etc/nginx/conf.d/default.conf 2>/dev/null || true
rm -f /etc/nginx/sites-enabled/default 2>/dev/null || true

nginx -t
systemctl reload nginx || nginx -s reload

echo "OK — http://${DOMAIN}/ should proxy to ${BACKEND}"
echo "Also set on the LiftBot .env:"
echo "  DJANGO_ALLOWED_HOSTS=...,${DOMAIN}"
echo "  PUBLIC_APP_URL=http://${DOMAIN}"
echo "  PUBLIC_WIDGET_URL=http://${DOMAIN}/static/widget.js"
echo "  PUBLIC_WIDGET_API_URL=http://${DOMAIN}/api/widget"
echo "Then recreate/restart the backend container."
