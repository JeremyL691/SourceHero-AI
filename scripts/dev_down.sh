#!/usr/bin/env bash
# Stop ONLY processes whose PIDs are recorded in .dev/pids/. Never kill by port:
# other projects are running on this machine.
set -uo pipefail
cd "$(dirname "$0")/.."

shopt -s nullglob
files=(.dev/pids/*.pid)
if [[ ${#files[@]} -eq 0 ]]; then
  echo "dev_down: no pid files; nothing to stop"
  exit 0
fi

for pidfile in "${files[@]}"; do
  pid="$(cat "$pidfile" 2>/dev/null || true)"
  [[ -n "$pid" ]] || { rm -f "$pidfile"; continue; }
  name="$(basename "$pidfile" .pid)"
  if ! kill -0 "$pid" 2>/dev/null; then
    echo "dev_down: $name (pid $pid) not running"
    rm -f "$pidfile"; continue
  fi
  # Signal the process group so npm/next wrappers take their children with them.
  pgid="$(ps -o pgid= -p "$pid" 2>/dev/null | tr -d ' ' || true)"
  if [[ -n "$pgid" && "$pgid" -gt 1 ]]; then
    kill -TERM -- "-$pgid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  else
    pkill -TERM -P "$pid" 2>/dev/null || true
    kill -TERM "$pid" 2>/dev/null || true
  fi
  for _ in $(seq 1 25); do kill -0 "$pid" 2>/dev/null || break; sleep 0.2; done
  if kill -0 "$pid" 2>/dev/null; then
    if [[ -n "$pgid" && "$pgid" -gt 1 ]]; then
      kill -KILL -- "-$pgid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
    else
      pkill -KILL -P "$pid" 2>/dev/null || true
      kill -KILL "$pid" 2>/dev/null || true
    fi
    echo "dev_down: $name (pid $pid) needed SIGKILL"
  else
    echo "dev_down: stopped $name (pid $pid)"
  fi
  rm -f "$pidfile"
done
