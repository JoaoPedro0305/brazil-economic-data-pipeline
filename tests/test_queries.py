"""The analysis queries run on small synthetic data with known answers."""

from datetime import date, timedelta
from decimal import Decimal

import pandas as pd

from pipeline.config import Series
from pipeline.load import load_observations
from pipeline.queries import QUERY_DIR, query_files, run_query, to_markdown

SELIC = Series(432, "selic_target", "Selic", "% per year", "calendar_daily")
IPCA = Series(433, "ipca_monthly", "IPCA", "% per month", "monthly")
USD = Series(1, "usd_brl", "USD/BRL", "BRL per USD", "business_daily")


def load(conn, series, rows):
    df = pd.DataFrame([(series.name, d, v) for d, v in rows], columns=["series", "ref_date", "value"])
    load_observations(conn, series, df)
    conn.commit()


def days(start, end):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def query(conn, prefix):
    (path,) = query_files(prefix)
    return run_query(conn, path)


def test_every_query_file_is_found():
    assert [p.name for p in query_files()] == sorted(p.name for p in QUERY_DIR.glob("*.sql"))
    assert len(query_files()) == 3


def test_selic_cycles_and_dollar_change(conn):
    # Selic: 10% -> hike to 11% (Feb 1) and 12% (Mar 1) -> cut to 11.5% (Apr 1)
    def selic_on(d):
        return (
            12.0
            if date(2024, 3, 1) <= d < date(2024, 4, 1)
            else (11.0 if date(2024, 2, 1) <= d < date(2024, 3, 1) else (11.5 if d >= date(2024, 4, 1) else 10.0))
        )

    load(conn, SELIC, [(d, selic_on(d)) for d in days(date(2024, 1, 1), date(2024, 4, 30))])

    # USD: 5.00 until Feb 1, 5.50 from Mar 1, 4.95 from Apr 1 (weekdays only)
    def usd_on(d):
        return 4.95 if d >= date(2024, 4, 1) else (5.50 if d >= date(2024, 3, 1) else 5.00)

    load(conn, USD, [(d, usd_on(d)) for d in days(date(2024, 1, 1), date(2024, 4, 30)) if d.weekday() < 5])

    columns, rows = query(conn, "01")
    assert columns[:6] == ["kind", "first_decision", "last_decision", "decisions", "selic_before", "selic_after"]
    hike, cut = rows
    assert hike[:6] == ("hike", date(2024, 2, 1), date(2024, 3, 1), 2, Decimal("10.000000"), Decimal("12.000000"))
    assert hike[-1] == Decimal("10.0")  # 5.00 -> 5.50
    assert cut[:4] == ("cut", date(2024, 4, 1), date(2024, 4, 1), 1)
    assert cut[-1] == Decimal("-10.0")  # 5.50 -> 4.95


def test_real_interest_rate_compounds_inflation(conn):
    load(conn, SELIC, [(d, 12.68) for d in days(date(2023, 1, 1), date(2023, 12, 31))])
    load(
        conn, IPCA, [(date(2023, m, 1), 1.0) for m in range(1, 13)] + [(date(2024, m, 1), 1.0) for m in range(1, 4)]
    )  # 2024 incomplete: excluded
    columns, rows = query(conn, "02")
    assert columns == ["year", "selic_avg_pct", "ipca_pct", "real_rate_pct"]
    assert rows == [(2023, Decimal("12.68"), Decimal("12.68"), Decimal("0.00"))]  # 1.01^12 - 1 = 12.68%


def test_pass_through_correlation(conn):
    # IPCA follows the dollar with a 6-month delay, so correlation peaks at lag 6.
    months = pd.date_range("2010-01-01", "2016-12-01", freq="MS")
    usd = [(m.date(), 2.0 + 0.5 * ((i // 12) % 2) + 0.01 * i) for i, m in enumerate(months)]
    load(conn, USD, [(d, v) for d, v in usd])
    usd_by_month = {d: v for d, v in usd}
    ipca = []
    for m in months:
        src = (m - pd.DateOffset(months=6)).date()
        prev = (m - pd.DateOffset(months=7)).date()
        change = (usd_by_month[src] / usd_by_month[prev] - 1) * 10 if prev in usd_by_month else 0.0
        ipca.append((m.date(), round(0.4 + change, 4)))
    load(conn, IPCA, ipca)

    columns, rows = query(conn, "03")
    assert columns == ["lag_months", "months_compared", "correlation"]
    by_lag = {lag: corr for lag, _, corr in rows}
    assert set(by_lag) == {0, 3, 6, 9, 12, 18}
    assert max(by_lag, key=by_lag.get) == 6


def test_markdown_table():
    md = to_markdown(["year", "rate"], [(2023, Decimal("8.290000")), (2024, Decimal("10.000000"))])
    assert md == "| year | rate |\n|---|---|\n| 2023 | 8.29 |\n| 2024 | 10 |"
