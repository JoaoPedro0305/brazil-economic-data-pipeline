from pipeline.health import check


def add_run(conn, status, hours_ago):
    conn.execute(
        "INSERT INTO pipeline_runs (status, started_at, finished_at) "
        "VALUES (%s, now() - make_interval(hours => %s), now() - make_interval(hours => %s))",
        (status, hours_ago, hours_ago),
    )
    conn.commit()


def test_no_runs_is_unhealthy(conn):
    assert check(conn) == (False, "no successful run yet")


def test_recent_success_is_healthy(conn):
    add_run(conn, "success", 2)
    ok, message = check(conn)
    assert ok and message.startswith("last successful run #1 finished 2.0h ago")


def test_old_success_is_unhealthy(conn):
    add_run(conn, "success", 30)
    ok, message = check(conn)
    assert not ok and "limit 26h" in message


def test_recent_failures_do_not_count(conn):
    add_run(conn, "success", 50)
    add_run(conn, "failed", 1)
    add_run(conn, "failed", 25)
    assert check(conn)[0] is False
