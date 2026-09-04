-- public.get_dashboard_kpis() (RPC) — Parameterized version
-- All p_* params default to NULL → treated as "no filter" (fully backward-compatible).
-- Accepts optional filter params for the real_estate block so all KPIs reflect
-- the active filter state from the frontend.

DROP FUNCTION IF EXISTS public.get_dashboard_kpis(text, text, integer, boolean, uuid[], date, date);

DROP FUNCTION IF EXISTS public.get_dashboard_kpis(text, text, integer, boolean, uuid[], date, date);

CREATE OR REPLACE FUNCTION public.get_dashboard_kpis(
  p_market        TEXT     DEFAULT NULL,
  p_platform      TEXT     DEFAULT NULL,
  p_bedrooms      INTEGER  DEFAULT NULL,
  p_is_active     BOOLEAN  DEFAULT NULL,
  p_property_ids  UUID[]   DEFAULT NULL,
  p_start_date    DATE     DEFAULT NULL,
  p_end_date      DATE     DEFAULT NULL
)
RETURNS json
LANGUAGE sql
SECURITY DEFINER
AS $
  WITH target_props AS (
    SELECT DISTINCT p.id, p.market, p.platform, p.bedrooms, p.is_active
    FROM properties p
    WHERE (p_market       IS NULL OR p.market    = p_market)
      AND (p_platform     IS NULL OR p.platform  = p_platform)
      AND (p_bedrooms     IS NULL OR p.bedrooms  = p_bedrooms)
      AND (p_is_active    IS NULL OR p.is_active = p_is_active)
      AND (p_property_ids IS NULL OR p.id        = ANY(p_property_ids))
  ),
  latest_scrape AS (
    SELECT DISTINCT ON (rh.property_id)
      rh.property_id,
      rh.is_available,
      rh.nightly_rate
    FROM rate_history rh
    JOIN target_props tp ON tp.id = rh.property_id
    ORDER BY rh.property_id, rh.created_at DESC
  )
  SELECT json_build_object(
    'pricing', json_build_object(
      'products_tracked',   (SELECT COUNT(*) FROM products),
      'price_changes_7d',   (
        SELECT COUNT(*) FROM price_history
        WHERE created_at >= NOW() - INTERVAL '7 days'
        AND price != (
          SELECT price FROM price_history ph2
          WHERE ph2.product_id = price_history.product_id
          AND ph2.created_at < price_history.created_at
          ORDER BY ph2.created_at DESC LIMIT 1
        )
      ),
      'spikes_7d',          (
        SELECT COUNT(*) FROM v_price_volatility
        WHERE ABS(pct_above_trailing_avg) >= 25
        AND created_at >= NOW() - INTERVAL '7 days'
      ),
      'last_scrape_status', (
        SELECT last_status FROM v_scrape_health
        WHERE job_type = 'PRICE_MONITOR'
        ORDER BY last_started_at DESC LIMIT 1
      ),
      'tracking_since',     (SELECT MIN(created_at) FROM products)
    ),
    'leads', json_build_object(
      'targets_crawled',      (SELECT COUNT(*) FROM lead_targets),
      'new_leads_7d',         (SELECT COUNT(*) FROM leads WHERE created_at >= NOW() - INTERVAL '7 days'),
      'total_leads_all_time', (SELECT COUNT(*) FROM leads),
      'last_scrape_status',   (
        SELECT last_status FROM v_scrape_health
        WHERE job_type = 'LEAD_GEN'
        ORDER BY last_started_at DESC LIMIT 1
      )
    ),
    'real_estate', json_build_object(
      -- Properties Tracked (exact count from target_props)
      'properties_tracked',     (SELECT COUNT(*) FROM target_props),

      -- Live Availability Breakdown
      'available_count',        (SELECT COUNT(*) FROM latest_scrape WHERE is_available = true),
      'unavailable_count',      (SELECT COUNT(*) FROM latest_scrape WHERE is_available = false OR is_available IS NULL),
      'availability_rate_pct',  (
        SELECT ROUND(
          (COUNT(*) FILTER (WHERE is_available = true)::numeric / NULLIF(COUNT(*), 0)::numeric) * 100.0,
          2
        )
        FROM latest_scrape
      ),

      -- Rate Changes 7d (scrape-window based)
      'rate_changes_7d', (
        SELECT COUNT(*)
        FROM rate_history rh
        JOIN target_props tp ON tp.id = rh.property_id
        WHERE rh.created_at >= NOW() - INTERVAL '7 days'
          AND rh.nightly_rate IS NOT NULL
          AND rh.nightly_rate != (
            SELECT nightly_rate FROM rate_history rh2
            WHERE rh2.property_id = rh.property_id
              AND rh2.created_at  < rh.created_at
              AND rh2.nightly_rate IS NOT NULL
            ORDER BY rh2.created_at DESC LIMIT 1
          )
      ),

      -- Spikes 7d (>= 25% deviation from trailing avg within last 7 days)
      'spikes_7d', (
        SELECT COUNT(*)
        FROM v_rate_volatility v
        JOIN target_props tp ON tp.id = v.property_id
        WHERE ABS(v.pct_above_trailing_avg) >= 25
          AND v.recorded_at >= NOW() - INTERVAL '7 days'
          AND (p_start_date IS NULL OR v.stay_date >= p_start_date)
          AND (p_end_date   IS NULL OR v.stay_date <= p_end_date)
      ),

      'tracking_since',     (SELECT MIN(created_at) FROM properties),
      'last_scrape_status', (
        SELECT COALESCE(json_object_agg(platform, last_status), '{}'::json)
        FROM v_scrape_health
        WHERE job_type = 'REAL_ESTATE_MONITOR'
      )
    )
  );function$;



