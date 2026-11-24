#!/bin/sh
# Dev-only entrypoint for the `jhe` and `cron` services in docker-compose.yml: provide the
# OIDC signing key, migrate, seed if the database is empty, then run the command. The
# production image (Dockerfile / fly.toml) does not use this.
#
# `cron` runs it too (it waits for `jhe` to be healthy first, so its migrate is a no-op and
# the key already exists): Django's startup checks require OIDC_RSA_PRIVATE_KEY in every
# process, not only the one that signs tokens.
set -eu
cd /code

# The OIDC id_token signing key. Generated once into ./local (gitignored) instead of being
# committed, so no private key, even a dev-only one, sits in the repository where secret
# scanners would flag it. Delete the file to rotate it.
key_file=/code/local/oidc.key
if [ ! -s "$key_file" ]; then
    echo "jhe-entrypoint: generating a dev OIDC signing key at local/oidc.key"
    mkdir -p "$(dirname "$key_file")"
    python - "$key_file" <<'PY'
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
pem = key.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
)
with open(sys.argv[1], "wb") as f:
    f.write(pem)
PY
fi
OIDC_RSA_PRIVATE_KEY="$(cat "$key_file")"
export OIDC_RSA_PRIVATE_KEY

python manage.py migrate --noinput

# `seed` only works on an empty database, so run it once. "Has any user" is the marker:
# seed's first step creates the superuser. Re-seed with scripts/dev/reset.sh.
if python manage.py shell -v 0 -c "import sys; from core.models import JheUser; sys.exit(0 if JheUser.objects.exists() else 1)"; then
    echo "jhe-entrypoint: database already seeded, skipping seed"
else
    echo "jhe-entrypoint: empty database, running seed"
    python manage.py seed
fi

exec "$@"
