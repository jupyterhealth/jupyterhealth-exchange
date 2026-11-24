#!/usr/bin/env bash
# Run a Django management command in the running `jhe` container.
#   scripts/dev/manage.sh showmigrations core
#   scripts/dev/manage.sh seed_rich_demo
set -euo pipefail
cd "$(dirname "$0")/../.."

# Allocate a TTY only when we have one (scripts and pipes need -T).
if [ -t 0 ] && [ -t 1 ]; then
    exec docker compose exec jhe python manage.py "$@"
else
    exec docker compose exec -T jhe python manage.py "$@"
fi
