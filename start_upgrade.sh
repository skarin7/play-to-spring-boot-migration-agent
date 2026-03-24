#!/usr/bin/env bash
# Entry point for Play → Spring automated upgrade (full monorepo clone).
# Delegates to play-to-spring-kit/scripts/migration_orchestrator.py — same CLI flags.
#
# Creates play-to-spring-kit/.venv on first run and installs scripts/requirements-venv.txt
# (orchestrator stays stdlib; optional deps e.g. pyhocon for --export-play-conf).
# Skip venv pip: MIGRATION_SKIP_VENV_SYNC=1
#
# Java: prefers JDK 17+ for Spring Boot 3 / Maven (if JAVA_HOME is unset or is Java 8,
# uses jenv 17 or macOS /usr/libexec/java_home -v 17 / 21).
#
# Headless init: export CURSOR_API_KEY="sk_..." before running (or finish initialize in Cursor IDE).
#
# Optional env (e.g. second app under same parent without clobbering workspace.yaml):
#   export PLAY_REPO=$HOME/Work/CodeBase/CMS/cms-tenant-service
#   export MIGRATION_WORKSPACE=$HOME/Work/CodeBase/cms-tenant-migration-ws
#   ./start_upgrade.sh
# When MIGRATION_WORKSPACE is set and you do not pass --workspace, it is prepended automatically.
#
# Compile-fix models (cursor-agent after mvn compile failures):
#   Default: same model for every round as --cursor-model / CURSOR_MODEL (default composer-2).
#   Token optimisation: pass --optimise-compile-fix or -O (or MIGRATION_OPTIMISE_COMPILE_FIX=1) to use
#   a cheaper model for the first rounds, then escalate to the main model.
#
# Examples:
#   ./start_upgrade.sh --play-repo /path/to/your-play-app
#   ./start_upgrade.sh --play-repo ../your-play-app --optimise-compile-fix
#   ./start_upgrade.sh --play-repo ../your-play-app --workspace /path/to/migration-ws
#   ./start_upgrade.sh --help

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT_ROOT="${REPO_ROOT}/play-to-spring-kit"
ORCHESTRATOR="${KIT_ROOT}/scripts/migration_orchestrator.py"
REQ_VENV="${KIT_ROOT}/scripts/requirements-venv.txt"
VENV_DIR="${KIT_ROOT}/.venv"
VENV_PY="${VENV_DIR}/bin/python3"

# --- Java 17+ (avoid jenv global 1.8 breaking Spring / Maven) ---
if [[ -n "${JAVA_HOME:-}" ]] && [[ -x "${JAVA_HOME}/bin/java" ]]; then
  _java_ver="$("${JAVA_HOME}/bin/java" -version 2>&1 | head -1 || true)"
  if echo "$_java_ver" | grep -q '"1\.8'; then
    echo "[start_upgrade] JAVA_HOME is Java 8; switching to JDK 17+ for Spring/Maven." >&2
    unset JAVA_HOME
  fi
fi
if [[ -z "${JAVA_HOME:-}" ]]; then
  if command -v jenv >/dev/null 2>&1 && jenv prefix 17 >/dev/null 2>&1; then
    export JAVA_HOME="$(jenv prefix 17)"
    export PATH="${JAVA_HOME}/bin:${PATH}"
    echo "[start_upgrade] JAVA_HOME=$JAVA_HOME (jenv 17)" >&2
  elif [[ -x /usr/libexec/java_home ]]; then
    _jh="$(/usr/libexec/java_home -v 17 2>/dev/null || /usr/libexec/java_home -v 21 2>/dev/null || true)"
    if [[ -n "$_jh" ]]; then
      export JAVA_HOME="$_jh"
      export PATH="${JAVA_HOME}/bin:${PATH}"
      echo "[start_upgrade] JAVA_HOME=$JAVA_HOME (java_home)" >&2
    fi
  fi
fi

if [[ -z "${CURSOR_API_KEY:-}" ]]; then
  echo "[start_upgrade] warn: CURSOR_API_KEY unset — headless Spring init (cursor-agent) exits 3 until set or IDE init completes." >&2
fi

if [[ ! -f "$ORCHESTRATOR" ]]; then
  echo "ERROR: orchestrator not found at ${ORCHESTRATOR}" >&2
  exit 1
fi

# Optional: inject --workspace when MIGRATION_WORKSPACE is set and argv has no --workspace
EXTRA=()
if [[ -n "${MIGRATION_WORKSPACE:-}" ]]; then
  _has_ws=false
  for a in "$@"; do
    if [[ "$a" == "--workspace" ]] || [[ "$a" == --workspace=* ]]; then
      _has_ws=true
      break
    fi
  done
  if [[ "$_has_ws" == false ]]; then
    mkdir -p "$MIGRATION_WORKSPACE"
    _ws="$(cd "$MIGRATION_WORKSPACE" && pwd)"
    EXTRA=(--workspace "$_ws")
    echo "[start_upgrade] using MIGRATION_WORKSPACE=$_ws" >&2
  fi
fi

if [[ ! -x "$VENV_PY" ]]; then
  echo "[start_upgrade] Creating kit Python venv: ${VENV_DIR}" >&2
  python3 -m venv "${VENV_DIR}"
fi

if [[ "${MIGRATION_SKIP_VENV_SYNC:-}" != "1" ]] && [[ -f "$REQ_VENV" ]]; then
  "$VENV_PY" -m pip install -q --upgrade pip
  "$VENV_PY" -m pip install -q -r "$REQ_VENV"
fi

exec "$VENV_PY" "$ORCHESTRATOR" "${EXTRA[@]}" "$@"
