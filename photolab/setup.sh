#!/usr/bin/env bash
# First-time setup on Linux, and again after pulling changes: Python packages, web packages, web build.
# Needs uv (https://docs.astral.sh/uv/) and Node.js 20+.
set -euo pipefail
cd "$(dirname "$0")"
(cd backend && uv sync)
(cd frontend && if [ -f package-lock.json ]; then npm ci; else npm install; fi && npm run build)
echo "PhotoLab is set up. Next: ./start-photolab.sh"
