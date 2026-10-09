"""Run the whole pipeline: extract -> transform -> load -> quality, for every series.

- First run: full history from START_DATE. Later runs: incremental, starting
  LOOKBACK_DAYS before the last loaded date to pick up revisions.
- Quality checks run on the full series after loading it, before committing.
  If an `error` check fails, the series is rolled back: bad data never lands.
- A failing series does not stop the others; the run is then marked failed
  and the process exits with code 1.
- Only one run at a time: a PostgreSQL advisory lock makes a second, concurrent
  run (say, a manual run while the scheduled one is going) skip instead.
- Every run is recorded in pipeline_runs, quality_results, logs/pipeline.log
  and a Markdown report in reports/quality/.

Usage:
    python -m pipeline.run            # incremental
    python -m pipeline.run --full     # re-fetch everything since START_DATE
"""

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg
import requests
from psycopg.types.json import Jsonb

from pipeline.config import (
    CLEAN_DIR,
    LOG_DIR,
    LOOKBACK_DAYS,
    RAW_DIR,
    REPORT_DIR,
    SERIES,
    START_DATE,
    Series,
    database_url,
)
from pipeline.extract import fetch_series, save_raw
from pipeline.load import connect, ensure_schema, last_loaded_date, load_observations, read_series
from pipeline.quality import CheckResult, blocking_failures, check_series
from pipeline.report import save_checks, write_report
from pipeline.transform import save_clean, transform

log = logging.getLogger("pipeline")

LOCK_KEY = 20_000_101  # arbitrary id for this pipeline's advisory lock


class QualityError(Exception):
    pass


@dataclass
class RunSummary:
    run_id: int
    status: str = "running"
    details: dict = field(default_factory=dict)
    checks: list[CheckResult] = field(default_factory=list)
    report: Path | None = None

    def total(self, key: str) -> int:
        return sum(d.get(key, 0) for d in self.details.values())


def start_date_for(conn: psycopg.Connection, series: Series, full: bool) -> date:
    last = None if full else last_loaded_date(conn, series)
    if last is None:
        return START_DATE
    return max(START_DATE, last - timedelta(days=LOOKBACK_DAYS[series.frequency]))


def start_run(conn: psycopg.Connection) -> int:
    run_id = conn.execute("INSERT INTO pipeline_runs (status) VALUES ('running') RETURNING run_id").fetchone()[0]
    conn.commit()
    return run_id


def finish_run(conn: psycopg.Connection, summary: RunSummary, error: str | None) -> None:
    conn.execute(
        """
        UPDATE pipeline_runs
           SET finished_at = now(), status = %s, inserted = %s, updated = %s,
               rejected = %s, details = %s, error = %s
         WHERE run_id = %s
        """,
        (
            summary.status,
            summary.total("inserted"),
            summary.total("updated"),
            summary.total("rejected"),
            Jsonb(summary.details),
            error,
            summary.run_id,
        ),
    )
    conn.commit()


def run_series(
    conn,
    series: Series,
    today: date,
    run_at: datetime,
    full: bool,
    summary: RunSummary,
    session: requests.Session,
    raw_dir: Path,
    clean_dir: Path,
    sleep,
) -> dict:
    t0 = time.perf_counter()
    start = start_date_for(conn, series, full)

    records = fetch_series(series, start, today, session=session, sleep=sleep)
    save_raw(series, records, run_at, raw_dir=raw_dir)

    result = transform(series, records)
    save_clean(series, result, run_at, clean_dir=clean_dir)

    loaded = load_observations(conn, series, result.clean)

    # Check the full series as it would look after this load, before committing.
    checks = check_series(read_series(conn, series), series, today)
    summary.checks.extend(checks)
    blocking = blocking_failures(checks)
    if blocking:
        conn.rollback()  # bad data never lands
        save_checks(conn, summary.run_id, checks)
        conn.commit()
        raise QualityError("quality checks failed: " + ", ".join(f"{c.check} ({c.failures})" for c in blocking))

    conn.commit()  # persist this series now, so a later failure cannot roll it back
    save_checks(conn, summary.run_id, checks)
    conn.commit()
    return {
        "from": start.isoformat(),
        "to": today.isoformat(),
        "extracted": len(records),
        "rejected": len(result.rejected),
        "duplicates": result.duplicates,
        "conflicts": result.conflicts,
        "inserted": loaded.inserted,
        "updated": loaded.updated,
        "unchanged": loaded.unchanged,
        "quality_warnings": [c.check for c in checks if not c.passed],
        "seconds": round(time.perf_counter() - t0, 2),
    }


