-- What was the real interest rate each year?
--
-- Real rate = (1 + Selic) / (1 + inflation) - 1, ex post.
-- Selic: average of the daily target over the year (an approximation of what
-- an investor earned). Inflation: IPCA compounded over the 12 months,
-- exp(sum(ln(1 + m))) - 1. Only complete years (12 IPCA months).

WITH ipca AS (
    SELECT extract(year FROM ref_date)::int                     AS year,
           count(*)                                             AS months,
           100 * (exp(sum(ln(1 + value / 100))) - 1)            AS ipca_pct
      FROM observations
     WHERE series_id = 'ipca_monthly'
     GROUP BY 1
),
selic AS (
    SELECT extract(year FROM ref_date)::int AS year,
           avg(value)                       AS selic_avg_pct
      FROM observations
     WHERE series_id = 'selic_target'
     GROUP BY 1
)
SELECT year,
       round(selic_avg_pct, 2)                                                 AS selic_avg_pct,
       round(ipca_pct, 2)                                                      AS ipca_pct,
       round(100 * ((1 + selic_avg_pct / 100) / (1 + ipca_pct / 100) - 1), 2)  AS real_rate_pct
  FROM selic
  JOIN ipca USING (year)
 WHERE months = 12
 ORDER BY year;
