from datetime import date, datetime

import pandas as pd

from pipeline.config import Series
from pipeline.transform import save_clean, transform

USD = Series(1, "usd_brl", "USD/BRL", "BRL per USD", "business_daily")


def rec(d, v):
    return {"data": d, "valor": v}


def test_parses_dates_and_values():
    r = transform(USD, [rec("07/10/2026", "4.9935"), rec("08/10/2026", "5.0119")])
    assert list(r.clean.columns) == ["series", "ref_date", "value"]
    assert r.clean.to_dict("records") == [
        {"series": "usd_brl", "ref_date": date(2026, 10, 7), "value": 4.9935},
        {"series": "usd_brl", "ref_date": date(2026, 10, 8), "value": 5.0119},
    ]
    assert r.rejected.empty


def test_negative_values_are_valid():
    r = transform(USD, [rec("01/08/2026", "-0.32")])
    assert r.clean["value"].tolist() == [-0.32]


def test_output_is_sorted_by_date():
    r = transform(USD, [rec("08/10/2026", "5.01"), rec("06/10/2026", "4.96"), rec("07/10/2026", "4.99")])
    assert r.clean["ref_date"].tolist() == [date(2026, 10, 6), date(2026, 10, 7), date(2026, 10, 8)]


def test_invalid_rows_are_rejected_with_reason():
    r = transform(USD, [
        rec("08/10/2026", "5.0119"),
        rec("31/02/2026", "5.00"),      # impossible date
        rec("2026-10-08", "5.00"),      # wrong date format
        rec("09/10/2026", ""),          # empty value
        rec("10/10/2026", "5,01"),      # comma decimal
        rec("11/10/2026", "abc"),
    ])
    assert len(r.clean) == 1
    assert r.rejected[["raw_date", "reason"]].values.tolist() == [
        ["31/02/2026", "invalid date"],
        ["2026-10-08", "invalid date"],
        ["09/10/2026", "invalid value"],
        ["10/10/2026", "invalid value"],
        ["11/10/2026", "invalid value"],
    ]


def test_missing_value_key_is_rejected():
    r = transform(USD, [{"data": "08/10/2026"}])
    assert r.clean.empty
    assert r.rejected["reason"].tolist() == ["invalid value"]


def test_identical_duplicates_are_kept_once():
    r = transform(USD, [rec("08/10/2026", "5.0119"), rec("08/10/2026", "5.0119")])
    assert len(r.clean) == 1
    assert r.duplicates == 1
    assert r.conflicts == 0


def test_conflicting_duplicates_keep_last_and_are_counted():
    r = transform(USD, [rec("08/10/2026", "5.0119"), rec("08/10/2026", "5.0200")])
    assert r.clean["value"].tolist() == [5.02]
    assert r.conflicts == 1


def test_empty_input():
    r = transform(USD, [])
    assert r.clean.empty and r.rejected.empty
    assert list(r.clean.columns) == ["series", "ref_date", "value"]


def test_save_clean_writes_csv_and_rejected(tmp_path):
    r = transform(USD, [rec("08/10/2026", "5.0119"), rec("xx", "1")])
    path = save_clean(USD, r, datetime(2026, 10, 8, 19, 0), clean_dir=tmp_path)
    assert path == tmp_path / "usd_brl" / "20261008T190000.csv"
    assert pd.read_csv(path).to_dict("records") == [{"series": "usd_brl", "ref_date": "2026-10-08", "value": 5.0119}]
    assert (tmp_path / "usd_brl" / "20261008T190000_rejected.csv").exists()
