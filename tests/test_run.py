"""End-to-end tests: mocked Central Bank API, real PostgreSQL."""

import json
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

import requests
import responses

from pipeline.config import Series
from pipeline.run import run

USD = Series(1, "usd_brl", "USD/BRL", "BRL per USD", "business_daily")
IPCA = Series(433, "ipca_monthly", "IPCA", "% per month", "monthly")
TODAY = date(2026, 10, 8)
NO_SLEEP = lambda seconds: None  # noqa: E731
URL_RE = re.compile(r"https://api\.bcb\.gov\.br/dados/serie/bcdata\.sgs\.(\d+)/dados.*")


def fake_api(failing_codes=()):
    """Answer with one record per business day (USD) or month (IPCA) in the requested window."""
    requested = []

    def callback(request):
        code = int(URL_RE.match(request.url).group(1))
        q = parse_qs(urlparse(request.url).query)
        start = date(*reversed([int(x) for x in q["dataInicial"][0].split("/")]))
        end = date(*reversed([int(x) for x in q["dataFinal"][0].split("/")]))
        requested.append((code, start, end))
        if code in failing_codes:
            return 502, {}, "Bad Gateway"
        days = [start + timedelta(n) for n in range((end - start).days + 1)]
        if code == 433:
            days = [d for d in days if d.day == 1]
        else:
            days = [d for d in days if d.weekday() < 5]
        body = [{"data": d.strftime("%d/%m/%Y"), "valor": "5.0000"} for d in days]
        if not body:
            return 404, {}, '{"erro": {"detail": "Value(s) not found"}}'

        return 200, {"Content-Type": "application/json"}, json.dumps(body)

    responses.add_callback(responses.GET, URL_RE, callback=callback)
    return requested


def go(conn, tmp_path, **kwargs):
    return run(conn, series=(USD, IPCA), today=TODAY, session=requests.Session(),
               raw_dir=tmp_path / "raw", clean_dir=tmp_path / "clean", sleep=NO_SLEEP, **kwargs)


@responses.activate
def test_first_run_loads_full_history(conn, tmp_path):
    requested = fake_api()
    summary = go(conn, tmp_path)

    assert summary.status == "success"
    assert {(code, start) for code, start, _ in requested if start.year == 2000} == {(1, date(2000, 1, 1)), (433, date(2000, 1, 1))}
    usd = conn.execute("SELECT count(*), min(ref_date), max(ref_date) FROM observations WHERE series_id = 'usd_brl'").fetchone()
    assert usd[1:] == (date(2000, 1, 3), TODAY)
    assert summary.details["usd_brl"]["inserted"] == usd[0]

    row = conn.execute("SELECT status, inserted, finished_at IS NOT NULL, details ? 'usd_brl' FROM pipeline_runs").fetchone()
    assert row == ("success", summary.total("inserted"), True, True)
    assert (tmp_path / "raw" / "usd_brl").exists() and (tmp_path / "clean" / "ipca_monthly").exists()


@responses.activate
def test_second_run_is_incremental_with_lookback(conn, tmp_path):
    fake_api()
    go(conn, tmp_path)
    responses.reset()
    requested = fake_api()

    summary = go(conn, tmp_path)

    assert dict((code, start) for code, start, _ in requested) == {
        1: TODAY - timedelta(days=30),            # last USD date minus 30 days
        433: date(2026, 10, 1) - timedelta(days=90),
    }
    assert summary.total("inserted") == 0
    assert summary.total("updated") == 0
    assert conn.execute("SELECT count(*) FROM pipeline_runs WHERE status = 'success'").fetchone()[0] == 2


@responses.activate
def test_full_flag_refetches_everything(conn, tmp_path):
    fake_api()
    go(conn, tmp_path)
    responses.reset()
    requested = fake_api()
    go(conn, tmp_path, full=True)
    assert min(start for _, start, _ in requested) == date(2000, 1, 1)


@responses.activate
def test_failing_series_does_not_stop_the_others(conn, tmp_path):
    fake_api(failing_codes={433})
    summary = go(conn, tmp_path)

    assert summary.status == "failed"
    assert "error" in summary.details["ipca_monthly"]
    assert summary.details["usd_brl"]["inserted"] > 0
    loaded = dict(conn.execute("SELECT series_id, count(*) FROM observations GROUP BY 1").fetchall())
    assert "usd_brl" in loaded and "ipca_monthly" not in loaded
    status, error = conn.execute("SELECT status, error FROM pipeline_runs").fetchone()
    assert status == "failed" and error.startswith("ipca_monthly:")


@responses.activate
def test_failure_in_later_series_keeps_earlier_series(conn, tmp_path):
    fake_api(failing_codes={1})
    run(conn, series=(IPCA, USD), today=TODAY, session=requests.Session(),
        raw_dir=tmp_path / "raw", clean_dir=tmp_path / "clean", sleep=NO_SLEEP)
    assert conn.execute("SELECT count(*) FROM observations WHERE series_id = 'ipca_monthly'").fetchone()[0] > 0
