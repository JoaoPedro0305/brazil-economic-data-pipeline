# Brazil Economic Data Pipeline

A daily data pipeline that collects Brazilian economic indicators from the Central Bank's public API, cleans them, loads them into PostgreSQL and checks their quality automatically.

> Status: work in progress

## The problem

Economic indicators like the Selic rate, inflation and the dollar are public, but scattered across API calls with quirks (date formats, request limits, occasional bad responses). This project turns them into a clean, always up-to-date database that can answer questions with plain SQL, and refuses to load data that breaks known rules.

## Data

Source: the Central Bank of Brazil's [SGS API](https://dadosabertos.bcb.gov.br/) (public, no key required).

| Series | SGS code | Frequency | Unit |
|---|---|---|---|
| Selic target rate (Copom) | 432 | every calendar day | % per year |
| IPCA inflation, monthly change | 433 | monthly | % per month |
| USD/BRL exchange rate (sell) | 1 | business days | BRL per USD |

History starts on 2000-01-01.

## How the extraction works

`pipeline/extract.py` downloads each series and saves the API response unchanged in `data/raw/<series>/<run timestamp>.json`. Things the live API does that the extractor handles:

- **10-year limit:** daily series reject requests longer than 10 years (10 years + 1 day returns a 502), so the history is fetched in windows.
- **Empty is not an error:** a window with no data (a weekend, a future month) returns a 404 "Value(s) not found", treated as an empty result.
- **Errors disguised as success:** the API sometimes returns an HTML error page with status 200. Every response is checked to be a JSON list, and failures are retried with exponential backoff (1s, 2s, 4s).

## How the transformation works

`pipeline/transform.py` turns raw records into a clean table (`series`, `ref_date`, `value`) saved in `data/clean/<series>/<run timestamp>.csv`:

- dates `dd/mm/yyyy` become ISO dates, and values become numbers (only plain numbers with a dot decimal are accepted);
- **rows that cannot be parsed are not dropped silently**: they go to a `_rejected.csv` file with the reason (`invalid date`, `invalid value`);
- the same date twice with the same value is kept once; with different values it counts as a conflict, and the last value returned by the API wins.

On the full history (2000 to today) the API data came back clean: 16,822 rows, nothing rejected, no duplicates. The rules are still tested with deliberately broken inputs in `tests/test_transform.py`.

## How to run

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
pytest
```

## License

MIT. Data: Banco Central do Brasil, public data.
