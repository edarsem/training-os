#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

BACKEND_HOST="${BACKEND_HOST:-0.0.0.0}"
BACKEND_PORT="${BACKEND_PORT:-8000}"
FRONTEND_PORT="${FRONTEND_PORT:-3000}"
AUTO_REFRESH="${AUTO_REFRESH:-true}"
PY_BIN="${PY_BIN:-python3}"

BACKEND_PID=""
FRONTEND_PID=""

cleanup() {
  if [[ -n "${BACKEND_PID}" ]] && kill -0 "${BACKEND_PID}" 2>/dev/null; then
    kill "${BACKEND_PID}" 2>/dev/null || true
  fi
  if [[ -n "${FRONTEND_PID}" ]] && kill -0 "${FRONTEND_PID}" 2>/dev/null; then
    kill "${FRONTEND_PID}" 2>/dev/null || true
  fi
}

trap cleanup EXIT INT TERM

kill_port_listeners() {
  local port="$1"
  local pids
  pids="$(lsof -nP -t -iTCP:"${port}" -sTCP:LISTEN 2>/dev/null || true)"
  if [[ -n "${pids}" ]]; then
    echo "Stopping process(es) on port ${port}: ${pids}"
    kill ${pids} 2>/dev/null || true
    sleep 0.5
  fi
}

if [[ -f "${ROOT_DIR}/.venv/bin/activate" ]]; then
  source "${ROOT_DIR}/.venv/bin/activate"
elif [[ -f "${ROOT_DIR}/backend/venv/bin/activate" ]]; then
  source "${ROOT_DIR}/backend/venv/bin/activate"
fi

kill_port_listeners "${BACKEND_PORT}"
kill_port_listeners "${FRONTEND_PORT}"

echo "Starting backend on http://${BACKEND_HOST}:${BACKEND_PORT}"
(
  cd "${ROOT_DIR}"
  STRAVA_AUTO_REFRESH_ON_STARTUP="${AUTO_REFRESH}" \
  "${PY_BIN}" -m uvicorn app.main:app --reload --app-dir backend --host "${BACKEND_HOST}" --port "${BACKEND_PORT}"
) &
BACKEND_PID=$!

echo "Starting frontend on http://localhost:${FRONTEND_PORT}"
(
  cd "${ROOT_DIR}/frontend"
  "${PY_BIN}" -m http.server "${FRONTEND_PORT}"
) &
FRONTEND_PID=$!

echo ""
echo "Training OS is running:"
echo "- App: http://localhost:${FRONTEND_PORT}"
echo "- API: http://localhost:${BACKEND_PORT}/docs"
echo ""
echo "Press Ctrl+C to stop both services."

wait -n "${BACKEND_PID}" "${FRONTEND_PID}"
