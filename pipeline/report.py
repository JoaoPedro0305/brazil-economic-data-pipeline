"""Persist quality results and write a Markdown report for each run."""

from datetime import datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from pipeline.quality import CheckResult


def save_checks(conn: psycopg.Connection, run_id: int, checks: list[CheckResult]) -> None:
    with conn.cursor() as cur:
        cur.executemany(
            """
            INSERT INTO quality_results
                (run_id, series_id, check_name, severity, passed, failures, detail, sample)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            [(run_id, c.series, c.check, c.severity, c.passed, c.failures, c.detail, Jsonb(c.sample))
             for c in checks],
        )


def outcome(c: CheckResult) -> str:
    if c.passed:
        return "pass"
    return "FAIL" if c.severity == "error" else "WARN"


def render(run_id: int, status: str, run_at: datetime, checks: list[CheckResult]) -> str:
    failed = [c for c in checks if not c.passed]
    lines = [
        "# Data quality report",
        "",
        f"Run #{run_id}, {run_at:%Y-%m-%d %H:%M}. Pipeline status: **{status}**. "
        f"{len(checks) - len(failed)} of {len(checks)} checks passed.",
        "",
        "| Series | Check | Severity | Result | Detail |",
        "|---|---|---|---|---|",
    ]
    lines += [f"| {c.series} | {c.check} | {c.severity} | {outcome(c)} | {c.detail} |" for c in checks]
    if failed:
        lines += ["", "## Failures", ""]
        for c in failed:
            blocked = "load blocked" if c.severity == "error" else "warning only"
            lines.append(f"- **{c.series} / {c.check}** ({blocked}): {c.detail}")
            lines += [f"  - {example}" for example in c.sample]
    return "\n".join(lines) + "\n"


def write_report(report_dir: Path, run_id: int, status: str, run_at: datetime, checks: list[CheckResult]) -> Path:
    report_dir.mkdir(parents=True, exist_ok=True)
    text = render(run_id, status, run_at, checks)
    path = report_dir / f"run_{run_id:05d}.md"
    path.write_text(text, encoding="utf-8")
    (report_dir / "latest.md").write_text(text, encoding="utf-8")
    return path
