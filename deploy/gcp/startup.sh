#!/bin/bash
# KinKudos family edition on a Google Cloud free-tier e2-micro (Debian 12).
# Runs at every boot as the VM startup script: adds swap, installs Docker,
# and starts KinKudos + scheduler + Caddy (automatic HTTPS on <ip>.sslip.io).
set -euo pipefail
exec >>/var/log/kinkudos-startup.log 2>&1
echo "=== KinKudos startup $(date -Is)"

MD=http://metadata.google.internal/computeMetadata/v1
md() { curl -fsS -H 'Metadata-Flavor: Google' "$MD/$1"; }
IP=$(md instance/network-interfaces/0/access-configs/0/external-ip)
HOST="${IP//./-}.sslip.io"
TOKEN=$(md instance/attributes/kinkudos-setup-token)

# The e2-micro has 1 GB of memory; add 1 GB of swap as a safety margin.
if [ ! -f /swapfile ]; then
  fallocate -l 1G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
fi
swapon /swapfile 2>/dev/null || true

if ! command -v docker >/dev/null; then
  curl -fsSL https://get.docker.com | sh
fi

mkdir -p /opt/kinkudos
cat > /opt/kinkudos/compose.yaml <<EOF
x-kinkudos-environment: &kinkudos-environment
  KINKUDOS_DEBUG: "false"
  KINKUDOS_DATABASE_PATH: /app/data/kinkudos.sqlite3
  KINKUDOS_MEDIA_ROOT: /app/data/media
  KINKUDOS_RUNTIME_SECRETS_DIR: /app/data/.runtime-secrets
  KINKUDOS_SECRET_KEY_FILE: /app/data/.runtime-secrets/django_secret_key
  KINKUDOS_SETUP_TOKEN: "${TOKEN}"
  KINKUDOS_VAPID_PRIVATE_KEY_FILE: /app/data/.runtime-secrets/vapid_private.pem
  KINKUDOS_VAPID_PUBLIC_KEY_FILE: /app/data/.runtime-secrets/vapid_public.txt
  KINKUDOS_VAPID_SUBJECT: "https://${HOST}"
  KINKUDOS_ALLOWED_HOSTS: "${HOST},127.0.0.1,localhost"
  KINKUDOS_CSRF_TRUSTED_ORIGINS: "https://${HOST}"
  KINKUDOS_EMAIL_ENABLED: "false"
  KINKUDOS_SECURE_COOKIES: "true"
  KINKUDOS_SECURE_SSL_REDIRECT: "true"
  KINKUDOS_HSTS_SECONDS: "31536000"
  KINKUDOS_LOG_LEVEL: INFO
  KINKUDOS_DEFAULT_LANGUAGE: en
  KINKUDOS_DEVICE_PAIRING_REQUIRED: "true"
  KINKUDOS_TRUSTED_PROXIES: "172.16.0.0/12"

services:
  app:
    image: ghcr.io/onepersonstartup901/kinkudos-family:latest
    restart: unless-stopped
    environment: *kinkudos-environment
    volumes:
      - kinkudos-data:/app/data
    healthcheck:
      test:
        - CMD
        - python
        - -c
        - "import urllib.request; r=urllib.request.Request('http://127.0.0.1:8000/health/', headers={'X-Forwarded-Proto':'https'}); urllib.request.urlopen(r, timeout=3)"
      interval: 30s
      timeout: 5s
      retries: 3
      start_period: 60s

  scheduler:
    image: ghcr.io/onepersonstartup901/kinkudos-family:latest
    restart: unless-stopped
    depends_on:
      app:
        condition: service_healthy
    entrypoint: ["python", "manage.py", "run_scheduler"]
    command: []
    environment: *kinkudos-environment
    volumes:
      - kinkudos-data:/app/data

  caddy:
    image: caddy:2
    restart: unless-stopped
    depends_on:
      - app
    ports:
      - "80:80"
      - "443:443"
    command: caddy reverse-proxy --from ${HOST} --to app:8000
    volumes:
      - caddy-data:/data

volumes:
  kinkudos-data:
  caddy-data:
EOF

cd /opt/kinkudos
docker compose pull
docker compose up -d
echo "KinKudos is starting at https://${HOST}/"
