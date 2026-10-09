"""Data quality checks for one series.

Checks are pure functions over a table with `ref_date` and `value`, so they
can be tested without a database. The pipeline runs them on the full series
right after loading it, before committing: if any `error` check fails the
load is rolled back, so bad data never lands. `warn` checks are reported but
do not block.

Thresholds were calibrated on the real history (2000-2026) with a margin:
- USD/BRL biggest daily move was 9.33% (2008-10-08) -> limit 15%;
- the longest run of weekdays without a USD quote was 2 (Carnival) -> limit 3;
- the biggest Selic change at one meeting was 3.00 pp (2002) -> limit 5 pp;
- IPCA's biggest month-to-month change was 1.71 pp -> limit 3 pp.
"""

from dataclasses import asdict, dataclass, field
from datetime import date

import pandas as pd

from pipeline.config import Series


@dataclass(frozen=True)
class Rules:
    min_value: float
    max_value: float
    max_jump: float  # max change between consecutive observations
    jump_unit: str  # "pct" (relative) or "pp" (absolute)
    max_missing_weekdays: int  # business_daily only: longest allowed run of missing weekdays
    max_age_days: int  # freshness: how old the latest value may be


RULES = {
    "selic_target": Rules(0, 50, 5.0, "pp", 0, 3),
    "ipca_monthly": Rules(-3, 5, 3.0, "pp", 0, 75),  # IPCA is published ~10 days after month end
    "usd_brl": Rules(1, 10, 15.0, "pct", 3, 5),
}


@dataclass
class CheckResult:
    series: str
    check: str
    severity: str  # "error" blocks the load, "warn" does not
    passed: bool
    failures: int
    detail: str
    sample: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _result(series, check, severity, bad: list[str], detail_ok: str, detail_bad: str) -> CheckResult:
    return CheckResult(
        series.name,
        check,
        severity,
        passed=not bad,
        failures=len(bad),
        detail=detail_bad if bad else detail_ok,
        sample=bad[:5],
    )


def _runs(dates: pd.DatetimeIndex, step) -> list[tuple[pd.Timestamp, pd.Timestamp, int]]:
    """Group sorted dates into consecutive runs, where `step(a, b)` says b follows a."""
    runs = []
    for d in dates:
        if runs and step(runs[-1][1], d):
            runs[-1] = (runs[-1][0], d, runs[-1][2] + 1)
        else:
            runs.append((d, d, 1))
    return runs


def _fmt_run(start, end, n, unit) -> str:
    return f"{start.date()}" if n == 1 else f"{start.date()}..{end.date()} ({n} {unit})"


def check_required(s, df) -> CheckResult:
    bad = df[df["ref_date"].isna() | df["value"].isna()]
    sample = [f"{d.date() if pd.notna(d) else 'no date'}: value={v}" for d, v in zip(bad["ref_date"], bad["value"])]
    return _result(
        s,
        "required_values",
        "error",
        sample,
        "no missing dates or values",
        f"{len(bad)} row(s) with a missing date or value",
    )


def check_unique(s, df) -> CheckResult:
    dup = df["ref_date"].dropna()
    dup = dup[dup.duplicated(keep=False)].drop_duplicates()
    return _result(
        s,
        "unique_dates",
        "error",
        [str(d.date()) for d in dup],
        "one value per date",
        f"{len(dup)} date(s) appear more than once",
    )


def check_range(s, df, r: Rules) -> CheckResult:
    v = df.dropna(subset=["value"])
    bad = v[(v["value"] < r.min_value) | (v["value"] > r.max_value)]
    return _result(
        s,
        "value_range",
        "error",
        [f"{d.date()}: {x}" for d, x in zip(bad["ref_date"], bad["value"])],
        f"all values within [{r.min_value}, {r.max_value}]",
        f"{len(bad)} value(s) outside [{r.min_value}, {r.max_value}] {s.unit}",
    )


def check_calendar(s, df) -> CheckResult:
    d = df["ref_date"].dropna()
    if s.frequency == "business_daily":
        bad = d[d.dt.dayofweek >= 5]
        ok, problem = "no weekend dates", "date(s) on a weekend"
    elif s.frequency == "monthly":
        bad = d[d.dt.day != 1]
        ok, problem = "every date is the 1st of a month", "date(s) not on the 1st of a month"
    else:
        bad = d.iloc[0:0]
        ok, problem = "any calendar day is valid", ""
    return _result(s, "calendar", "error", [f"{x.date()} ({x.day_name()})" for x in bad], ok, f"{len(bad)} {problem}")