-- Permissions
GRANT EXECUTE ON FUNCTION public.get_dashboard_kpis(TEXT, TEXT, INTEGER, BOOLEAN, UUID[], DATE, DATE) TO anon;



-- public.v_rate_volatility — exact DDL
create or replace view public.v_rate_volatility as
WITH day_ranked AS (
         SELECT rh_1.id,
            rh_1.property_id,
            rh_1.stay_date,
            rh_1.nightly_rate,
            rh_1.is_available,
            rh_1.currency,
            rh_1.meta_data,
            rh_1.created_at,
            rh_1.updated_at,
            dense_rank() OVER (PARTITION BY rh_1.property_id ORDER BY (date((rh_1.created_at AT TIME ZONE 'America/New_York'::text)))) AS day_rank
           FROM rate_history rh_1
          WHERE rh_1.nightly_rate IS NOT NULL
        ), daily_avg AS (
         SELECT day_ranked.property_id,
            day_ranked.day_rank,
            avg(day_ranked.nightly_rate) AS day_avg
           FROM day_ranked
          GROUP BY day_ranked.property_id, day_ranked.day_rank
        ), trailing_data AS (
         SELECT daily_avg.property_id,
            daily_avg.day_rank,
            avg(daily_avg.day_avg) OVER (PARTITION BY daily_avg.property_id ORDER BY daily_avg.day_rank ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING) AS trailing_avg_rate
           FROM daily_avg
        ), valid_rates AS (
         SELECT dr.id,
            dr.property_id,
            dr.stay_date,
            dr.nightly_rate,
            dr.created_at,
            t.trailing_avg_rate
           FROM day_ranked dr
             JOIN trailing_data t ON t.property_id = dr.property_id AND t.day_rank = dr.day_rank
        )
 SELECT rh.id,
    p.id AS property_id,
    p.name AS property_name,
    p.url,
    p.market,
    p.platform,
    p.latitude,
    p.longitude,
    p.bedrooms,
    p.avg_rating,
    p.review_count,
    p.is_active,
    rh.stay_date,
    rh.nightly_rate,
    rh.is_available,
    rh.currency,
    rh.created_at AS recorded_at,
    vr.trailing_avg_rate,
    round((rh.nightly_rate - vr.trailing_avg_rate) / NULLIF(vr.trailing_avg_rate, 0::numeric) * 100::numeric, 2) AS pct_above_trailing_avg
   FROM rate_history rh
     JOIN properties p ON p.id = rh.property_id
     LEFT JOIN valid_rates vr ON vr.id = rh.id;

-- Permissions
GRANT MAINTAIN ON TABLE public.v_rate_volatility TO anon;
GRANT REFERENCES ON TABLE public.v_rate_volatility TO anon;
GRANT SELECT ON TABLE public.v_rate_volatility TO anon;
GRANT TRIGGER ON TABLE public.v_rate_volatility TO anon;
GRANT TRUNCATE ON TABLE public.v_rate_volatility TO anon;




-- public.v_scrape_health — exact DDL
create or replace view public.v_scrape_health as
WITH recent AS (
         SELECT scrape_runs.job_type,
            scrape_runs.status,
            scrape_runs.started_at,
            scrape_runs.finished_at,
            scrape_runs.items_attempted,
            scrape_runs.items_succeeded,
            scrape_runs.items_failed,
            scrape_runs.error_summary,
            scrape_runs.id,
            scrape_runs.created_at,
            scrape_runs.updated_at,
            scrape_runs.platform,
            scrape_runs.meta_data,
            row_number() OVER (PARTITION BY scrape_runs.job_type, scrape_runs.platform ORDER BY scrape_runs.started_at DESC) AS rn
           FROM scrape_runs
        )
 SELECT job_type,
    platform,
    status AS last_status,
    started_at AS last_started_at,
    finished_at AS last_finished_at,
    EXTRACT(epoch FROM finished_at - started_at) AS last_duration_seconds,
    items_attempted,
    items_succeeded,
    items_failed,
    meta_data,
    error_summary,
        CASE
            WHEN status::text = 'failed'::text THEN true
            ELSE false
        END AS is_failed,
        CASE
            WHEN (items_failed::double precision / NULLIF(items_attempted, 0)::double precision) > 0.20::double precision THEN true
            ELSE false
        END AS high_failure_rate,
        CASE
            WHEN ((meta_data ->> 'blocked_count'::text)::integer) > 0 THEN true
            ELSE false
        END AS has_blocks
   FROM recent
  WHERE rn = 1;




GRANT MAINTAIN ON TABLE public.v_scrape_health TO anon;
GRANT REFERENCES ON TABLE public.v_scrape_health TO anon;
GRANT SELECT ON TABLE public.v_scrape_health TO anon;
GRANT TRIGGER ON TABLE public.v_scrape_health TO anon;
GRANT TRUNCATE ON TABLE public.v_scrape_health TO anon;