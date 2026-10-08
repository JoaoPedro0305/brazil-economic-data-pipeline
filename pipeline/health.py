"""Health check for the scheduled pipeline: was there a successful run recently?

Used as the Docker healthcheck of the pipeline container, so `docker ps` shows
it as unhealthy when daily runs stop succeeding (API down for days, database
problem, scheduler stopped).

Usage:
    python -m pipeline.health    # exit 0 if healthy, 1 otherwise
"""

import sys

import psycopg

from pipeline.config import database_url

MAX_AGE_HOURS = 26  # daily schedule plus a margin


def check(conn: psycopg.Connection, max_age_hours: int = MAX_AGE_HOURS) -> tuple[bool, str]:
    row = conn.execute(
        """
        SELECT run_id, finished_at, extract(epoch FROM now() - finished_at) / 3600
          FROM pipeline_runs
         WHERE status = 'success'
         ORDER BY finished_at DESC
         LIMIT 1
        """
    ).fetchone()
    if row is None:
        return False, "no successful run yet"
    run_id, finished_at, hours = row
    if hours > max_age_hours:
        return False, f"last successful run #{run_id} finished {hours:.1f}h ago (limit {max_age_hours}h)"
    return True, f"last successful run #{run_id} finished {hours:.1f}h ago"


def main() -> None:
    try:
        with psycopg.connect(database_url(), connect_timeout=5) as conn:
            ok, message = check(conn)
    except psycopg.Error as err:
        ok, message = False, f"database unavailable: {err}"
    print(message)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
