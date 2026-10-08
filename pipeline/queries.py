"""Run the analysis queries in sql/queries/ and print them as Markdown tables.

Usage:
    python -m pipeline.queries                 # all queries
    python -m pipeline.queries 02              # files starting with 02
"""

import argparse
from decimal import Decimal
from pathlib import Path

import psycopg

from pipeline.config import ROOT, database_url

QUERY_DIR = ROOT / "sql" / "queries"


def query_files(prefix: str = "") -> list[Path]:
    return sorted(QUERY_DIR.glob(f"{prefix}*.sql"))


def run_query(conn: psycopg.Connection, path: Path) -> tuple[list[str], list[tuple]]:
    cur = conn.execute(path.read_text(encoding="utf-8"))
    return [c.name for c in cur.description], cur.fetchall()


def fmt(value) -> str:
    if isinstance(value, Decimal):
        return f"{value.normalize():f}" if value == value.to_integral() else f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def to_markdown(columns: list[str], rows: list[tuple]) -> str:
    lines = ["| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)]
    lines += ["| " + " | ".join(fmt(v) for v in row) + " |" for row in rows]
    return "\n".join(lines)


def main() -> None:
    cli = argparse.ArgumentParser(description="Run the analysis queries.")
    cli.add_argument("prefix", nargs="?", default="", help="only files starting with this prefix")
    args = cli.parse_args()

    with psycopg.connect(database_url()) as conn:
        for path in query_files(args.prefix):
            columns, rows = run_query(conn, path)
            print(f"### {path.name}\n\n{to_markdown(columns, rows)}\n")


if __name__ == "__main__":
    main()
