#!/usr/bin/env bash
set -e

# Change to project root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Ensure virtual environment exists
if [ ! -d ".venv" ]; then
  echo "Virtual environment (.venv) not found. Creating one..."
  python3 -m venv .venv
  .venv/bin/pip install --upgrade pip
  .venv/bin/pip install -r requirements.txt
fi

# Determine expected host and port from arguments or defaults
HOST="127.0.0.1"
PORT="5000"

args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  case "${args[i]}" in
    --host)
      if [ $((i+1)) -lt ${#args[@]} ]; then
        HOST="${args[i+1]}"
      fi
      ;;
    --host=*)
      HOST="${args[i]#*=}"
      ;;
    --port)
      if [ $((i+1)) -lt ${#args[@]} ]; then
        PORT="${args[i+1]}"
      fi
      ;;
    --port=*)
      PORT="${args[i]#*=}"
      ;;
  esac
done

TARGET_HOST="$HOST"
if [ "$TARGET_HOST" = "0.0.0.0" ]; then
  TARGET_HOST="127.0.0.1"
fi
URL="http://${TARGET_HOST}:${PORT}"

# If server is already running, open the browser window and exit cleanly
if (echo > "/dev/tcp/${TARGET_HOST}/${PORT}") >/dev/null 2>&1; then
  echo "Photo Uploader is already running at ${URL}."
  echo "Opening in browser..."
  if command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1
  elif command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1
  elif [ -x ".venv/bin/python" ]; then
    .venv/bin/python -m webbrowser "$URL" >/dev/null 2>&1
  else
    python3 -m webbrowser "$URL" >/dev/null 2>&1
  fi
  exit 0
fi

# Automatically open browser window once server is ready
(
  set +e
  server_ready=0
  for _ in {1..30}; do
    if ! kill -0 $$ 2>/dev/null; then
      exit 0
    fi
    if (echo > "/dev/tcp/${TARGET_HOST}/${PORT}") >/dev/null 2>&1 || curl -s -m 1 -o /dev/null "$URL" >/dev/null 2>&1; then
      server_ready=1
      break
    fi
    sleep 0.5
  done

  if [ "$server_ready" -eq 1 ]; then
    echo "Opening ${URL} in browser..."
    if command -v xdg-open >/dev/null 2>&1; then
      xdg-open "$URL" >/dev/null 2>&1
    elif command -v open >/dev/null 2>&1; then
      open "$URL" >/dev/null 2>&1
    elif [ -x ".venv/bin/python" ]; then
      .venv/bin/python -m webbrowser "$URL" >/dev/null 2>&1
    else
      python3 -m webbrowser "$URL" >/dev/null 2>&1
    fi
  fi
) &

# Run photo uploader web application
exec .venv/bin/python photo_uploader.py "$@"
