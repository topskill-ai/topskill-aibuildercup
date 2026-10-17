#!/usr/bin/env bash
# One-time, idempotent setup of keyless GitHub Actions deploys for .github/workflows/deploy.yml
# (SPEC 13.4, owner decision D3 revised 2026-10-16). Run it once as the project owner/admin.
#
#   deploy/scripts/setup_ci_deploy.sh            # asks before IAM changes
#   deploy/scripts/setup_ci_deploy.sh --yes      # no prompts
#   deploy/scripts/setup_ci_deploy.sh --dry-run  # print what would run, change nothing
#
# Creates or ensures:
#   - API sts.googleapis.com (token exchange; iamcredentials is enabled by provision.sh)
#   - Workload Identity pool `github` + OIDC provider `topskill-repo` that only accepts tokens of
#     .github/workflows/deploy.yml running on refs/heads/main of this repository (pinned by id)
#   - service account gh-deployer@, impersonable only through that provider
#   - gh-deployer roles: Cloud Build submit (+ source bucket, actAs the build SA), Firebase Hosting,
#     Firestore rules + indexes, and Viewer (stream build logs, read Cloud Run / index state)
# Caller needs roles/iam.workloadIdentityPoolAdmin besides its usual IAM admin roles; grant it with
#   gcloud projects add-iam-policy-binding top-skill --member=user:<you> \
#     --role=roles/iam.workloadIdentityPoolAdmin --condition=None
# No service account key is created. deploy.yml has the provider and account names built in.
set -euo pipefail

PROJECT="${PROJECT:-top-skill}"
REPO="${REPO:-topskill-ai/topskill}"
REPO_ID="${REPO_ID:-1420086285}"  # gh api repos/topskill-ai/topskill --jq .id (survives renames and transfers)
POOL="${POOL:-github}"
PROVIDER="${PROVIDER:-topskill-repo}"
DEPLOYER_SA_NAME="${DEPLOYER_SA_NAME:-gh-deployer}"
DEPLOYER_SA="${DEPLOYER_SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

ASSUME_YES=0
DRY_RUN=0
for arg in "$@"; do
  case "${arg}" in
    --yes|-y) ASSUME_YES=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    *) echo "unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
ok() { printf '    \033[32mok\033[0m %s\n' "$*"; }

run() {
  printf '    $ %s\n' "$*"
  if [[ "${DRY_RUN}" == 1 ]]; then return 0; fi
  "$@"
}

confirm() {
  if [[ "${ASSUME_YES}" == 1 || "${DRY_RUN}" == 1 ]]; then return 0; fi
  local reply
  read -r -p "    $1 [y/N] " reply
  [[ "${reply}" == "y" || "${reply}" == "Y" ]]
}

bind_project() {
  local member="$1" role="$2"
  run gcloud projects add-iam-policy-binding "${PROJECT}" --member="${member}" --role="${role}" \
    --condition=None --quiet --format=none
}

log "Project ${PROJECT}, repository ${REPO} (id ${REPO_ID})"
PROJECT_NUMBER="$(gcloud projects describe "${PROJECT}" --format='value(projectNumber)')"

log "APIs"
run gcloud services enable --project "${PROJECT}" sts.googleapis.com iamcredentials.googleapis.com

log "Workload Identity pool ${POOL}"
if gcloud iam workload-identity-pools describe "${POOL}" --location=global --project "${PROJECT}" \
  >/dev/null 2>&1; then
  ok "exists"
else
  run gcloud iam workload-identity-pools create "${POOL}" --location=global --project "${PROJECT}" \
    --display-name="GitHub Actions" || {
    echo "    creating the pool failed; the caller needs roles/iam.workloadIdentityPoolAdmin (see --help)" >&2
    exit 1
  }
fi

