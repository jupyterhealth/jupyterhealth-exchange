#!/usr/bin/env bash
# Django shell (IPython) in the running `jhe` container. Models are auto-imported.
set -euo pipefail
exec "$(dirname "$0")/manage.sh" shell "$@"