def run(
    conn: psycopg.Connection,
    series: tuple[Series, ...] = SERIES,
    today: date | None = None,
    full: bool = False,
    session: requests.Session | None = None,
    raw_dir: Path = RAW_DIR,
    clean_dir: Path = CLEAN_DIR,
    report_dir: Path = REPORT_DIR,
    sleep=time.sleep,
) -> RunSummary:
    today = today or date.today()
    session = session or requests.Session()
    run_at = datetime.now()

    ensure_schema(conn)
    locked = conn.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,)).fetchone()[0]
    conn.commit()
    if not locked:
        log.warning("another run is in progress; skipping this one")
        return RunSummary(run_id=0, status="skipped")
    try:
        return _run_locked(conn, series, today, full, session, raw_dir, clean_dir, report_dir, sleep, run_at)
    finally:
        conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_KEY,))
        conn.commit()


def _run_locked(conn, series, today, full, session, raw_dir, clean_dir, report_dir, sleep, run_at) -> RunSummary:
    summary = RunSummary(start_run(conn))
    log.info("run %d started (%s)", summary.run_id, "full" if full else "incremental")

    errors = []
    for s in series:
        try:
            d = run_series(conn, s, today, run_at, full, summary, session, raw_dir, clean_dir, sleep)
            summary.details[s.name] = d
            log.info(
                "%s: %s..%s extracted %d, inserted %d, updated %d, unchanged %d, rejected %d (%.2fs)",
                s.name,
                d["from"],
                d["to"],
                d["extracted"],
                d["inserted"],
                d["updated"],
                d["unchanged"],
                d["rejected"],
                d["seconds"],
            )
            for w in d["quality_warnings"]:
                log.warning("%s: quality warning on %s", s.name, w)
        except Exception as err:  # keep going with the other series
            conn.rollback()
            summary.details[s.name] = {"error": str(err)}
            errors.append(f"{s.name}: {err}")
            if isinstance(err, QualityError):
                log.error("%s blocked: %s", s.name, err)
            else:
                log.exception("%s failed", s.name)

    summary.status = "failed" if errors else "success"
    finish_run(conn, summary, "; ".join(errors) or None)
    if summary.checks:
        summary.report = write_report(report_dir, summary.run_id, summary.status, run_at, summary.checks)
    log.info(
        "run %d %s: inserted %d, updated %d, rejected %d, quality %d/%d checks passed",
        summary.run_id,
        summary.status,
        summary.total("inserted"),
        summary.total("updated"),
        summary.total("rejected"),
        sum(c.passed for c in summary.checks),
        len(summary.checks),
    )
    return summary


def setup_logging() -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    for handler in (logging.StreamHandler(), logging.FileHandler(LOG_DIR / "pipeline.log", encoding="utf-8")):
        handler.setFormatter(fmt)
        root.addHandler(handler)


def main() -> None:
    cli = argparse.ArgumentParser(description="Run the economic data pipeline.")
    cli.add_argument("--full", action="store_true", help=f"re-fetch everything since {START_DATE}")
    args = cli.parse_args()

    setup_logging()
    with connect(database_url()) as conn:
        summary = run(conn, full=args.full)
    sys.exit(0 if summary.status in ("success", "skipped") else 1)


if __name__ == "__main__":
    main()
