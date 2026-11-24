#!/usr/bin/env bash
# Wipe the local database and start over with freshly seeded data.
#   scripts/dev/reset.sh         asks before deleting
#   scripts/dev/reset.sh --yes   no prompt
#
# Stops the whole stack (so every Django process restarts and drops anything it cached
# from the old database), deletes ./local/db, and brings the stack back up. The `jhe`
# entrypoint sees an empty database and runs `manage.py seed`.
set -euo pipefail
cd "$(dirname "$0")/../.."

if [ "${1:-}" != "--yes" ]; then
    read -r -p "Delete all local JHE data in ./local/db and re-seed? [y/N] " answer
    case "$answer" in [yY]*) ;; *) echo "Aborted."; exit 1 ;; esac
fi

docker compose down
# Delete through a container: Postgres writes its files as its own uid, which a plain
# `rm` can't remove on Linux hosts.
docker run --rm -v "$PWD/local:/local" postgres:16 rm -rf /local/db
docker compose up -d --wait
echo "Reset complete: http://localhost:${JHE_PORT:-8000}"
