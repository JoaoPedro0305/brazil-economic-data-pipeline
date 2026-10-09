"""Which series the pipeline collects and where data goes."""

import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
CLEAN_DIR = ROOT / "data" / "clean"

LOG_DIR = ROOT / "logs"
REPORT_DIR = ROOT / "reports" / "quality"

# History starts here on the first run; later runs are incremental.
START_DATE = date(2000, 1, 1)

# Incremental runs re-fetch this many days before the last loaded date, so
# values the Central Bank revises after publication are picked up.
LOOKBACK_DAYS = {"business_daily": 30, "calendar_daily": 30, "monthly": 90}


@dataclass(frozen=True)
class Series:
    code: int  # SGS code at the Central Bank API
    name: str  # short id used in files and in the database
    description: str
    unit: str
    frequency: str  # "business_daily", "calendar_daily" or "monthly"


SERIES = (
    Series(432, "selic_target", "Selic target rate set by Copom", "% per year", "calendar_daily"),
    Series(433, "ipca_monthly", "IPCA consumer inflation, monthly change", "% per month", "monthly"),
    Series(1, "usd_brl", "USD/BRL exchange rate (sell)", "BRL per USD", "business_daily"),
)


def database_url() -> str:
    return os.environ.get("DATABASE_URL", "postgresql://pipeline:pipeline@127.0.0.1:5433/economy")
