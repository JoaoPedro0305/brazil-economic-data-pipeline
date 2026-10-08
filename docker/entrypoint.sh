#!/bin/sh
# With a command (docker compose run pipeline python -m pipeline.run), run it.
# Without one: run the pipeline once on startup (the first run loads the full
# history), then hand over to the scheduler. A failed first run must not stop
# the scheduler.
set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

echo "Running the pipeline once on startup..."
python -m pipeline.run || echo "Startup run failed; the daily schedule will retry."

echo "Starting the daily schedule:"
grep -v '^#' /app/docker/crontab
exec supercronic -passthrough-logs /app/docker/crontab
