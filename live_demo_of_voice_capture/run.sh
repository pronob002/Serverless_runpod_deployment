#!/usr/bin/env bash
# Starts the app via Docker Compose, adding GPU access automatically if this host has an
# NVIDIA GPU with the NVIDIA Container Toolkit installed. Falls back to CPU-only otherwise.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

FILES=(-f docker-compose.yml)

if command -v nvidia-smi >/dev/null 2>&1 \
   && docker run --rm --gpus all busybox true >/dev/null 2>&1; then
    echo "NVIDIA GPU detected and usable by Docker — enabling GPU access."
    FILES+=(-f docker-compose.gpu.yml)
else
    echo "No usable NVIDIA GPU/Container Toolkit found — running CPU-only."
fi


# COMPOSE_BAKE=false: avoids requiring the `docker buildx` plugin (Compose 2.39+ tries to build
# via Bake by default, which errors with a garbled context path if buildx isn't installed).
export COMPOSE_BAKE=false
exec docker compose "${FILES[@]}" up --build "$@"
