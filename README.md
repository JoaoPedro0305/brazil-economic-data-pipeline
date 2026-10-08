# Brazil Economic Data Pipeline

[![tests](https://github.com/JoaoPedro0305/brazil-economic-data-pipeline/actions/workflows/tests.yml/badge.svg)](https://github.com/JoaoPedro0305/brazil-economic-data-pipeline/actions/workflows/tests.yml) [![live data](https://github.com/JoaoPedro0305/brazil-economic-data-pipeline/actions/workflows/live-data.yml/badge.svg)](https://github.com/JoaoPedro0305/brazil-economic-data-pipeline/actions/workflows/live-data.yml)

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

## Database

PostgreSQL 17 runs in Docker (`docker-compose.yml`, host port 5433 so it does not clash with a local Postgres). The schema is in [`sql/schema.sql`](sql/schema.sql) and is applied by the pipeline itself, so it is safe to run more than once.

| Table | Contents |
|---|---|
| `series` | one row per indicator: SGS code, description, unit, frequency |
| `observations` | the values; primary key `(series_id, ref_date)`, so the database itself rejects duplicate dates |
| `revisions` | values the Central Bank changed after they were first loaded (old and new value) |
| `pipeline_runs` | one row per execution: start, end, status, rows inserted/updated/rejected |
| `quality_results` | the outcome of every quality check, for every series, in every run |

**Idempotent load** (`pipeline/load.py`), one transaction per series:

1. `COPY` the clean rows into a temporary staging table (bulk load, much faster than row-by-row inserts);
2. record changed values in `revisions`;
3. `UPDATE` only the values that changed;
4. `INSERT ... ON CONFLICT DO NOTHING` for new dates.

Loading the full history twice:

| | Inserted | Updated | Unchanged | Time |
|---|---|---|---|---|
| 1st load | 16,822 | 0 | 0 | 1.2s |
| 2nd load (same data) | 0 | 0 | 16,822 | 0.6s |

## Questions answered with SQL

The database answers questions that need more than one series. The queries are in [`sql/queries/`](sql/queries/), run with `python -m pipeline.queries`, and are tested on synthetic data with known answers (`tests/test_queries.py`).

### 1. How did the dollar move during each Selic cycle?

[`01_dollar_by_selic_cycle.sql`](sql/queries/01_dollar_by_selic_cycle.sql) finds every Copom decision (a day the target changed), groups consecutive decisions in the same direction into cycles with window functions, and looks up the dollar before and after each cycle with `LATERAL` joins.

| Cycle | Period | Decisions | Selic | USD/BRL | Dollar |
|---|---|---|---|---|---|
| cut | Mar 2000 to Jan 2001 | 6 | 19.00% to 15.25% | 1.75 to 1.95 | +11.9% |
| hike | Mar 2001 to Jul 2001 | 5 | 15.25% to 19.00% | 2.10 to 2.50 | +19.2% |
| cut | Feb 2002 to Jul 2002 | 3 | 19.00% to 18.00% | 2.43 to 2.88 | +18.5% |
| hike | Oct 2002 to Feb 2003 | 5 | 18.00% to 26.50% | 3.86 to 3.61 | -6.6% |
| cut | Jun 2003 to Apr 2004 | 9 | 26.50% to 16.00% | 2.89 to 2.91 | +0.6% |
| hike | Sep 2004 to May 2005 | 9 | 16.00% to 19.75% | 2.90 to 2.45 | -15.8% |
| cut | Sep 2005 to Sep 2007 | 18 | 19.75% to 11.25% | 2.33 to 1.95 | -16.1% |
| hike | Apr 2008 to Sep 2008 | 4 | 11.25% to 13.75% | 1.67 to 1.83 | +9.3% |
| cut | Jan 2009 to Jul 2009 | 5 | 13.75% to 8.75% | 2.35 to 1.89 | -19.6% |
| hike | Apr 2010 to Jul 2011 | 8 | 8.75% to 12.50% | 1.76 to 1.56 | -11.3% |
| cut | Sep 2011 to Oct 2012 | 10 | 12.50% to 7.25% | 1.59 to 2.04 | +28.3% |
| hike | Apr 2013 to Jul 2015 | 16 | 7.25% to 14.25% | 1.99 to 3.37 | +68.8% |
| cut | Oct 2016 to Aug 2020 | 21 | 14.25% to 2.00% | 3.18 to 5.34 | +68.0% |
| hike | Mar 2021 to Aug 2022 | 12 | 2.00% to 13.75% | 5.66 to 5.24 | -7.4% |
| cut | Aug 2023 to May 2024 | 7 | 13.75% to 10.50% | 4.81 to 5.16 | +7.3% |
| hike | Sep 2024 to Jun 2025 | 7 | 10.50% to 15.00% | 5.48 to 5.49 | +0.2% |
| cut | Mar 2026 to Sep 2026 | 5 | 15.00% to 13.75% | 5.21 to 5.15 | -1.1% |

**Rate direction alone does not explain the dollar.** In the 8 hiking cycles the dollar rose in 4 and fell in 4; in the 9 cutting cycles it rose in 6. The 2013-2015 hikes, from 7.25% to 14.25%, came with the dollar up 69%, during a fiscal crisis and recession. External shocks and fiscal risk weigh more than the Selic's direction.

### 2. What was the real interest rate each year?

[`02_real_interest_rate.sql`](sql/queries/02_real_interest_rate.sql) compounds monthly IPCA into annual inflation (`exp(sum(ln(1 + m))) - 1`, which matches IBGE's official figures, e.g. 12.53% in 2002 and 10.06% in 2021) and compares it with the year's average Selic target: real rate = (1 + Selic) / (1 + IPCA) - 1.

| Year | Selic (avg) | IPCA | Real rate |
|---|---|---|---|
| 2000 | 17.60% | 5.97% | 10.97% |
| 2001 | 17.46% | 7.67% | 9.09% |
| 2002 | 19.22% | 12.53% | 5.95% |
| 2003 | 23.52% | 9.30% | 13.01% |
| 2004 | 16.38% | 7.60% | 8.16% |
| 2005 | 19.14% | 5.69% | 12.72% |
| 2006 | 15.32% | 3.14% | 11.80% |
| 2007 | 12.04% | 4.46% | 7.26% |
| 2008 | 12.45% | 5.90% | 6.18% |
| 2009 | 10.14% | 4.31% | 5.59% |
| 2010 | 9.90% | 5.91% | 3.77% |
| 2011 | 11.76% | 6.50% | 4.93% |
| 2012 | 8.63% | 5.84% | 2.64% |
| 2013 | 8.29% | 5.91% | 2.25% |
| 2014 | 10.96% | 6.41% | 4.28% |
| 2015 | 13.47% | 10.67% | 2.53% |
| 2016 | 14.18% | 6.29% | 7.42% |
| 2017 | 10.15% | 2.95% | 7.00% |
| 2018 | 6.58% | 3.75% | 2.73% |
| 2019 | 6.04% | 4.31% | 1.66% |
| 2020 | 2.89% | 4.52% | **-1.56%** |
| 2021 | 4.54% | 10.06% | **-5.02%** |
| 2022 | 12.55% | 5.78% | 6.39% |
| 2023 | 13.30% | 4.62% | 8.29% |
| 2024 | 10.94% | 4.83% | 5.83% |
| 2025 | 14.42% | 4.26% | 9.74% |

Only 2 years had a negative real rate, 2020 and 2021, when the Selic was cut to 2% and inflation came back. The highest was 2003 (13.01%). The average target is an approximation of what an investor actually earned.

### 3. Does a weaker real show up in inflation, and how fast?

[`03_dollar_pass_through.sql`](sql/queries/03_dollar_pass_through.sql) compares the dollar's change over 12 months with 12-month IPCA measured 0 to 18 months later (exchange-rate pass-through).

| Inflation measured ... | Months compared | Correlation with the dollar's 12-month change |
|---|---|---|
| the same month | 308 | 0.17 |
| 3 months later | 305 | 0.32 |
| 6 months later | 302 | **0.43** |
| 9 months later | 299 | **0.43** |
| 12 months later | 296 | 0.37 |
| 18 months later | 290 | 0.33 |

**The dollar reaches inflation with a lag of about 6 to 9 months:** the correlation is weak in the same month (0.17) and peaks at 0.43 after 6-9 months. This is the pattern described in the economics literature, found here with 26 years of data. It is a correlation, not a causal estimate: other factors (commodity prices, the output gap, the Selic itself) move together with both.

## Running the pipeline

`python -m pipeline.run` runs extract, transform, load and quality checks for every series:

- **Incremental:** the first run fetches the full history; later runs start 30 days (daily series) or 90 days (IPCA) before the last loaded date. Re-fetching that window is how revisions by the Central Bank get picked up, and the idempotent load means it never duplicates rows. `--full` re-fetches everything.
- **Isolated failures:** if one series fails (say, the API is down for the dollar), the others are still loaded and committed. The run is marked `failed`, the error is recorded and the process exits with code 1, so a scheduler can alert on it.
- **One run at a time:** a PostgreSQL advisory lock makes a second, concurrent run (a manual run while the scheduled one is going) skip instead of racing the first one on the same rows.
- **Run log:** each execution is recorded in `pipeline_runs` (status, timings, totals, per-series details as JSON), `quality_results`, `logs/pipeline.log` and `reports/quality/`.

| Run | Range fetched | Rows extracted | Time |
|---|---|---|---|
| Full history | 2000-01-01 to today | 16,822 | ~50s (API) + 1.2s (load) |
| Daily incremental | last 30-90 days | ~58 | 0.7s |

## Data quality

Every run checks each series after loading it and **before committing** (`pipeline/quality.py`). If any `error` check fails, the series is rolled back, so bad data never reaches the database; the rest of the run continues. `warn` checks are reported but do not block.

| Check | Severity | Rule |
|---|---|---|
| `required_values` | error | no missing dates or values |
| `unique_dates` | error | one value per date |
| `value_range` | error | Selic 0-50%, IPCA -3% to 5%, USD/BRL 1-10 |
| `calendar` | error | USD only on weekdays, IPCA only on the 1st of a month |
| `no_future_dates` | error | nothing after today |
| `no_gaps` | error | Selic: no missing day. IPCA: no missing month. USD: never more than 3 weekdays in a row without a quote (holidays exist) |
| `max_jump` | error | USD at most 15% between consecutive days, Selic 5 pp, IPCA 3 pp |
| `freshness` | warn | latest value at most 5 days old (USD), 3 (Selic), 75 (IPCA, published ~10 days after month end) |

**Thresholds come from the real history, with a margin.** Measured on 2000-2026: the biggest USD/BRL daily move was 9.33% (2008-10-08, financial crisis); the longest run of weekdays without a quote was 2 (Carnival); the biggest Selic move at one meeting was 3.00 pp (2002); IPCA never changed more than 1.71 pp from one month to the next. A guessed "5% per day" limit would have blocked real 2008 data. All 24 checks pass on the full history.

**Each check is proven to catch its defect.** `tests/fixtures/quality/` holds a clean file and one copy per defect (missing value, duplicate date, a Saturday, an out-of-range value, a future date, a 5-weekday gap, a 23% jump, stale data). Each test asserts that exactly the right check fails and every other check stays quiet. The out-of-range value (10.20 next to ~9.10) is deliberately a jump smaller than 15%, so it only trips `value_range`.

Results go to the `quality_results` table and to a Markdown report per run (`reports/quality/latest.md`). Example of a blocked load, from the end-to-end tests (a USD quote of 55.00):

```
usd_brl blocked: quality checks failed: value_range (1), max_jump (2)

- usd_brl / value_range (load blocked): 1 value(s) outside [1, 10] BRL per USD
  - 2026-10-07: 55.0
- usd_brl / max_jump (load blocked): 2 change(s) bigger than 15.0%
  - 2026-10-07: 5.0 -> 55.0 (+1000.0 %)
```

## Daily schedule

`docker compose up -d` starts two containers:

| Container | What it does |
|---|---|
| `economy_db` | PostgreSQL 17, data in a named volume |
| `economy_pipeline` | runs the pipeline once on startup (the first run loads the full history), then every day at **19:00 Brasília time** |

- **Scheduler:** [supercronic](https://github.com/aptible/supercronic), a cron built for containers (regular `cron` drops environment variables and hides job output). The schedule is a normal crontab in [`docker/crontab`](docker/crontab). 19:00 is after the day's USD rate (~13:00) and any Copom decision (~18:30) are published; a day missed because the API was down is recovered by the next run's lookback window.
- **Supply chain:** the supercronic binary is pinned to a version and verified against the SHA-256 published on its release before the image is built.
- **Health check:** `python -m pipeline.health` exits 1 when the last successful run is older than 26 hours. It is the container's Docker healthcheck, so `docker ps` shows `unhealthy` if daily runs stop succeeding.
- **Logs and reports on the host:** `data/`, `logs/` and `reports/` are mounted from the project folder, so each run's raw files, `logs/pipeline.log` and quality reports are visible without entering the container. Job output also goes to `docker compose logs pipeline`.
- The container runs as an unprivileged user, and `.gitattributes` keeps LF line endings on the scripts that run inside it, so a Windows checkout does not break them.

The schedule was verified by running the same image with an every-minute crontab: supercronic fired the job, the pipeline completed with 24/24 quality checks passing, and the run appeared in `pipeline_runs`.

## Continuous integration

Two GitHub Actions workflows:

- **[`tests`](.github/workflows/tests.yml)**, on every push and pull request: the full test suite on Python 3.11, 3.12 and 3.13 against a real PostgreSQL service container, plus a build of the Docker image (which re-verifies the supercronic checksum). Locally, database tests are skipped when PostgreSQL is not running; in CI `REQUIRE_DB=1` turns that skip into a failure, so a broken database service can never leave the badge green with half the tests silently skipped.
- **[`live data`](.github/workflows/live-data.yml)**, weekly and whenever the pipeline code changes: the test suite mocks the Central Bank API, so this job runs the real pipeline against the live API from 2000 to today on an empty database. If the API changes its format or real data starts breaking a quality rule, it fails here. The quality report is published on the run page and kept as an artifact.

## How to run

With Docker (database + daily pipeline):

```bash
docker compose up -d --build                       # first start loads the full history
docker compose logs -f pipeline                    # follow the runs
docker compose run --rm pipeline python -m pipeline.run   # extra run on demand
```

Local development and tests:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scriptsctivate
pip install -r requirements-dev.txt
docker compose up -d db          # PostgreSQL on localhost:5433
python -m pipeline.run
python -m pipeline.queries       # analysis queries as Markdown tables
pytest                           # database tests use a separate economy_test database
```

## License

MIT. Data: Banco Central do Brasil, public data.
