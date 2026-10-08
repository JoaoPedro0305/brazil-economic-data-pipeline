-- How much did the dollar move during each Selic hiking or cutting cycle?
--
-- A Copom decision is a day the Selic target changed. Consecutive decisions in
-- the same direction form a cycle; a cycle ends when the direction flips.
-- USD/BRL at the start is the last quote *before* the first decision (the
-- rate before the cycle began); at the end, the last quote on or before the
-- last decision. The target changes on any calendar day, quotes only exist on
-- business days, hence "last quote before".

WITH selic AS (
    SELECT ref_date, value,
           value - lag(value) OVER (ORDER BY ref_date) AS change
      FROM observations
     WHERE series_id = 'selic_target'
),
decisions AS (
    SELECT ref_date, value, change,
           sign(change) AS direction,
           lag(sign(change)) OVER (ORDER BY ref_date) AS previous_direction
      FROM selic
     WHERE change <> 0
),
numbered AS (
    -- running count of direction flips = cycle number
    SELECT *,
           sum(CASE WHEN direction = previous_direction THEN 0 ELSE 1 END)
               OVER (ORDER BY ref_date) AS cycle
      FROM decisions
),
cycles AS (
    SELECT cycle,
           CASE WHEN min(direction) > 0 THEN 'hike' ELSE 'cut' END        AS kind,
           min(ref_date)                                                   AS first_decision,
           max(ref_date)                                                   AS last_decision,
           count(*)                                                        AS decisions,
           (array_agg(value - change ORDER BY ref_date))[1]                AS selic_before,
           (array_agg(value ORDER BY ref_date DESC))[1]                    AS selic_after
      FROM numbered
     GROUP BY cycle
)
SELECT c.kind,
       c.first_decision,
       c.last_decision,
       c.decisions,
       c.selic_before,
       c.selic_after,
       usd_start.value                                       AS usd_start,
       usd_end.value                                         AS usd_end,
       round(100 * (usd_end.value / usd_start.value - 1), 1) AS usd_change_pct
  FROM cycles c
 CROSS JOIN LATERAL (
       SELECT value FROM observations
        WHERE series_id = 'usd_brl' AND ref_date < c.first_decision
        ORDER BY ref_date DESC LIMIT 1) usd_start
 CROSS JOIN LATERAL (
       SELECT value FROM observations
        WHERE series_id = 'usd_brl' AND ref_date <= c.last_decision
        ORDER BY ref_date DESC LIMIT 1) usd_end
 ORDER BY c.first_decision;
