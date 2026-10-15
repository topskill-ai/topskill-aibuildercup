#!/usr/bin/env bash
# Run a command against private Firestore + Auth emulators (project demo-topskill).
#
#   deploy/scripts/emu_exec.sh <FS_PORT> <AUTH_PORT> <HUB_PORT> <LOG_PORT> -- <command ...>
#
# Example (backend tests of one lane, from backend/):
#   ../deploy/scripts/emu_exec.sh 8102 9102 4402 4502 -- uv run pytest -m "not live"
#
# Several lanes run tests at the same time, so each one uses its own ports. The script writes a
# temporary firebase config at $EMU_CONFIG_ROOT/<lane>/firebase.json (default root
# /private/tmp/claude-501/topskill-emu on macOS, $RUNNER_TEMP or /tmp elsewhere), exports
# FIRESTORE_EMULATOR_HOST / FIREBASE_AUTH_EMULATOR_HOST for those ports and runs
#   firebase emulators:exec --config <that file> --only firestore,auth --project demo-topskill "<command>"
# The command's exit code is returned. Emulators stop when the command ends.
#
# Environment overrides:
#   EMU_LANE            directory name under the config root (default: lane from the git branch
#                       `lane/<name>`, else ports-<FS_PORT>)
#   EMU_CONFIG_ROOT     config root directory
#   EMU_WS_PORT         Firestore emulator websocket port (default LOG_PORT + 100; the default 9150
#                       would collide between lanes)
#   EMU_PROJECT         emulator project id (default demo-topskill; must equal FIRESTORE_PROJECT)
set -euo pipefail

usage() { sed -n '2,24p' "$0"; }

if [[ $# -lt 6 || "$5" != "--" ]]; then
  usage >&2
  exit 2
fi

FS_PORT="$1"; AUTH_PORT="$2"; HUB_PORT="$3"; LOG_PORT="$4"
shift 5
for p in "${FS_PORT}" "${AUTH_PORT}" "${HUB_PORT}" "${LOG_PORT}"; do
  [[ "${p}" =~ ^[0-9]+$ ]] || { echo "emu_exec: port '${p}' is not a number" >&2; exit 2; }
done
WS_PORT="${EMU_WS_PORT:-$((LOG_PORT + 100))}"
PROJECT_ID="${EMU_PROJECT:-demo-topskill}"

# Lane name for the private config directory.
lane="${EMU_LANE:-}"
if [[ -z "${lane}" ]]; then
  branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
  if [[ "${branch}" == lane/* ]]; then lane="${branch#lane/}"; else lane="ports-${FS_PORT}"; fi
fi
lane="${lane//[^A-Za-z0-9_-]/-}"

if [[ -n "${EMU_CONFIG_ROOT:-}" ]]; then
  root="${EMU_CONFIG_ROOT}"
elif [[ -d /private/tmp/claude-501 ]]; then
  root="/private/tmp/claude-501/topskill-emu"
else
  root="${RUNNER_TEMP:-/tmp}/topskill-emu"
fi
cfg_dir="${root}/${lane}"
mkdir -p "${cfg_dir}"
cfg="${cfg_dir}/firebase.json"
cat >"${cfg}" <<JSON
{
  "emulators": {
    "firestore": { "host": "127.0.0.1", "port": ${FS_PORT}, "websocketPort": ${WS_PORT} },
    "auth": { "host": "127.0.0.1", "port": ${AUTH_PORT} },
    "hub": { "host": "127.0.0.1", "port": ${HUB_PORT} },
    "logging": { "host": "127.0.0.1", "port": ${LOG_PORT} },
    "ui": { "enabled": false },
    "singleProjectMode": true
  }
}
JSON

# Firebase emulators need Java >= 21. macOS ships a /usr/bin/java stub, so prefer Homebrew's JDK.
java_major() {
  local v
  v="$("${1}" -version 2>&1 | awk -F'"' '/version/ {print $2; exit}')"
  v="${v%%.*}"
  [[ "${v}" =~ ^[0-9]+$ ]] && echo "${v}" || echo 0
}
if [[ -n "${JAVA_HOME:-}" && -x "${JAVA_HOME}/bin/java" && "$(java_major "${JAVA_HOME}/bin/java")" -ge 21 ]]; then
  :
elif [[ -x /opt/homebrew/opt/openjdk/bin/java ]]; then
  export JAVA_HOME=/opt/homebrew/opt/openjdk
elif command -v java >/dev/null 2>&1 && [[ "$(java_major java)" -ge 21 ]]; then
  :
else
  echo "emu_exec: Java >= 21 not found (set JAVA_HOME)" >&2
  exit 1
fi
if [[ -n "${JAVA_HOME:-}" ]]; then export PATH="${JAVA_HOME}/bin:${PATH}"; fi

command -v firebase >/dev/null 2>&1 || { echo "emu_exec: firebase CLI not found (npm i -g firebase-tools)" >&2; exit 1; }

export FIRESTORE_EMULATOR_HOST="127.0.0.1:${FS_PORT}"
export FIREBASE_AUTH_EMULATOR_HOST="127.0.0.1:${AUTH_PORT}"

# emulators:exec takes the command as one shell string; quote every argument. Firebase is started
# from the config directory (so firestore-debug.log lands there, not in the repo); the command
# itself runs in the caller's working directory.
cmd="cd $(printf '%q' "${PWD}") &&"
for a in "$@"; do cmd+=" $(printf '%q' "${a}")"; done

echo "emu_exec: lane=${lane} firestore=${FIRESTORE_EMULATOR_HOST} auth=${FIREBASE_AUTH_EMULATOR_HOST}" \
  "hub=${HUB_PORT} logging=${LOG_PORT} ws=${WS_PORT} config=${cfg}" >&2
cd "${cfg_dir}"
exec firebase emulators:exec --config "${cfg}" --only firestore,auth --project "${PROJECT_ID}" "${cmd}"
