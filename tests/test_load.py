from datetime import date
from decimal import Decimal

import pandas as pd
import psycopg
import pytest

from pipeline.config import Series
from pipeline.load import ensure_schema, last_loaded_date, load_observations

USD = Series(1, "usd_brl", "USD/BRL", "BRL per USD", "business_daily")


def clean(rows):
    return pd.DataFrame(
        [("usd_brl", date.fromisoformat(d), v) for d, v in rows], columns=["series", "ref_date", "value"]
    )


def values(conn):
    return conn.execute("SELECT ref_date, value FROM observations ORDER BY ref_date").fetchall()


def test_first_load_inserts_everything(conn):
    r = load_observations(conn, USD, clean([("2026-10-07", 4.9935), ("2026-10-08", 5.0119)]))
    assert (r.inserted, r.updated, r.unchanged) == (2, 0, 0)
    assert values(conn) == [(date(2026, 10, 7), Decimal("4.993500")), (date(2026, 10, 8), Decimal("5.011900"))]
    assert conn.execute("SELECT series_id, sgs_code, frequency FROM series").fetchall() == [
        ("usd_brl", 1, "business_daily")
    ]


def test_loading_twice_changes_nothing(conn):
    data = clean([("2026-10-07", 4.9935), ("2026-10-08", 5.0119)])
    load_observations(conn, USD, data)
    r = load_observations(conn, USD, data)
    assert (r.inserted, r.updated, r.unchanged) == (0, 0, 2)
    assert conn.execute("SELECT count(*) FROM observations").fetchone()[0] == 2
    assert conn.execute("SELECT count(*) FROM revisions").fetchone()[0] == 0


def test_changed_value_is_updated_and_recorded_as_revision(conn):
    load_observations(conn, USD, clean([("2026-10-07", 4.9935), ("2026-10-08", 5.0119)]))
    r = load_observations(conn, USD, clean([("2026-10-08", 5.0200), ("2026-10-09", 5.0300)]))
    assert (r.inserted, r.updated, r.unchanged) == (1, 1, 0)
    assert values(conn)[1] == (date(2026, 10, 8), Decimal("5.020000"))
    assert conn.execute("SELECT ref_date, old_value, new_value FROM revisions").fetchall() == [
        (date(2026, 10, 8), Decimal("5.011900"), Decimal("5.020000"))
    ]


def test_failed_load_rolls_back(conn):
    load_observations(conn, USD, clean([("2026-10-07", 4.9935)]))
    broken = clean([("2026-10-08", 5.0119)])
    broken.loc[1] = ["usd_brl", "not a date", 1.0]
    with pytest.raises(psycopg.Error):
        load_observations(conn, USD, broken)
    conn.rollback()
    assert values(conn) == [(date(2026, 10, 7), Decimal("4.993500"))]


def test_last_loaded_date(conn):
    assert last_loaded_date(conn, USD) is None
    load_observations(conn, USD, clean([("2026-10-07", 4.9935), ("2026-10-08", 5.0119)]))
    assert last_loaded_date(conn, USD) == date(2026, 10, 8)


def test_schema_can_be_applied_twice(conn):
    ensure_schema(conn)
    ensure_schema(conn)


def test_database_rejects_duplicate_dates(conn):
    load_observations(conn, USD, clean([("2026-10-08", 5.0119)]))
    with pytest.raises(psycopg.errors.UniqueViolation):
        conn.execute("INSERT INTO observations (series_id, ref_date, value) VALUES ('usd_brl', '2026-10-08', 1)")
    conn.rollback()
