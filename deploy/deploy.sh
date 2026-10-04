#!/usr/bin/env bash
# Zero-touch deploy, run ON the VPS (GitHub Actions pipes it over SSH, or run by hand):
#   APP_DIR=/opt/liftbot bash deploy/deploy.sh
#
# Pulls master, rebuilds and restarts the stack, waits for the app to answer,
# and rolls back to the previous commit if it doesn't come up healthy.
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/liftbot}"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8001/}"
BRANCH="${BRANCH:-master}"
DEPLOY_SHA="${DEPLOY_SHA:-}"

cd "$APP_DIR"
compose() { docker compose -f "$COMPOSE_FILE" "$@"; }

if [[ ! -f .env ]]; then
  echo "ERROR: $APP_DIR/.env is missing — copy .env.example and fill it in first." >&2
  exit 1
fi
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "ERROR: $APP_DIR has uncommitted changes to tracked files; refusing to deploy over them:" >&2
  git status --short --untracked-files=no >&2
  exit 1
fi

PREVIOUS_SHA="$(git rev-parse HEAD)"
echo "==> Current: $PREVIOUS_SHA"

git fetch --quiet origin "$BRANCH"
TARGET_SHA="${DEPLOY_SHA:-$(git rev-parse "origin/$BRANCH")}"
git checkout --quiet "$BRANCH"
git merge --ff-only --quiet "$TARGET_SHA"
echo "==> Deploying: $(git log -1 --format='%h %s')"

wait_healthy() {
  for _ in $(seq 1 40); do
    code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 5 "$HEALTH_URL" || true)"
    if [[ "$code" =~ ^(200|301|302)$ ]] && compose exec -T rag curl -fsS http://127.0.0.1:8100/health >/dev/null 2>&1; then
      return 0
    fi
    sleep 5
  done
  return 1
}

up() {
  compose build --pull
  compose up -d --remove-orphans
}

up
if wait_healthy; then
  echo "==> Healthy at $(git rev-parse --short HEAD)"
  docker image prune -f >/dev/null || true
  exit 0
fi

echo "!!! Health check failed — recent backend logs:" >&2
compose logs --tail=80 backend rag >&2 || true
if [[ "$PREVIOUS_SHA" != "$(git rev-parse HEAD)" ]]; then
  echo "!!! Rolling back to $PREVIOUS_SHA" >&2
  git reset --quiet --hard "$PREVIOUS_SHA"
  up
  wait_healthy && echo "==> Rolled back; previous version is serving." >&2
fi
exit 1
