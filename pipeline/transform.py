"""Turn raw API records into a clean table: series, ref_date, value.

Rules:
- dates come as "dd/mm/yyyy" and values as text with a dot decimal ("5.0119");
- rows whose date or value cannot be parsed are not dropped silently: they go
  to a `rejected` table with the reason;
- the same date twice with the same value is kept once; with different values
  it is a conflict: the last one returned by the API wins and the conflict is
  reported.
"""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline.config import CLEAN_DIR, Series

COLUMNS = ["series", "ref_date", "value"]
REJECTED_COLUMNS = ["series", "raw_date", "raw_value", "reason"]


@dataclass
class TransformResult:
    clean: pd.DataFrame
    rejected: pd.DataFrame
    duplicates: int     # identical rows removed
    conflicts: int      # dates that had different values

    def summary(self) -> str:
        return (f"{len(self.clean):,} clean, {len(self.rejected):,} rejected, "
                f"{self.duplicates:,} duplicates, {self.conflicts:,} conflicts")


def read_raw(path: Path) -> list[dict]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def transform(series: Series, records: list[dict]) -> TransformResult:
    raw = pd.DataFrame(records, columns=["data", "valor"], dtype="string")

    ref_date = pd.to_datetime(raw["data"], format="%d/%m/%Y", errors="coerce").dt.date
    # Only plain numbers with a dot decimal are accepted ("5,01" or "" are rejected).
    valid_number = raw["valor"].str.fullmatch(r"-?\d+(\.\d+)?").fillna(False).astype(bool)
    value = pd.to_numeric(raw["valor"].where(valid_number), errors="coerce")

    reason = pd.Series(pd.NA, index=raw.index, dtype="string")
    reason[~valid_number] = "invalid value"
    reason[ref_date.isna()] = "invalid date"
    bad = reason.notna()

    rejected = pd.DataFrame({
        "series": series.name,
        "raw_date": raw["data"],
        "raw_value": raw["valor"],
        "reason": reason,
    })[bad].reset_index(drop=True)

    clean = pd.DataFrame({"series": series.name, "ref_date": ref_date, "value": value})[~bad]

    before = len(clean)
    clean = clean.drop_duplicates()
    duplicates = before - len(clean)

    conflicting_dates = clean["ref_date"].duplicated(keep=False)
    conflicts = clean.loc[conflicting_dates, "ref_date"].nunique()
    clean = clean.drop_duplicates(subset="ref_date", keep="last")

    clean = clean.sort_values("ref_date").reset_index(drop=True)[COLUMNS]
    return TransformResult(clean, rejected[REJECTED_COLUMNS], duplicates, conflicts)


def save_clean(series: Series, result: TransformResult, run_at: datetime, clean_dir: Path = CLEAN_DIR) -> Path:
    """Save the clean table (and rejected rows, if any) for this run as CSV."""
    folder = clean_dir / series.name
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{run_at:%Y%m%dT%H%M%S}.csv"
    result.clean.to_csv(path, index=False)
    if len(result.rejected):
        result.rejected.to_csv(folder / f"{run_at:%Y%m%dT%H%M%S}_rejected.csv", index=False)
    return path
