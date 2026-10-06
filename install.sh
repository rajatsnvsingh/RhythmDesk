#!/usr/bin/env bash
set -Eeuo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
[[ -f .env ]] || { printf '%s\n' 'Copy .env.example to .env and set your paths and a secret token first.' >&2; exit 1; }
command -v docker >/dev/null || { printf '%s\n' 'Docker with Compose is required.' >&2; exit 1; }
docker compose config --quiet
docker compose up --build -d
printf '%s\n' 'Rhythm Desk started. Use docker compose logs -f to inspect startup.'
