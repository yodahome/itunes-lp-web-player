#!/usr/bin/env bash
# Starts the local HTTP-206 server for iTunes LPs and opens the overview in the default browser.
# Works on macOS and Linux regardless of whether the user's interactive shell is Bash, Zsh, or Fish.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
URL="http://127.0.0.1:8000"

cd "$SCRIPT_DIR"

# Locate Python 3
if command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python3"
elif command -v python >/dev/null 2>&1; then
  PYTHON_BIN="python"
else
  echo "[Error] Python 3 was not found in PATH." >&2
  exit 1
fi

# Detect system language (macOS AppleLocale / POSIX LANG)
SYS_LANG="${LC_ALL:-${LANG:-}}"
if [[ -z "$SYS_LANG" ]] && command -v defaults >/dev/null 2>&1; then
  SYS_LANG="$(defaults read -g AppleLocale 2>/dev/null || true)"
fi

# Open default browser in the background after a short delay
(
  sleep 1
  if command -v open >/dev/null 2>&1; then
    open "$URL" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL" >/dev/null 2>&1 || true
  fi
) &

echo "========================================================"
if [[ "${SYS_LANG,,}" == de* ]]; then
  echo "  iTunes LP Server läuft unter $URL"
  echo "  Dieses Terminal geöffnet lassen, solange du hörst."
  echo "  Zum Beenden Strg+C drücken."
else
  echo "  iTunes LP server is running at $URL"
  echo "  Keep this terminal open while listening."
  echo "  Press Ctrl+C to stop the server."
fi
echo "========================================================"
echo ""

exec "$PYTHON_BIN" "$SCRIPT_DIR/serve_itlp.py"