#!/usr/bin/env bash
# Build the backend image in Cloud Build and deploy it to Cloud Run (SPEC 13.2).
#
#   deploy/scripts/deploy_backend.sh            # build + deploy HEAD, then check /healthz
#   deploy/scripts/deploy_backend.sh --smoke    # ... and run smoke_prod.sh afterwards
#
# Refuses to run with a dirty working tree: the image tag is the git sha, so what is deployed must
# be exactly what is committed. Uses the caller's gcloud login (no WIF, owner decision D3).
set -euo pipefail

PROJECT="${PROJECT:-top-skill}"
REGION="${REGION:-asia-southeast1}"
SERVICE="${SERVICE:-topskill-api}"
RUN_SMOKE=0
for arg in "$@"; do
  case "${arg}" in
    --smoke) RUN_SMOKE=1 ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

ROOT="$(git rev-parse --show-toplevel)"
cd "${ROOT}"

if [[ -n "$(git status --porcelain)" ]]; then
  echo "deploy_backend: working tree is dirty; commit or stash first." >&2
  git status --short >&2
  exit 1
fi
for f in backend/pyproject.toml backend/uv.lock backend/topskill/main.py deploy/env/prod.env.yaml; do
  [[ -f "${f}" ]] || { echo "deploy_backend: missing ${f}" >&2; exit 1; }
done
branch="$(git rev-parse --abbrev-ref HEAD)"
[[ "${branch}" == "main" ]] || echo "deploy_backend: WARNING deploying branch ${branch}, not main" >&2

SHA="$(git rev-parse --short HEAD)"
echo "==> Cloud Build: api:${SHA} -> Cloud Run ${SERVICE} (${PROJECT}/${REGION})"
gcloud builds submit . --config deploy/cloudbuild.yaml --substitutions "_SHA=${SHA}" \
  --project "${PROJECT}" --region "${REGION}"

URL="$(gcloud run services describe "${SERVICE}" --project "${PROJECT}" --region "${REGION}" \
  --format='value(status.url)')"
echo "==> ${URL}/healthz"
health="$(curl -fsS --max-time 20 "${URL}/healthz")"
echo "    ${health}"
if [[ "${health}" != *"\"${SHA}\""* ]]; then
  echo "deploy_backend: /healthz does not report version ${SHA}" >&2
  exit 1
fi

if [[ "${RUN_SMOKE}" == 1 ]]; then
  "${ROOT}/deploy/scripts/smoke_prod.sh" --base "${URL}"
fi
echo "==> deployed ${SHA}"
