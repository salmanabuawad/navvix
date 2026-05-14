#!/bin/bash
# Deploy navvix to navvis.wavelync.com
set -e

REMOTE_HOST="185.229.226.37"
REMOTE_USER="root"
SSH_KEY="$HOME/.ssh/id_ed25519"
APP_DIR="/opt/navvix"
DOMAIN="navvis.wavelync.com"
PORT=8020

ssh_run() { ssh -i "$SSH_KEY" "$REMOTE_USER@$REMOTE_HOST" "$@"; }
scp_r()   { scp -i "$SSH_KEY" -r "$1" "$REMOTE_USER@$REMOTE_HOST:$2"; }

echo "=============================="
echo "  navvix Deployment"
echo "  → $DOMAIN  ($REMOTE_HOST:$PORT)"
echo "=============================="

# ── 1. Build frontend ─────────────────────────────────────────────────────
echo ""
echo "[1/6] Building frontend..."
cd "$(dirname "$0")/../frontend"
npm install --silent
npm run build
echo "      ✓ Build complete (dist/)"
cd "C:/KortexProjects/navvix"

# ── 2. Provision server directories ──────────────────────────────────────
echo ""
echo "[2/6] Provisioning server directories..."
ssh_run "mkdir -p $APP_DIR/backend $APP_DIR/navvix_v11 $APP_DIR/navvix_v12 $APP_DIR/navvix_v13 $APP_DIR/frontend/dist"

# ── 3. Upload Python code ─────────────────────────────────────────────────
echo ""
echo "[3/6] Uploading Python modules..."
scp_r "backend/main.py"          "$APP_DIR/backend/main.py"
scp_r "backend/requirements.txt" "$APP_DIR/backend/requirements.txt"
scp_r "navvix_v12/"              "$APP_DIR/"
scp_r "navvix_v13/"              "$APP_DIR/"

# Create storage + training_files dirs on server
ssh_run "mkdir -p $APP_DIR/backend/storage $APP_DIR/backend/training_files"
echo "      ✓ Python files uploaded"

# ── 4. Set up venv + install deps ────────────────────────────────────────
echo ""
echo "[4/6] Setting up Python venv..."
ssh_run "
  cd $APP_DIR
  if [ ! -d venv ]; then
    python3 -m venv venv
    echo '      venv created'
  fi
  venv/bin/pip install --quiet --upgrade pip
  venv/bin/pip install --quiet -r backend/requirements.txt
  echo '      deps installed'
"
echo "      ✓ venv ready"

# ── 5. Upload frontend dist ───────────────────────────────────────────────
echo ""
echo "[5/6] Uploading frontend..."
ssh_run "rm -rf $APP_DIR/frontend/dist && mkdir -p $APP_DIR/frontend/dist"
scp_r "frontend/dist/." "$APP_DIR/frontend/dist/"
echo "      ✓ Frontend uploaded"

# ── 6. Nginx + SSL + systemd ──────────────────────────────────────────────
echo ""
echo "[6/6] Configuring nginx, SSL, and systemd..."

# Write nginx config (HTTP only first, certbot adds HTTPS)
ssh_run "cat > /etc/nginx/sites-available/$DOMAIN << 'NGINXEOF'
server {
    listen 80;
    server_name $DOMAIN;

    root  $APP_DIR/frontend/dist;
    index index.html;

    client_max_body_size 500M;

    gzip on;
    gzip_vary on;
    gzip_types text/plain text/css application/json application/javascript text/xml image/svg+xml;
    gzip_min_length 1024;

    add_header X-Frame-Options SAMEORIGIN always;
    add_header X-Content-Type-Options nosniff always;

    location /api/ {
        proxy_pass         http://127.0.0.1:$PORT/api/;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
        proxy_request_buffering off;
        client_max_body_size 500M;
    }

    location = /index.html {
        add_header Cache-Control \"no-cache, no-store, must-revalidate\";
        add_header Pragma \"no-cache\";
        try_files \$uri /index.html;
    }

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location ~* \.(js|css)\$ {
        expires 1y;
        add_header Cache-Control \"public, immutable\";
        access_log off;
    }

    location ~* \.(woff2?|ttf|svg|png|jpg|ico|webp|pdf|dxf)\$ {
        expires 30d;
        add_header Cache-Control \"public\";
        access_log off;
    }
}
NGINXEOF"

# Enable site
ssh_run "ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN"

# Test nginx config
ssh_run "nginx -t"

# Reload nginx (HTTP-only first, so certbot can do HTTP-01 challenge)
ssh_run "systemctl reload nginx"

# Obtain SSL certificate
ssh_run "certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@wavelync.com --redirect 2>&1 | tail -5"

# Write systemd service
ssh_run "cat > /etc/systemd/system/navvix-backend.service << 'SVCEOF'
[Unit]
Description=navvix FastAPI Backend
After=network.target

[Service]
Type=exec
User=root
Group=root
WorkingDirectory=$APP_DIR/backend
ExecStart=$APP_DIR/venv/bin/uvicorn main:app --host 127.0.0.1 --port $PORT --workers 1 --no-access-log
Restart=always
RestartSec=5
StartLimitInterval=60
StartLimitBurst=3

[Install]
WantedBy=multi-user.target
SVCEOF"

# Enable + start service
ssh_run "systemctl daemon-reload && systemctl enable navvix-backend && systemctl restart navvix-backend"
ssh_run "sleep 2 && systemctl is-active navvix-backend && echo 'Backend running on port $PORT'"

echo ""
echo "=============================="
echo "  Deployment complete!"
echo "  https://$DOMAIN"
echo "=============================="
