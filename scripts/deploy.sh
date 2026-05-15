#!/bin/bash
# Deploy navvix (backend/app/ + Postgres) to navvis.wavelync.com
set -e

REMOTE_HOST="185.229.226.37"
REMOTE_USER="root"
SSH_KEY="$HOME/.ssh/id_ed25519"
APP_DIR="/opt/navvix"
DOMAIN="navvis.wavelync.com"
PORT=8020

ssh_run() { ssh -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new "$REMOTE_USER@$REMOTE_HOST" "$@"; }
scp_r()   { scp -i "$SSH_KEY" -o StrictHostKeyChecking=accept-new -r "$1" "$REMOTE_USER@$REMOTE_HOST:$2"; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=============================="
echo "  navvix deployment"
echo "  layout: backend/app/, Postgres"
echo "  → $DOMAIN  ($REMOTE_HOST:$PORT)"
echo "=============================="

# ── 1. Build frontend ─────────────────────────────────────────────────────
echo ""
echo "[1/7] Building frontend..."
cd "$REPO_ROOT/frontend"
npm install --silent
npm run build
echo "      ✓ Build complete (dist/)"
cd "$REPO_ROOT"

# ── 2. Provision server directories + clean obsolete versioned dirs ──────
echo ""
echo "[2/7] Provisioning server..."
ssh_run "mkdir -p $APP_DIR/frontend/dist"
ssh_run "rm -rf $APP_DIR/navvix_v11 $APP_DIR/navvix_v12 $APP_DIR/navvix_v13 $APP_DIR/navvix_v17 $APP_DIR/navvix_v18 $APP_DIR/navvix"

# ── 3. Postgres install + database/user setup ────────────────────────────
echo ""
echo "[3/7] Postgres setup..."
ssh_run '
  if ! dpkg -s postgresql >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get update -qq
    DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql postgresql-contrib
  fi
  systemctl enable --now postgresql >/dev/null
  sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='\''navvix'\''" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE DATABASE navvix;"
  sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='\''navvix'\''" | grep -q 1 \
    || sudo -u postgres psql -c "CREATE USER navvix WITH ENCRYPTED PASSWORD '\''navvix'\'';"
  sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE navvix TO navvix;" >/dev/null
  sudo -u postgres psql -d navvix -c "GRANT ALL ON SCHEMA public TO navvix;" >/dev/null
  echo "      ✓ postgresql ready (db=navvix, user=navvix)"
'

# ── 4. Upload Python code ────────────────────────────────────────────────
echo ""
echo "[4/7] Uploading Python code..."
ssh_run "rm -rf $APP_DIR/backend/app $APP_DIR/backend/main.py $APP_DIR/backend/requirements.txt $APP_DIR/scripts"
ssh_run "mkdir -p $APP_DIR/backend"
scp_r "backend/app"              "$APP_DIR/backend/"
scp_r "backend/main.py"          "$APP_DIR/backend/main.py"
scp_r "backend/requirements.txt" "$APP_DIR/backend/requirements.txt"
scp_r "scripts"                  "$APP_DIR/"
if [ -d samples ]; then
  ssh_run "rm -rf $APP_DIR/samples && mkdir -p $APP_DIR/samples"
  scp_r "samples/."             "$APP_DIR/samples/"
fi
ssh_run "mkdir -p $APP_DIR/backend/storage $APP_DIR/backend/training_files"
echo "      ✓ Code uploaded"

# ── 5. venv + dependencies ───────────────────────────────────────────────
echo ""
echo "[5/7] Python venv + deps..."
ssh_run "
  cd $APP_DIR
  if [ ! -d venv ]; then
    python3 -m venv venv
  fi
  venv/bin/pip install --quiet --upgrade pip
  venv/bin/pip install --quiet -r backend/requirements.txt
  echo '      ✓ deps installed'
"

# ── 6. Upload frontend dist ──────────────────────────────────────────────
echo ""
echo "[6/7] Uploading frontend..."
ssh_run "rm -rf $APP_DIR/frontend/dist && mkdir -p $APP_DIR/frontend/dist"
scp_r "frontend/dist/." "$APP_DIR/frontend/dist/"
echo "      ✓ Frontend uploaded"

# ── 7. systemd + nginx + cert ────────────────────────────────────────────
echo ""
echo "[7/7] Configuring systemd + nginx..."

ssh_run "cat > /etc/systemd/system/navvix-backend.service << 'SVCEOF'
[Unit]
Description=navvix FastAPI Backend (app.main)
After=network.target postgresql.service
Requires=postgresql.service

[Service]
Type=exec
User=root
Group=root
WorkingDirectory=$APP_DIR/backend
Environment=DATABASE_URL=postgresql+psycopg2://navvix:navvix@localhost:5432/navvix
Environment=STORAGE_DIR=$APP_DIR/backend/storage
Environment=PYTHONUNBUFFERED=1
ExecStart=$APP_DIR/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port $PORT --workers 1 --no-access-log
Restart=always
RestartSec=5
StartLimitInterval=60
StartLimitBurst=3

[Install]
WantedBy=multi-user.target
SVCEOF"

# nginx config — idempotent
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

ssh_run "ln -sf /etc/nginx/sites-available/$DOMAIN /etc/nginx/sites-enabled/$DOMAIN"
ssh_run "nginx -t"
ssh_run "systemctl reload nginx"
ssh_run "certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email admin@wavelync.com --redirect 2>&1 | tail -3"

ssh_run "systemctl daemon-reload && systemctl enable navvix-backend && systemctl restart navvix-backend"
ssh_run "sleep 3 && systemctl is-active navvix-backend && echo '      ✓ backend running on port $PORT'"

echo ""
echo "=============================="
echo "  Deployment complete!"
echo "  https://$DOMAIN"
echo "=============================="
