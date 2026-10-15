#!/usr/bin/env bash
# Local stack: Firestore + Auth emulators (project demo-topskill) + backend (:8000) + web (:5173).
#
#   deploy/scripts/dev_up.sh                  # everything; Ctrl-C stops all of it
#   deploy/scripts/dev_up.sh --no-web         # emulators + backend
#   deploy/scripts/dev_up.sh --emulators-only # emulators only (e.g. for pytest in another shell)
#
# Ports (override with env): FS_PORT=8080 AUTH_PORT=9099 HUB_PORT=4400 LOG_PORT=4500
#                            API_PORT=8000 WEB_PORT=5173
# Backend dev settings are set here explicitly (process env wins over backend/.env):
#   ENV=dev AUTH_DEV_BYPASS=true FIRESTORE_PROJECT=demo-topskill STORAGE_BACKEND=local
#   KNOWLEDGE_BACKEND=${KNOWLEDGE_BACKEND:-local} LLM_MODE=${LLM_MODE:-live}
# Gemini runs on Vertex via your ADC login (gcloud auth application-default login). For
# KNOWLEDGE_BACKEND=file_search put GEMINI_API_KEY into the git-ignored backend/.env, or run with
# DEV_FETCH_GEMINI_KEY=1 to read it from Secret Manager into this process only (never printed).
#
# Uses deploy/scripts/emu_exec.sh, so the emulators live exactly as long as the backend/web.
set -euo pipefail

FS_PORT="${FS_PORT:-8080}"
AUTH_PORT="${AUTH_PORT:-9099}"
HUB_PORT="${HUB_PORT:-4400}"
LOG_PORT="${LOG_PORT:-4500}"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"

RUN_BACKEND=1
RUN_WEB=1
INNER=0
for arg in "$@"; do
  case "${arg}" in
    --no-web) RUN_WEB=0 ;;
    --no-backend) RUN_BACKEND=0 ;;
    --emulators-only) RUN_BACKEND=0; RUN_WEB=0 ;;
    --inner) INNER=1 ;;
    -h|--help) sed -n '2,17p' "$0"; exit 0 ;;
    *) echo "unknown argument: ${arg}" >&2; exit 2 ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"

if [[ "${INNER}" != 1 ]]; then
  # Outer call: start the emulators and re-run this script inside them.
  inner_args=(--inner)
  [[ "${RUN_BACKEND}" == 0 ]] && inner_args+=(--no-backend)
  [[ "${RUN_WEB}" == 0 ]] && inner_args+=(--no-web)
  export EMU_LANE="${EMU_LANE:-dev-up-${FS_PORT}}"
  export FS_PORT AUTH_PORT HUB_PORT LOG_PORT API_PORT WEB_PORT
  exec "${SCRIPT_DIR}/emu_exec.sh" "${FS_PORT}" "${AUTH_PORT}" "${HUB_PORT}" "${LOG_PORT}" -- \
    "${SCRIPT_DIR}/dev_up.sh" "${inner_args[@]}"
fi

# ---- inner: emulators are running; FIRESTORE_EMULATOR_HOST / FIREBASE_AUTH_EMULATOR_HOST are set.
# Local traffic must bypass the corporate proxy; gRPC (Firestore) must not use grpc_proxy here.
export NO_PROXY="127.0.0.1,localhost${NO_PROXY:+,${NO_PROXY}}"
export no_proxy="${NO_PROXY}"
unset grpc_proxy GRPC_PROXY

pids=()
cleanup() {
  for pid in ${pids[@]+"${pids[@]}"}; do kill "${pid}" 2>/dev/null || true; done
  wait 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "dev_up: firestore=${FIRESTORE_EMULATOR_HOST} auth=${FIREBASE_AUTH_EMULATOR_HOST}"

if [[ "${RUN_BACKEND}" == 1 ]]; then
  if [[ -f "${ROOT}/backend/pyproject.toml" ]]; then
    export ENV=dev APP_VERSION=dev AUTH_DEV_BYPASS=true LLM_MODE="${LLM_MODE:-live}"
    export GOOGLE_CLOUD_PROJECT=top-skill GOOGLE_CLOUD_LOCATION=global GOOGLE_GENAI_USE_VERTEXAI=true
    export FIRESTORE_PROJECT=demo-topskill
    export STORAGE_BACKEND=local KNOWLEDGE_BACKEND="${KNOWLEDGE_BACKEND:-local}"
    export PUBLIC_API_BASE="http://127.0.0.1:${API_PORT}" PUBLIC_WEB_BASE="http://localhost:${WEB_PORT}"
    export CORS_ORIGINS="http://localhost:${WEB_PORT},http://127.0.0.1:${WEB_PORT}"
    if [[ "${DEV_FETCH_GEMINI_KEY:-0}" == 1 && -z "${GEMINI_API_KEY:-}" ]]; then
      GEMINI_API_KEY="$(gcloud secrets versions access latest --secret gemini-api-key --project top-skill)"
      export GEMINI_API_KEY
    fi
    echo "dev_up: backend http://127.0.0.1:${API_PORT} (LLM_MODE=${LLM_MODE}, KNOWLEDGE_BACKEND=${KNOWLEDGE_BACKEND})"
    (cd "${ROOT}/backend" && exec uv run uvicorn topskill.main:app --reload --host 127.0.0.1 --port "${API_PORT}") &
    pids+=($!)
  else
    echo "dev_up: backend/ not found, skipping backend" >&2
  fi
fi

if [[ "${RUN_WEB}" == 1 ]]; then
  if [[ -f "${ROOT}/web/package.json" ]]; then
    export VITE_API_BASE="http://127.0.0.1:${API_PORT}"
    export VITE_FIREBASE_PROJECT_ID=demo-topskill VITE_USE_AUTH_EMULATOR=1 VITE_DEV_AUTH=1
    export VITE_AUTH_EMULATOR_URL="http://${FIREBASE_AUTH_EMULATOR_HOST}"
    [[ -d "${ROOT}/web/node_modules" ]] || (cd "${ROOT}/web" && npm ci --no-audit --no-fund)
    echo "dev_up: web http://localhost:${WEB_PORT}"
    (cd "${ROOT}/web" && exec npm run dev -- --port "${WEB_PORT}" --strictPort) &
    pids+=($!)
  else
    echo "dev_up: web/ not found, skipping web" >&2
  fi
fi

[[ ${#pids[@]} -eq 0 ]] && echo "dev_up: emulators running; press Ctrl-C to stop."
# Stop everything as soon as the emulators (firebase) or one of the processes exits
# (bash 3.2 has no `wait -n`). Find the `firebase emulators:exec` ancestor process.
firebase_pid=""
p="${PPID}"
for _ in 1 2 3 4; do
  [[ -n "${p}" && "${p}" != 1 ]] || break
  if ps -o command= -p "${p}" | grep -q 'emulators:exec'; then firebase_pid="${p}"; break; fi
  p="$(ps -o ppid= -p "${p}" | tr -d ' ')"
done
while true; do
  if [[ -n "${firebase_pid}" ]] && ! kill -0 "${firebase_pid}" 2>/dev/null; then
    echo "dev_up: emulators stopped; stopping the stack" >&2
    exit 0
  fi
  for pid in ${pids[@]+"${pids[@]}"}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      echo "dev_up: process ${pid} exited; stopping the stack" >&2
      exit 1
    fi
  done
  sleep 2
done
