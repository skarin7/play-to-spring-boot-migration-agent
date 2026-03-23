#!/usr/bin/env bash
# Entry point for Play → Spring automated upgrade (full monorepo clone).
# Delegates to play-to-spring-kit/scripts/migration_orchestrator.py — same CLI flags.
#
# Example:
#   ./start_upgrade.sh --play-repo /path/to/your-play-app
#   ./start_upgrade.sh --play-repo ../your-play-app
#   ./start_upgrade.sh --help

set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ORCHESTRATOR="${REPO_ROOT}/play-to-spring-kit/scripts/migration_orchestrator.py"
if [[ ! -f "$ORCHESTRATOR" ]]; then
  echo "ERROR: orchestrator not found at ${ORCHESTRATOR}" >&2
  exit 1
fi
exec python3 "$ORCHESTRATOR" "$@"
