#!/usr/bin/env bash
# Deploy iKids Park na Fly.io (wymaga: fly auth login + karta w billing na weryfikację free).
set -euo pipefail
export PATH="${HOME}/.fly/bin:${PATH}"
cd "$(dirname "$0")/.."

APP="${FLY_APP:-ikidspark}"

if [[ ! -f .env ]]; then
  echo "Brak .env — skopiuj .env.example i ustaw DATABASE_URL"
  exit 1
fi

# shellcheck disable=SC1091
set -a && source .env && set +a

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "Brak DATABASE_URL w .env"
  exit 1
fi

if ! fly apps list 2>/dev/null | grep -qw "$APP"; then
  echo "Tworzę aplikację $APP..."
  fly apps create "$APP" || true
fi

echo "Ustawiam secret DATABASE_URL..."
fly secrets set "DATABASE_URL=${DATABASE_URL}" -a "$APP"

echo "Deploy..."
fly deploy -a "$APP"

echo
fly status -a "$APP"
echo
echo "URL: https://${APP}.fly.dev"
