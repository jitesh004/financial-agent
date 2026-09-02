#!/bin/sh
# Start the API, but refuse clearly if the image is older than the code.
#
# `docker compose up` reuses an image it already has and does NOT rebuild
# when requirements.txt changes. An image built before the PostgreSQL
# migration therefore starts happily and dies on `import psycopg` - a
# dependency that is sitting right there in requirements.txt, which makes the
# bare ModuleNotFoundError one of the more misleading errors this project can
# produce. This turns it into the command that fixes it.
#
# Exec's whatever it is given, so it composes with any command: line.
set -e

if ! python -c 'import psycopg' 2>/dev/null; then
    echo ""
    echo "ERROR: this backend image predates the PostgreSQL migration."
    echo "       psycopg is in requirements.txt but not in the image, because"
    echo "       'docker compose up' reuses an image rather than rebuilding it."
    echo ""
    echo "       Rebuild:  docker compose up --build"
    echo ""
    exit 1
fi

exec "$@"