def check_future(s, df, today: date) -> CheckResult:
    bad = df["ref_date"].dropna()
    bad = bad[bad > pd.Timestamp(today)]
    return _result(
        s,
        "no_future_dates",
        "error",
        [str(x.date()) for x in bad],
        f"no dates after {today}",
        f"{len(bad)} date(s) after {today}",
    )


def check_gaps(s, df, r: Rules) -> CheckResult:
    d = pd.DatetimeIndex(df["ref_date"].dropna().drop_duplicates().sort_values())
    if len(d) < 2:
        return _result(s, "no_gaps", "error", [], "fewer than 2 dates, nothing to compare", "")

    if s.frequency == "business_daily":
        expected = pd.bdate_range(d.min(), d.max())
        missing = expected.difference(d)
        position = {day: i for i, day in enumerate(expected)}
        runs = _runs(missing, lambda a, b: position[b] == position[a] + 1)
        bad = [_fmt_run(a, b, n, "weekdays") for a, b, n in runs if n > r.max_missing_weekdays]
        ok = f"{len(missing)} weekday(s) without data (holidays), never more than {r.max_missing_weekdays} in a row"
        problem = f"{len(bad)} run(s) of more than {r.max_missing_weekdays} weekdays without data"
    elif s.frequency == "monthly":
        months = d.to_period("M").to_timestamp()
        missing = pd.date_range(months.min(), months.max(), freq="MS").difference(months)
        runs = _runs(missing, lambda a, b: b == a + pd.offsets.MonthBegin(1))
        bad = [_fmt_run(a, b, n, "months") for a, b, n in runs]
        ok, problem = "no missing months", f"{len(missing)} missing month(s)"
    else:
        missing = pd.date_range(d.min(), d.max()).difference(d)
        runs = _runs(missing, lambda a, b: b == a + pd.Timedelta(days=1))
        bad = [_fmt_run(a, b, n, "days") for a, b, n in runs]
        ok, problem = "no missing days", f"{len(missing)} missing day(s)"
    return _result(s, "no_gaps", "error", bad, ok, problem)


def check_jumps(s, df, r: Rules) -> CheckResult:
    v = df.dropna().drop_duplicates(subset="ref_date").sort_values("ref_date")
    prev = v["value"].shift()
    change = (v["value"] / prev - 1) * 100 if r.jump_unit == "pct" else v["value"] - prev
    bad = v[change.abs() > r.max_jump]
    sample = [
        f"{d.date()}: {p} -> {x} ({c:+.1f}{' %' if r.jump_unit == 'pct' else ' pp'})"
        for d, p, x, c in zip(bad["ref_date"], prev[bad.index], bad["value"], change[bad.index])
    ]
    unit = "%" if r.jump_unit == "pct" else " pp"
    return _result(
        s,
        "max_jump",
        "error",
        sample,
        f"no change bigger than {r.max_jump}{unit} between consecutive values",
        f"{len(bad)} change(s) bigger than {r.max_jump}{unit}",
    )


def check_freshness(s, df, r: Rules, today: date) -> CheckResult:
    latest = df["ref_date"].dropna().max()
    if pd.isna(latest):
        return _result(s, "freshness", "warn", ["no data"], "", "series has no data")
    age = (pd.Timestamp(today) - latest).days
    bad = [f"latest {latest.date()} is {age} days old"] if age > r.max_age_days else []
    return _result(
        s,
        "freshness",
        "warn",
        bad,
        f"latest value {latest.date()} ({age} days old, limit {r.max_age_days})",
        f"latest value is {age} days old (limit {r.max_age_days})",
    )


def check_series(df: pd.DataFrame, series: Series, today: date, rules: Rules | None = None) -> list[CheckResult]:
    """Run every check on one series. df needs `ref_date` and `value` columns."""
    r = rules or RULES[series.name]
    df = pd.DataFrame(
        {
            "ref_date": pd.to_datetime(df["ref_date"], errors="coerce"),
            "value": pd.to_numeric(df["value"], errors="coerce"),
        }
    )
    return [
        check_required(series, df),
        check_unique(series, df),
        check_range(series, df, r),
        check_calendar(series, df),
        check_future(series, df, today),
        check_gaps(series, df, r),
        check_jumps(series, df, r),
        check_freshness(series, df, r, today),
    ]


def blocking_failures(results: list[CheckResult]) -> list[CheckResult]:
    return [c for c in results if c.severity == "error" and not c.passed]
