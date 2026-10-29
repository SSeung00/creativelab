#!/usr/bin/env bash
# Start PhotoLab in this terminal and open its window (Ctrl+C stops it). If it is already running,
# just open the window.
cd "$(dirname "$0")"
URL=http://photolab.localhost:8002/
open_app() {
  for b in microsoft-edge microsoft-edge-stable google-chrome google-chrome-stable chromium chromium-browser brave-browser; do
    if command -v "$b" >/dev/null; then setsid -f "$b" --app="$URL" --class=PhotoLab >/dev/null 2>&1; return; fi
  done
  xdg-open "$URL"
}
if (exec 3<>/dev/tcp/127.0.0.1/8002) 2>/dev/null; then open_app; exit 0; fi
[ -x backend/.venv/bin/python ] || (cd backend && uv sync)
dist=frontend/dist/index.html
if [ ! -f "$dist" ] || [ -n "$(find frontend/src frontend/public frontend/index.html -type f -newer "$dist" -print -quit)" ]; then
  echo "Building the web app (first run or after an update)..."
  (cd frontend && { [ -d node_modules ] || npm install; } && npm run build)
fi
echo "PhotoLab is running at http://127.0.0.1:8002 - keep this terminal open."
(sleep 4; open_app) &
cd backend && exec .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8002 --timeout-graceful-shutdown 2
