"""Each fixture in tests/fixtures/quality/ is clean.csv with exactly one planted
defect. Every check must catch its own defect and stay quiet on the others."""

from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from pipeline.config import Series
from pipeline.quality import RULES, blocking_failures, check_series

FIXTURES = Path(__file__).parent / "fixtures" / "quality"
USD = Series(1, "usd_brl", "USD/BRL", "BRL per USD", "business_daily")
SELIC = Series(432, "selic_target", "Selic", "% per year", "calendar_daily")
IPCA = Series(433, "ipca_monthly", "IPCA", "% per month", "monthly")
TODAY = date(2026, 10, 1)


def failed(results):
    return {c.check: c.failures for c in results if not c.passed}


def run_fixture(name):
    return check_series(pd.read_csv(FIXTURES / f"{name}.csv"), USD, TODAY)


def test_clean_fixture_passes_every_check():
    results = run_fixture("clean")
    assert failed(results) == {}
    assert len(results) == 8


@pytest.mark.parametrize("fixture, expected", [
    ("missing_value", {"required_values": 1}),
    ("duplicate_date", {"unique_dates": 1}),
    ("weekend_date", {"calendar": 1}),
    ("out_of_range", {"value_range": 1}),
    ("future_date", {"no_future_dates": 1}),
    ("gap", {"no_gaps": 1}),
    ("jump", {"max_jump": 2}),          # down 23% and back up 30%
    ("stale", {"freshness": 1}),
])
def test_each_planted_defect_is_caught_by_its_check_only(fixture, expected):
    assert failed(run_fixture(fixture)) == expected


def test_failures_explain_what_is_wrong():
    (gap,) = [c for c in run_fixture("gap") if c.check == "no_gaps"]
    assert gap.sample == ["2026-09-14..2026-09-18 (5 weekdays)"]
    (jump,) = [c for c in run_fixture("jump") if c.check == "max_jump"]
    assert jump.sample[0] == "2026-09-17: 9.11 -> 7.0 (-23.2 %)"
    (weekend,) = [c for c in run_fixture("weekend_date") if c.check == "calendar"]
    assert weekend.sample == ["2026-09-12 (Saturday)"]


def test_freshness_warns_but_does_not_block():
    results = run_fixture("stale")
    assert not blocking_failures(results)
    (fresh,) = [c for c in results if c.check == "freshness"]
    assert fresh.severity == "warn" and fresh.detail == "latest value is 15 days old (limit 5)"


def test_every_other_defect_blocks_the_load():
    for name in ["missing_value", "duplicate_date", "weekend_date", "out_of_range", "future_date", "gap", "jump"]:
        assert blocking_failures(run_fixture(name)), name


def test_holidays_are_not_gaps():
    # Carnival 2026: Mon 16 and Tue 17 Feb without quotes
    days = pd.bdate_range("2026-02-09", "2026-02-27").difference(pd.to_datetime(["2026-02-16", "2026-02-17"]))
    df = pd.DataFrame({"ref_date": days, "value": 5.0})
    assert "no_gaps" not in failed(check_series(df, USD, date(2026, 2, 27)))


def test_monthly_series_rules():
    ok = pd.DataFrame({"ref_date": pd.date_range("2026-01-01", "2026-08-01", freq="MS"), "value": 0.3})
    assert failed(check_series(ok, IPCA, TODAY)) == {}

    bad = ok.drop(index=[3, 4])                       # April and May missing
    bad.loc[99] = [pd.Timestamp("2026-08-15"), 0.3]   # not on the 1st
    result = failed(check_series(bad, IPCA, TODAY))
    assert result == {"no_gaps": 1, "calendar": 1}


def test_calendar_daily_series_rules():
    days = pd.date_range("2026-09-01", "2026-09-30")
    ok = pd.DataFrame({"ref_date": days, "value": 13.75})
    assert failed(check_series(ok, SELIC, TODAY)) == {}            # weekends are expected here

    bad = ok[ok["ref_date"] != "2026-09-13"].copy()
    bad.loc[bad["ref_date"] == "2026-09-20", "value"] = 20.0      # +6.25 pp, then back
    assert failed(check_series(bad, SELIC, TODAY)) == {"no_gaps": 1, "max_jump": 2}


def test_empty_series():
    results = check_series(pd.DataFrame({"ref_date": [], "value": []}), USD, TODAY)
    assert failed(results) == {"freshness": 1}


def test_real_history_thresholds_are_documented():
    assert RULES["usd_brl"].max_jump > 9.33        # biggest real daily move, 2008-10-08
    assert RULES["usd_brl"].max_missing_weekdays >= 2  # Carnival
    assert RULES["selic_target"].max_jump > 3.0    # biggest real Copom move, 2002
