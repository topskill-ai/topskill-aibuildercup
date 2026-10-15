#!/usr/bin/env bash
# Build the web app with production settings and deploy it to Firebase Hosting (SPEC 13.3).
#
#   deploy/scripts/deploy_web.sh                    # live site https://top-skill.web.app
#   deploy/scripts/deploy_web.sh --channel pr-12    # preview channel (expires in 7 days)
#   deploy/scripts/deploy_web.sh --with-firestore   # also deploy firestore rules + indexes
#   deploy/scripts/deploy_web.sh --skip-build       # deploy the existing web/dist
#
# Production VITE_* values (SPEC 4.3) are exported here; process env wins over any web/.env* file,
# so a local dev .env cannot leak into the production bundle. Set VITE_GOOGLE_SIGNIN=1 once the
# Google provider is enabled in the Firebase console (owner decision D4).
set -euo pipefail

PROJECT="${PROJECT:-top-skill}"
CHANNEL=""
WITH_FIRESTORE=0
SKIP_BUILD=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --channel) CHANNEL="${2:?--channel needs a name}"; shift 2 ;;
    --with-firestore) WITH_FIRESTORE=1; shift ;;
    --skip-build) SKIP_BUILD=1; shift ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel)"
cd "${ROOT}"
[[ -f web/package.json ]] || { echo "deploy_web: web/package.json not found" >&2; exit 1; }

export VITE_API_BASE="${VITE_API_BASE:-https://topskill-api-920441209758.asia-southeast1.run.app}"
export VITE_FIREBASE_API_KEY="${VITE_FIREBASE_API_KEY:-AIzaSyBINjnUKFWfLiSPREGBXUFx0xX8Z9-BHA0}"
export VITE_FIREBASE_AUTH_DOMAIN="${VITE_FIREBASE_AUTH_DOMAIN:-top-skill.firebaseapp.com}"
export VITE_FIREBASE_PROJECT_ID="top-skill"
export VITE_FIREBASE_APP_ID="${VITE_FIREBASE_APP_ID:-1:920441209758:web:3e9d36df86419f88748c8f}"
export VITE_USE_AUTH_EMULATOR=0
export VITE_DEV_AUTH=0
export VITE_GOOGLE_SIGNIN="${VITE_GOOGLE_SIGNIN:-0}"
VITE_APP_VERSION="$(git rev-parse --short HEAD)"
export VITE_APP_VERSION

if [[ "${SKIP_BUILD}" != 1 ]]; then
  echo "==> npm ci && npm run build (API ${VITE_API_BASE})"
  (cd web && npm ci --no-audit --no-fund && npm run build)
fi
[[ -f web/dist/index.html ]] || { echo "deploy_web: web/dist/index.html missing" >&2; exit 1; }

# Guard: a production bundle must not point at the local dev backend.
if grep -rqE '(127\.0\.0\.1|localhost):8000' web/dist; then
  echo "deploy_web: web/dist references the local dev backend; refusing to deploy" >&2
  exit 1
fi

if [[ -n "${CHANNEL}" ]]; then
  echo "==> Hosting preview channel ${CHANNEL}"
  firebase hosting:channel:deploy "${CHANNEL}" --expires 7d --project "${PROJECT}" --non-interactive
else
  targets="hosting"
  [[ "${WITH_FIRESTORE}" == 1 ]] && targets="hosting,firestore:rules,firestore:indexes"
  echo "==> firebase deploy --only ${targets}"
  firebase deploy --only "${targets}" --project "${PROJECT}" --non-interactive
  echo "==> https://${PROJECT}.web.app"
fi
