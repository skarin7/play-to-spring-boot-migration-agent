#!/usr/bin/env bash
# Entry point for Play → Spring automated upgrade (full monorepo clone).
# Delegates to play-to-spring-kit/scripts/migration_orchestrator.py — same CLI flags.
#
# Creates play-to-spring-kit/.venv on first run and installs scripts/requirements-venv.txt
# (orchestrator stays stdlib; optional deps e.g. pyhocon for --export-play-conf).
# Skip sync: MIGRATION_SKIP_VENV_SYNC=1  (use existing .venv as-is)
#
# Example:
#   ./start_upgrade.sh --play-repo /path/to/your-play-app
#   ./start_upgrade.sh --play-repo ../your-play-app
#   ./start_upgrade.sh --help

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_ROOT="${REPO_ROOT}/play-to-spring-kit"
ORCHESTRATOR="${KIT_ROOT}/scripts/migration_orchestrator.py"
REQ_VENV="${KIT_ROOT}/scripts/requirements-venv.txt"
VENV_DIR="${KIT_ROOT}/.venv"
VENV_PY="${VENV_DIR}/bin/python3"

if [[ ! -f "$ORCHESTRATOR" ]]; then
  echo "ERROR: orchestrator not found at ${ORCHESTRATOR}" >&2
  exit 1
fi

if [[ ! -x "$VENV_PY" ]]; then
  echo "[start_upgrade] Creating kit Python venv: ${VENV_DIR}" >&2
  python3 -m venv "${VENV_DIR}"
fi

if [[ "${MIGRATION_SKIP_VENV_SYNC:-}" != "1" ]] && [[ -f "$REQ_VENV" ]]; then
  # Idempotent; quick when requirements already satisfied
  "$VENV_PY" -m pip install -q --upgrade pip
  "$VENV_PY" -m pip install -q -r "$REQ_VENV"
fi

exec "$VENV_PY" "$ORCHESTRATOR" "$@"
