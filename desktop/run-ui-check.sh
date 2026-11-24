#!/usr/bin/env bash
set -euo pipefail

if (( $# < 2 )); then
  printf 'Usage: bash %s /absolute/path/to/test/StoreGate.app /path/to/isolated-workspace [test-command ...]\n' "$0" >&2
  exit 2
fi
bundle="$(cd "$1" && pwd -P)"
workspace="$(cd "$2" && pwd -P)"
shift 2
executable="$bundle/Contents/MacOS/StoreGate"
if [[ ! -x "$executable" || "$workspace" != /private/tmp/* || "$bundle" != /private/tmp/* ]]; then
  printf 'Use a test build and data directory under /private/tmp.\n' >&2
  exit 2
fi

storegate_pids() {
  ps -axo pid=,comm= | awk '/\/StoreGate$/ {print $1}' | sort -n
}

before="$(storegate_pids)"
STOREGATE_WORKSPACE_DIR="$workspace" PYTHONDONTWRITEBYTECODE=1 "$executable" &
test_pid=$!

test_running() {
  ps -p "$test_pid" -o ppid= -o comm= | awk -v parent="$$" -v executable="$executable" '
    $1 == parent { sub(/^[[:space:]]*[0-9]+[[:space:]]+/, ""); if ($0 == executable) running = 1 }
    END { exit !running }
  '
}

cleanup() {
  status=$?
  trap - EXIT
  if test_running; then
    kill -TERM "$test_pid" 2>/dev/null || true
    for delay in 1 2 3 4 5; do
      test_running || break
      sleep 1
    done
    if test_running; then
      kill -KILL "$test_pid" 2>/dev/null || true
    fi
  fi
  wait "$test_pid" 2>/dev/null || true
  after="$(storegate_pids)"
  if [[ "$before" == "$after" ]]; then
    printf 'Test StoreGate exited. Existing StoreGate PIDs are unchanged.\n'
  else
    printf 'Test PID %s exited. StoreGate PIDs before: [%s], after: [%s]\n' "$test_pid" "$before" "$after" >&2
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
printf 'Test StoreGate PID: %s\n' "$test_pid"
if (( $# )); then
  "$@" "$test_pid"
else
  read -r -p 'Press Return after checking the test window. ' _
fi
