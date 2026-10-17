#!/usr/bin/env bash
# Build the backend image in Cloud Build and deploy it to Cloud Run (SPEC 13.2).
#
#   deploy/scripts/deploy_backend.sh                        # build + deploy HEAD, then verify it serves
#   deploy/scripts/deploy_backend.sh --smoke                # ... and run smoke_prod.sh afterwards
#   deploy/scripts/deploy_backend.sh --smoke --allow-missing # walking skeleton (404 = router not merged)
#   deploy/scripts/deploy_backend.sh --skip-index-wait      # do not wait for Firestore indexes
#
# Refuses to run with a dirty working tree: the image tag is the git sha, so what is deployed must
# be exactly what is committed (untracked files count: they would enter the Cloud Build context).
# Run it from a dedicated clean worktree, not a checkout where other work writes files:
#   git worktree add --detach ../topskill.worktrees/deploy-prod main   # once
#   git -C ../topskill.worktrees/deploy-prod checkout --detach <sha>    # each deploy
# Before building it waits (up to INDEX_WAIT_S, default 900 s) until every Firestore composite and
# single-field index is READY: a new revision's startup knowledge sweep queries the knowledge.status
# collection-group index and is skipped if that index is still building. Deploy indexes first.
# Uses the caller's gcloud login: the owner's on a laptop, gh-deployer@ (WIF) in .github/workflows/deploy.yml.
set -euo pipefail

PROJECT="${PROJECT:-top-skill}"
REGION="${REGION:-asia-southeast1}"
SERVICE="${SERVICE:-topskill-api}"
INDEX_WAIT_S="${INDEX_WAIT_S:-900}"
RUN_SMOKE=0
WAIT_INDEXES=1
SMOKE_ARGS=()
for arg in "$@"; do
  case "${arg}" in
    --smoke) RUN_SMOKE=1 ;;
    --allow-missing) SMOKE_ARGS+=(--allow-missing) ;;
    --skip-index-wait) WAIT_INDEXES=0 ;;
    -h|--help) sed -n '2,19p' "$0"; exit 0 ;;
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

# Prints the number of Firestore indexes (composite + single-field) that are not READY yet.
indexes_not_ready() {
  local composite fields
  composite="$(gcloud firestore indexes composite list --project "${PROJECT}" --format=json)"
  fields="$(gcloud firestore indexes fields list --project "${PROJECT}" --format=json)"
  COMPOSITE="${composite}" FIELDS="${fields}" python3 -c '
import json, os
states = [i.get("state") for i in json.loads(os.environ["COMPOSITE"] or "[]")]
for f in json.loads(os.environ["FIELDS"] or "[]"):
    states += [i.get("state") for i in (f.get("indexConfig") or {}).get("indexes", [])]
print(sum(1 for s in states if s != "READY"))
'
}
if [[ "${WAIT_INDEXES}" == 1 ]]; then
  waited=0
  while :; do
    pending="$(indexes_not_ready)"
    [[ "${pending}" == 0 ]] && { echo "==> Firestore indexes: all READY"; break; }
    if (( waited >= INDEX_WAIT_S )); then
      echo "deploy_backend: ${pending} Firestore index(es) still building after ${INDEX_WAIT_S}s;" \
        "retry later or pass --skip-index-wait" >&2
      exit 1
    fi
    echo "    ${pending} Firestore index(es) still building; waiting..."
    sleep 30; waited=$((waited + 30))
  done
fi

SHA="$(git rev-parse --short HEAD)"
echo "==> Cloud Build: api:${SHA} -> Cloud Run ${SERVICE} (${PROJECT}/${REGION})"
gcloud builds submit . --config deploy/cloudbuild.yaml --substitutions "_SHA=${SHA}" \
  --project "${PROJECT}" --region "${REGION}"

# The serving revision must run the image we just built, take all traffic and be Ready (readiness is
# gated by the startup probe on /healthz inside the container).
read -r URL image ready traffic < <(gcloud run services describe "${SERVICE}" --project "${PROJECT}" \
  --region "${REGION}" --format=json | python3 -c '
import json, sys
s = json.load(sys.stdin)
ready = next((c["status"] for c in s["status"].get("conditions", []) if c["type"] == "Ready"), "Unknown")
latest = sum(t.get("percent", 0) for t in s["status"].get("traffic", []) if t.get("latestRevision"))
print(s["status"]["url"], s["spec"]["template"]["spec"]["containers"][0]["image"], ready, latest)
')
echo "==> ${URL} serving ${image} (Ready=${ready}, latest revision traffic ${traffic}%)"
if [[ "${image}" != *":${SHA}" || "${ready}" != "True" || "${traffic}" != 100 ]]; then
  echo "deploy_backend: Cloud Run is not serving api:${SHA}" >&2
  exit 1
fi

# Cloud Run's front end reserves URL paths ending in "z", so /healthz answers 404 from outside (it only
# works for the in-container startup probe). Externally we use the /health alias when the app has it.
health="$(curl -fsS --max-time 20 "${URL}/health" 2>/dev/null || true)"
if [[ -n "${health}" ]]; then
  echo "==> ${URL}/health: ${health}"
  if [[ "${health}" != *"\"${SHA}\""* ]]; then
    echo "deploy_backend: /health does not report version ${SHA}" >&2
    exit 1
  fi
else
  echo "    (no /health alias in this build; readiness verified by the startup probe on /healthz)"
fi
meta_status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 20 "${URL}/api/public/meta")"
echo "==> ${URL}/api/public/meta: HTTP ${meta_status}"
[[ "${meta_status}" == 200 ]] || { echo "deploy_backend: /api/public/meta failed" >&2; exit 1; }

if [[ "${RUN_SMOKE}" == 1 ]]; then
  "${ROOT}/deploy/scripts/smoke_prod.sh" --base "${URL}" ${SMOKE_ARGS[@]+"${SMOKE_ARGS[@]}"}
fi
echo "==> deployed ${SHA}"
