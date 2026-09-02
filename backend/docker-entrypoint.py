"""Start the API, but refuse clearly if the image is older than the code.

`docker compose up` reuses an image it already has and does NOT rebuild when
requirements.txt changes. An image built before the PostgreSQL migration
therefore starts happily and dies on `import psycopg` - a dependency that is
sitting right there in requirements.txt, which makes the bare
ModuleNotFoundError one of the more misleading errors this project can
produce. This turns it into the command that fixes it.

Python rather than a shell script, deliberately. The dev compose bind-mounts
./backend over /app, so this file arrives from the host filesystem - and on a
Windows host it has neither a Unix executable bit nor LF line endings. A shell
script hits both: `sh` needs the exec bit unless invoked explicitly, and dash
reads a CRLF `set -e` as `set -e\r` and reports "set: Illegal option -".
Python's parser handles CRLF transparently and needs no exec bit, so invoking
this as `python docker-entrypoint.py ...` is immune to how the checkout
happened.
"""

import os
import sys

MESSAGE = """
ERROR: this backend image predates the PostgreSQL migration.
       psycopg is in requirements.txt but not in the image, because
       'docker compose up' reuses an image rather than rebuilding it.

       Rebuild:  docker compose up --build
"""

try:
    import psycopg  # noqa: F401
except ModuleNotFoundError:
    print(MESSAGE, file=sys.stderr)
    raise SystemExit(1)

if len(sys.argv) < 2:
    print("usage: docker-entrypoint.py <command> [args...]", file=sys.stderr)
    raise SystemExit(2)

# Replaces this process, so uvicorn keeps PID 1 and still receives the
# SIGTERM that `docker compose down` sends it.
os.execvp(sys.argv[1], sys.argv[1:])
