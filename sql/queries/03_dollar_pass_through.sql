-- Does a rise in the dollar show up in inflation, and how long does it take?
--
-- For every month: the USD/BRL change over the previous 12 months (month-end
-- rates) and the IPCA accumulated over 12 months. Then the correlation
-- between the dollar's 12-month change and inflation 0, 3, 6, 9, 12 and 18
-- months later. Economists call this exchange-rate pass-through.

WITH usd_month_end AS (
    SELECT DISTINCT ON (date_trunc('month', ref_date))
           date_trunc('month', ref_date)::date AS month,
           value
      FROM observations
     WHERE series_id = 'usd_brl'
     ORDER BY date_trunc('month', ref_date), ref_date DESC
),
usd_12m AS (
    SELECT month,
           100 * (value / lag(value, 12) OVER (ORDER BY month) - 1) AS usd_12m_pct
      FROM usd_month_end
),
ipca_12m AS (
    SELECT ref_date AS month,
           100 * (exp(sum(ln(1 + value / 100))
                      OVER (ORDER BY ref_date ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)) - 1) AS ipca_12m_pct,
           count(*) OVER (ORDER BY ref_date ROWS BETWEEN 11 PRECEDING AND CURRENT ROW)        AS months
      FROM observations
     WHERE series_id = 'ipca_monthly'
),
lags (lag_months) AS (
    VALUES (0), (3), (6), (9), (12), (18)
)
SELECT l.lag_months,
       count(*)                                                  AS months_compared,
       round(corr(u.usd_12m_pct, i.ipca_12m_pct)::numeric, 2)   AS correlation
  FROM lags l
  JOIN usd_12m u
    ON u.usd_12m_pct IS NOT NULL
  JOIN ipca_12m i
    ON i.month = (u.month + make_interval(months => l.lag_months))::date
   AND i.months = 12
 GROUP BY l.lag_months
 ORDER BY l.lag_months;
