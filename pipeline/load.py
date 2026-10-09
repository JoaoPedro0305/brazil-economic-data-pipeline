"""Load clean observations into PostgreSQL, idempotently.

Each series is loaded in one transaction:
1. COPY the clean rows into a temporary staging table (fast bulk load);
2. record values that changed since the last load in `revisions`;
3. UPDATE those values in `observations`;
4. INSERT new dates with ON CONFLICT DO NOTHING.

Loading the same data twice inserts and updates nothing.
"""

from dataclasses import dataclass
from datetime import date

import pandas as pd
import psycopg

from pipeline.config import ROOT, Series

SCHEMA_FILE = ROOT / "sql" / "schema.sql"


@dataclass
class LoadResult:
    inserted: int
    updated: int
    unchanged: int


def connect(url: str) -> psycopg.Connection:
    return psycopg.connect(url)


def ensure_schema(conn: psycopg.Connection) -> None:
    with conn.transaction():
        conn.execute(SCHEMA_FILE.read_text(encoding="utf-8"))


def upsert_series(conn: psycopg.Connection, series: Series) -> None:
    conn.execute(
        """
        INSERT INTO series (series_id, sgs_code, description, unit, frequency)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (series_id) DO UPDATE
           SET sgs_code = EXCLUDED.sgs_code, description = EXCLUDED.description,
               unit = EXCLUDED.unit, frequency = EXCLUDED.frequency
        """,
        (series.name, series.code, series.description, series.unit, series.frequency),
    )


def load_observations(conn: psycopg.Connection, series: Series, clean: pd.DataFrame) -> LoadResult:
    with conn.transaction():
        upsert_series(conn, series)
        conn.execute("CREATE TEMP TABLE staging (ref_date DATE, value NUMERIC(14,6)) ON COMMIT DROP")
        with conn.cursor().copy("COPY staging (ref_date, value) FROM STDIN") as copy:
            for ref_date, value in zip(clean["ref_date"], clean["value"], strict=True):
                copy.write_row((ref_date, value))

        updated = conn.execute(
            """
            INSERT INTO revisions (series_id, ref_date, old_value, new_value)
            SELECT o.series_id, o.ref_date, o.value, s.value
              FROM staging s
              JOIN observations o ON o.series_id = %s AND o.ref_date = s.ref_date
             WHERE o.value <> s.value
            """,
            (series.name,),
        ).rowcount
        conn.execute(
            """
            UPDATE observations o
               SET value = s.value, updated_at = now()
              FROM staging s
             WHERE o.series_id = %s AND o.ref_date = s.ref_date AND o.value <> s.value
            """,
            (series.name,),
        )
        inserted = conn.execute(
            """
            INSERT INTO observations (series_id, ref_date, value)
            SELECT %s, ref_date, value FROM staging
            ON CONFLICT (series_id, ref_date) DO NOTHING
            """,
            (series.name,),
        ).rowcount

    return LoadResult(inserted, updated, len(clean) - inserted - updated)


def read_series(conn: psycopg.Connection, series: Series) -> pd.DataFrame:
    """Full series as currently visible to this connection (including uncommitted rows)."""
    rows = conn.execute(
        "SELECT ref_date, value::float8 FROM observations WHERE series_id = %s ORDER BY ref_date",
        (series.name,),
    ).fetchall()
    return pd.DataFrame(rows, columns=["ref_date", "value"])


def last_loaded_date(conn: psycopg.Connection, series: Series) -> date | None:
    row = conn.execute("SELECT max(ref_date) FROM observations WHERE series_id = %s", (series.name,)).fetchone()
    return row[0]
