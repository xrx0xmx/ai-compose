#!/bin/bash
set -e

# Wrapper around the official postgres entrypoint.
# After PostgreSQL starts, it ensures the user password matches
# POSTGRES_PASSWORD even when the data directory already exists
# (the official image only sets the password on first init).

# Start the real entrypoint in the background
docker-entrypoint.sh postgres &
PG_PID=$!

# Wait for postgres to accept connections (local socket = trust auth)
until pg_isready -U "${POSTGRES_USER:-litellm}" -q; do
  sleep 1
done

# Sync password from environment variable
if [ -n "$POSTGRES_PASSWORD" ]; then
  psql -U "${POSTGRES_USER:-litellm}" -d "${POSTGRES_DB:-litellm}" -c \
    "ALTER USER ${POSTGRES_USER:-litellm} PASSWORD '${POSTGRES_PASSWORD}';" \
    >/dev/null 2>&1 && echo "pg-entrypoint: password synced" \
    || echo "pg-entrypoint: password sync skipped (already correct or first init)"
fi

# Keep running — follow the postgres process
wait $PG_PID
