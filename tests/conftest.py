"""Database fixtures.

Tests marked with `db` run against a real PostgreSQL in a separate database
(`<name>_test`), created fresh for the test session. Set TEST_DATABASE_URL to
point elsewhere. Without a reachable server those tests are skipped, so the
unit tests still run anywhere; with REQUIRE_DB=1 (set in CI) they fail instead.
"""

import os

import psycopg
import pytest
from psycopg import sql

from pipeline.config import database_url
from pipeline.load import ensure_schema

TEST_URL = os.environ.get("TEST_DATABASE_URL", database_url().rsplit("/", 1)[0] + "/economy_test")


def _admin_url(url: str) -> str:
    return url.rsplit("/", 1)[0] + "/postgres"


@pytest.fixture(scope="session")
def test_database():
    name = TEST_URL.rsplit("/", 1)[1]
    try:
        admin = psycopg.connect(_admin_url(TEST_URL), autocommit=True, connect_timeout=3)
    except psycopg.OperationalError as err:
        message = f"PostgreSQL not reachable ({err.__class__.__name__})"
        if os.environ.get("REQUIRE_DB") == "1":  # CI: a missing database must fail, not skip
            pytest.fail(message)
        pytest.skip(f"{message}; start it with docker compose up -d db")
    with admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    return TEST_URL


@pytest.fixture
def conn(test_database):
    """A connection to an empty schema: tables are recreated for every test."""
    with psycopg.connect(test_database) as conn:
        conn.execute("DROP TABLE IF EXISTS quality_results, pipeline_runs, revisions, observations, series CASCADE")
        conn.commit()
        ensure_schema(conn)
        yield conn
