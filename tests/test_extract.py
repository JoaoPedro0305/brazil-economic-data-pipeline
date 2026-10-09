import json
from datetime import date, datetime

import pytest
import requests
import responses

from pipeline.config import Series
from pipeline.extract import (
    BASE_URL,
    RETRIES,
    ExtractError,
    date_windows,
    fetch_series,
    fetch_window,
    save_raw,
)

USD = Series(1, "usd_brl", "USD/BRL", "BRL per USD", "business_daily")
URL = BASE_URL.format(code=1)
NO_SLEEP = lambda seconds: None  # noqa: E731


def test_windows_never_exceed_ten_years():
    windows = date_windows(date(2000, 1, 1), date(2026, 10, 8))
    assert windows == [
        (date(2000, 1, 1), date(2009, 12, 31)),
        (date(2010, 1, 1), date(2019, 12, 31)),
        (date(2020, 1, 1), date(2026, 10, 8)),
    ]


def test_short_range_is_one_window():
    assert date_windows(date(2026, 10, 1), date(2026, 10, 8)) == [(date(2026, 10, 1), date(2026, 10, 8))]


def test_leap_day_start():
    (first, *_) = date_windows(date(2000, 2, 29), date(2026, 1, 1))
    assert first == (date(2000, 2, 29), date(2010, 2, 27))


@responses.activate
def test_sends_dates_in_brazilian_format():
    responses.get(URL, json=[{"data": "08/10/2026", "valor": "5.0119"}])
    data = fetch_window(requests.Session(), 1, date(2026, 10, 1), date(2026, 10, 8))
    assert data == [{"data": "08/10/2026", "valor": "5.0119"}]
    params = responses.calls[0].request.params
    assert params == {"formato": "json", "dataInicial": "01/10/2026", "dataFinal": "08/10/2026"}


@responses.activate
def test_404_value_not_found_is_empty_not_error():
    responses.get(
        URL, status=404, json={"erro": {"statusCode": 404, "detail": "SGSNegocioException: Value(s) not found"}}
    )
    assert fetch_window(requests.Session(), 1, date(2026, 10, 3), date(2026, 10, 4)) == []


@responses.activate
def test_html_with_status_200_is_retried():
    responses.get(URL, body="<!doctype html><html>erro</html>", status=200, content_type="text/html")
    responses.get(URL, json=[{"data": "08/10/2026", "valor": "5.0119"}])
    waits = []
    data = fetch_window(requests.Session(), 1, date(2026, 10, 1), date(2026, 10, 8), sleep=waits.append)
    assert len(data) == 1
    assert waits == [1]


@responses.activate
def test_server_errors_retry_with_backoff_then_fail():
    for _ in range(RETRIES):
        responses.get(URL, status=502)
    waits = []
    with pytest.raises(ExtractError, match="failed after 4 attempts"):
        fetch_window(requests.Session(), 1, date(2026, 10, 1), date(2026, 10, 8), sleep=waits.append)
    assert waits == [1, 2, 4]


@responses.activate
def test_json_that_is_not_a_list_is_rejected():
    for _ in range(RETRIES):
        responses.get(URL, json={"unexpected": True})
    with pytest.raises(ExtractError):
        fetch_window(requests.Session(), 1, date(2026, 10, 1), date(2026, 10, 8), sleep=NO_SLEEP)


@responses.activate
def test_fetch_series_concatenates_windows():
    responses.get(URL, json=[{"data": "03/01/2000", "valor": "1.80"}])
    responses.get(URL, json=[{"data": "04/01/2010", "valor": "1.72"}])
    data = fetch_series(USD, date(2000, 1, 1), date(2010, 1, 5), session=requests.Session(), sleep=NO_SLEEP)
    assert [r["data"] for r in data] == ["03/01/2000", "04/01/2010"]
    assert len(responses.calls) == 2


def test_save_raw_writes_records_unchanged(tmp_path):
    records = [{"data": "08/10/2026", "valor": "5.0119"}]
    path = save_raw(USD, records, datetime(2026, 10, 8, 19, 0, 0), raw_dir=tmp_path)
    assert path == tmp_path / "usd_brl" / "20261008T190000.json"
    assert json.loads(path.read_text(encoding="utf-8")) == records