# Only the deploy workflow on main of this exact repository may exchange its GitHub token. The repository
# is matched by id, not name (it moved from topskillopen/ to topskill-ai/), so workflow_ref is matched
# by its path suffix.
CONDITION="assertion.repository_id == '${REPO_ID}' && assertion.ref == 'refs/heads/main'"
CONDITION+=" && assertion.workflow_ref.endsWith('/.github/workflows/deploy.yml@refs/heads/main')"
PROVIDER_ARGS=(--location=global --workload-identity-pool="${POOL}" --project "${PROJECT}"
  --display-name="${REPO}"
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.repository_id=assertion.repository_id,attribute.ref=assertion.ref,attribute.workflow_ref=assertion.workflow_ref"
  --attribute-condition="${CONDITION}")
log "OIDC provider ${PROVIDER}"
if gcloud iam workload-identity-pools providers describe "${PROVIDER}" --location=global \
  --workload-identity-pool="${POOL}" --project "${PROJECT}" >/dev/null 2>&1; then
  run gcloud iam workload-identity-pools providers update-oidc "${PROVIDER}" "${PROVIDER_ARGS[@]}"
else
  run gcloud iam workload-identity-pools providers create-oidc "${PROVIDER}" "${PROVIDER_ARGS[@]}" \
    --issuer-uri="https://token.actions.githubusercontent.com"
fi

log "Deployer service account ${DEPLOYER_SA}"
if gcloud iam service-accounts describe "${DEPLOYER_SA}" --project "${PROJECT}" >/dev/null 2>&1; then
  ok "exists"
else
  run gcloud iam service-accounts create "${DEPLOYER_SA_NAME}" --project "${PROJECT}" \
    --display-name "GitHub Actions deployer (deploy.yml)"
  # New service accounts take a few seconds to become visible to IAM.
  [[ "${DRY_RUN}" == 1 ]] || sleep 10
fi

BUILD_SA="$(gcloud builds get-default-service-account --project "${PROJECT}" --format='value(serviceAccountEmail)' 2>/dev/null || true)"
BUILD_SA="${BUILD_SA##*/}"
[[ -n "${BUILD_SA}" ]] || BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

log "IAM: ${DEPLOYER_SA}"
echo "    Cloud Build default service account: ${BUILD_SA}"
if confirm "Let ${REPO} impersonate ${DEPLOYER_SA} and grant it the deploy roles?"; then
  run gcloud iam service-accounts add-iam-policy-binding "${DEPLOYER_SA}" --project "${PROJECT}" \
    --role=roles/iam.workloadIdentityUser --format=none \
    --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/attribute.repository_id/${REPO_ID}"
  # deploy_backend.sh: gcloud builds submit (streaming logs from the default logs bucket needs
  # Viewer), Firestore index state, Cloud Run revision check. deploy_web.sh: Hosting release.
  # deploy.yml firestore step: rules + indexes.
  for role in roles/cloudbuild.builds.editor roles/viewer roles/serviceusage.serviceUsageConsumer \
    roles/firebasehosting.admin roles/firebaserules.admin roles/datastore.indexAdmin; do
    bind_project "serviceAccount:${DEPLOYER_SA}" "${role}"
  done
  # Source tarballs go to gs://<project>_cloudbuild (created by the first laptop build).
  run gcloud storage buckets add-iam-policy-binding "gs://${PROJECT}_cloudbuild" \
    --member="serviceAccount:${DEPLOYER_SA}" --role=roles/storage.admin --format=none
  # The build runs as the Cloud Build default service account, so the submitter must act as it.
  run gcloud iam service-accounts add-iam-policy-binding "${BUILD_SA}" --project "${PROJECT}" \
    --member="serviceAccount:${DEPLOYER_SA}" --role=roles/iam.serviceAccountUser --format=none
fi

log "Done. deploy.yml authenticates with"
echo "    workload_identity_provider: projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL}/providers/${PROVIDER}"
echo "    service_account:            ${DEPLOYER_SA}"
echo "    Optional: gh secret set SMOKE_EMAIL / SMOKE_PASSWORD -R ${REPO} for the signed-in smoke test."
