"""Download series from the Central Bank SGS API and save them unchanged.

API behavior this module handles (checked against the live API):
- Daily series accept at most 10 years per request (10 years + 1 day fails
  with a 502), so long ranges are split into windows.
- A window with no data (weekend, future dates) returns 404 with a JSON body
  "Value(s) not found": that is an empty result, not an error.
- Occasionally the API answers 200 with an HTML error page instead of JSON,
  so every response is validated and retried if it is not a JSON list.
"""

import json
import logging
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

import requests

from pipeline.config import RAW_DIR, Series

log = logging.getLogger(__name__)

BASE_URL = "https://api.bcb.gov.br/dados/serie/bcdata.sgs.{code}/dados"
WINDOW_YEARS = 10
TIMEOUT = 60
RETRIES = 4


class ExtractError(Exception):
    pass


def add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29 -> Feb 28
        return d.replace(year=d.year + years, day=28)


def date_windows(start: date, end: date, years: int = WINDOW_YEARS) -> list[tuple[date, date]]:
    """Split [start, end] into consecutive windows of at most `years` years."""
    windows = []
    current = start
    while current <= end:
        window_end = min(add_years(current, years) - timedelta(days=1), end)
        windows.append((current, window_end))
        current = window_end + timedelta(days=1)
    return windows


def fetch_window(
    session: requests.Session,
    code: int,
    start: date,
    end: date,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """Fetch one window, retrying on network errors, 5xx and non-JSON answers."""
    params = {
        "formato": "json",
        "dataInicial": start.strftime("%d/%m/%Y"),
        "dataFinal": end.strftime("%d/%m/%Y"),
    }
    url = BASE_URL.format(code=code)
    last_error = None

    for attempt in range(1, RETRIES + 1):
        try:
            response = session.get(url, params=params, timeout=TIMEOUT)
            if response.status_code == 404 and "not found" in response.text.lower():
                return []
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, list):
                raise ValueError(f"expected a JSON list, got {type(data).__name__}")
            return data
        except (requests.RequestException, ValueError) as err:
            # ValueError also covers HTML pages that fail JSON decoding.
            last_error = err
            if attempt < RETRIES:
                wait = 2 ** (attempt - 1)
                log.warning(
                    "SGS %s %s..%s attempt %d failed (%s); retrying in %ss", code, start, end, attempt, err, wait
                )
                sleep(wait)

    raise ExtractError(f"SGS {code} {start}..{end} failed after {RETRIES} attempts: {last_error}")


def fetch_series(
    series: Series,
    start: date,
    end: date,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> list[dict]:
    """All observations of a series between start and end, as returned by the API."""
    session = session or requests.Session()
    records = []
    for window_start, window_end in date_windows(start, end):
        window = fetch_window(session, series.code, window_start, window_end, sleep=sleep)
        log.info("SGS %s (%s) %s..%s: %d records", series.code, series.name, window_start, window_end, len(window))
        records.extend(window)
    return records


def save_raw(series: Series, records: list[dict], run_at: datetime, raw_dir: Path = RAW_DIR) -> Path:
    """Write the API records unchanged, one file per series per run."""
    folder = raw_dir / series.name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{run_at:%Y%m%dT%H%M%S}.json"
    path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return path
