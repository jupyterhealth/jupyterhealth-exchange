# run a local postgres for the test database
# source test-env.sh first
set -eux
docker run --rm --name jhe-test-db -d -e POSTGRES_USER=${DB_USER} -e POSTGRES_PASSWORD=${DB_PASSWORD} -e POSTGRES_DB=${DB_NAME} -p127.0.0.1:5432:5432 postgres:16
