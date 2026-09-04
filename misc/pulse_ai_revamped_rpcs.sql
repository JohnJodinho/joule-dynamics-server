-- ============================================================================
-- PULSE AI BACKEND REVAMP: CORRECTNESS, CONTRACT STRICTNESS & TOKEN OPTIMIZATION
-- ============================================================================
-- Each block contains explicit DROP FUNCTION IF EXISTS statements followed by
-- CREATE OR REPLACE FUNCTION and GRANT EXECUTE permissions.
-- Copy and paste each block individually into the Supabase SQL Editor.
-- ============================================================================


-- ============================================================================
-- 1. get_market_snapshot
-- ============================================================================
DROP FUNCTION IF EXISTS public.get_market_snapshot(text, date, date);

CREATE OR REPLACE FUNCTION public.get_market_snapshot(
  p_market text,
  p_start_date date,
  p_end_date date
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  WITH market_props AS (
    SELECT DISTINCT ON (property_id)
      property_id,
      is_available,
      is_active
    FROM public.v_rate_volatility
    WHERE LOWER(market) = LOWER(p_market)
    ORDER BY property_id, recorded_at DESC
  )
  SELECT json_build_object(
    'status', 'success',
    'market', p_market,
    'period', json_build_object('start', p_start_date, 'end', p_end_date),
    'active_properties', (SELECT COUNT(*) FILTER (WHERE is_active = true) FROM market_props),
    'total_tracked', (SELECT COUNT(*) FROM market_props),
    'available_count', (SELECT COUNT(*) FILTER (WHERE is_available = true AND is_active = true) FROM market_props),
    'unavailable_count', (SELECT COUNT(*) FILTER (WHERE is_available = false AND is_active = true) FROM market_props),
    'availability_rate_pct', (
      SELECT ROUND(
        100.0 * COUNT(*) FILTER (WHERE is_available = true AND is_active = true)
        / NULLIF(COUNT(*) FILTER (WHERE is_active = true), 0), 2
      )
      FROM market_props
    ),
    'avg_nightly_rate', ROUND(AVG(rh.nightly_rate)::numeric, 2),
    'min_nightly_rate', ROUND(MIN(rh.nightly_rate)::numeric, 2),
    'max_nightly_rate', ROUND(MAX(rh.nightly_rate)::numeric, 2),
    'volatility_events', (
      SELECT COUNT(*)
      FROM public.v_rate_volatility v
      WHERE LOWER(v.market) = LOWER(p_market)
        AND ABS(v.pct_above_trailing_avg) >= 25
        AND v.recorded_at::date BETWEEN p_start_date AND p_end_date
    )
  )
  FROM public.rate_history rh
  JOIN public.properties p ON p.id = rh.property_id
  WHERE LOWER(p.market) = LOWER(p_market)
    AND p.is_active = true
    AND rh.stay_date::date BETWEEN p_start_date AND p_end_date;
$function$;

GRANT EXECUTE ON FUNCTION public.get_market_snapshot(text, date, date) TO anon, authenticated, service_role;


-- ============================================================================
-- 2. get_market_averages
-- ============================================================================
-- Dropping previous TABLE(...) signature to avoid PostgreSQL Error 42P13
DROP FUNCTION IF EXISTS public.get_market_averages(text);
DROP FUNCTION IF EXISTS public.get_market_averages();

CREATE OR REPLACE FUNCTION public.get_market_averages(
  market_param text DEFAULT NULL::text
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  WITH latest_props AS (
    SELECT DISTINCT ON (property_id)
      property_id,
      market,
      nightly_rate,
      trailing_avg_rate,
      is_available,
      is_active
    FROM public.v_rate_volatility
    WHERE (market_param IS NULL OR LOWER(market) = LOWER(market_param))
      AND is_active = true
      AND nightly_rate IS NOT NULL
    ORDER BY property_id, recorded_at DESC
  )
  SELECT json_build_object(
    'status', 'success',
    'market', COALESCE(market_param, 'All Markets'),
    'active_properties_with_rates', COUNT(*),
    'avg_nightly_rate', ROUND(AVG(nightly_rate)::numeric, 2),
    'avg_trailing_7d_rate', ROUND(AVG(trailing_avg_rate)::numeric, 2),
    'min_nightly_rate', ROUND(MIN(nightly_rate)::numeric, 2),
    'max_nightly_rate', ROUND(MAX(nightly_rate)::numeric, 2)
  )
  FROM latest_props;
$function$;

GRANT EXECUTE ON FUNCTION public.get_market_averages(text) TO anon, authenticated, service_role;


-- ============================================================================
-- 3. search_properties
-- ============================================================================
-- Dropping previous TABLE(...) signature and overloads
DROP FUNCTION IF EXISTS public.search_properties(text, text, text, integer, boolean, integer);
DROP FUNCTION IF EXISTS public.search_properties(text, text, text, integer, boolean, boolean, text, text, integer);

CREATE OR REPLACE FUNCTION public.search_properties(
  p_search text DEFAULT NULL::text,
  p_market text DEFAULT NULL::text,
  p_platform text DEFAULT NULL::text,
  p_bedrooms integer DEFAULT NULL::integer,
  p_available boolean DEFAULT NULL::boolean,
  p_is_active boolean DEFAULT true,
  p_rank_position text DEFAULT 'top'::text,
  p_sort_by text DEFAULT 'rate'::text,
  p_limit integer DEFAULT 6
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  WITH latest_state AS (
    SELECT DISTINCT ON (rvv.property_id)
      rvv.property_id,
      rvv.property_name,
      rvv.market,
      rvv.platform,
      rvv.bedrooms,
      ROUND(rvv.avg_rating::numeric, 2) AS avg_rating,
      rvv.review_count,
      ROUND(rvv.nightly_rate::numeric, 2) AS nightly_rate,
      ROUND(rvv.pct_above_trailing_avg::numeric, 2) AS pct_above_trailing_avg,
      rvv.is_available,
      rvv.is_active,
      rvv.recorded_at
    FROM public.v_rate_volatility rvv
    WHERE
      (p_market IS NULL OR LOWER(rvv.market) = LOWER(p_market))
      AND (p_platform IS NULL OR LOWER(rvv.platform) = LOWER(p_platform))
      AND (p_bedrooms IS NULL OR rvv.bedrooms = p_bedrooms)
      AND (
        p_search IS NULL
        OR rvv.property_name ILIKE '%' || p_search || '%'
        OR rvv.property_id::text = p_search
      )
    ORDER BY rvv.property_id, rvv.recorded_at DESC
  ),
  filtered AS (
    SELECT *
    FROM latest_state
    WHERE (p_available IS NULL OR is_available = p_available)
      AND (p_is_active IS NULL OR is_active = p_is_active)
  ),
  ranked AS (
    SELECT
      *,
      COUNT(*) OVER () AS total_matching,
      ROW_NUMBER() OVER (
        ORDER BY
          CASE WHEN p_sort_by = 'deviation' THEN ABS(COALESCE(pct_above_trailing_avg, 0)) END DESC NULLS LAST,
          CASE WHEN p_sort_by = 'rate' THEN nightly_rate END DESC NULLS LAST,
          property_name ASC
      ) AS rank_asc,
      ROW_NUMBER() OVER (
        ORDER BY
          CASE WHEN p_sort_by = 'deviation' THEN ABS(COALESCE(pct_above_trailing_avg, 0)) END ASC NULLS LAST,
          CASE WHEN p_sort_by = 'rate' THEN nightly_rate END ASC NULLS LAST,
          property_name DESC
      ) AS rank_desc
    FROM filtered
  ),
  sliced AS (
    SELECT
      property_id,
      property_name,
      market,
      platform,
      bedrooms,
      avg_rating,
      review_count,
      nightly_rate,
      pct_above_trailing_avg,
      is_available,
      is_active,
      recorded_at,
      total_matching
    FROM ranked
    WHERE
      CASE
        WHEN LOWER(p_rank_position) = 'bottom' THEN
          rank_desc <= LEAST(GREATEST(p_limit, 1), 20)
        WHEN LOWER(p_rank_position) = 'middle' THEN
          rank_asc > GREATEST(0, (total_matching - LEAST(GREATEST(p_limit, 1), 20)) / 2)
          AND rank_asc <= GREATEST(0, (total_matching - LEAST(GREATEST(p_limit, 1), 20)) / 2) + LEAST(GREATEST(p_limit, 1), 20)
        ELSE -- 'top'
          rank_asc <= LEAST(GREATEST(p_limit, 1), 20)
      END
    ORDER BY
      CASE WHEN LOWER(p_rank_position) = 'bottom' THEN rank_desc ELSE rank_asc END ASC
  )
  SELECT json_build_object(
    'status', 'success',
    'total_matching_count', COALESCE((SELECT MAX(total_matching) FROM sliced), (SELECT COUNT(*) FROM filtered)),
    'returned_count', (SELECT COUNT(*) FROM sliced),
    'rank_position', LOWER(COALESCE(p_rank_position, 'top')),
    'items', COALESCE(
      json_agg(
        json_build_object(
          'property_id', property_id,
          'property_name', property_name,
          'market', market,
          'platform', platform,
          'bedrooms', bedrooms,
          'avg_rating', avg_rating,
          'review_count', review_count,
          'nightly_rate', nightly_rate,
          'pct_above_trailing_avg', pct_above_trailing_avg,
          'is_available', is_available,
          'is_active', is_active,
          'recorded_at', recorded_at
        )
      ), '[]'::json
    )
  )
  FROM sliced;
$function$;

GRANT EXECUTE ON FUNCTION public.search_properties(text, text, text, integer, boolean, boolean, text, text, integer) TO anon, authenticated, service_role;


-- ============================================================================
-- 4. get_property_rate_changes
-- ============================================================================
-- Dropping previous TABLE(...) signatures and 3-parameter overload
DROP FUNCTION IF EXISTS public.get_property_rate_changes(text, integer, integer);
DROP FUNCTION IF EXISTS public.get_property_rate_changes(text, integer, integer, date, date);
DROP FUNCTION IF EXISTS public.get_property_rate_changes(text, integer, integer, date, date, integer);

CREATE OR REPLACE FUNCTION public.get_property_rate_changes(
  property_search text,
  days_param integer DEFAULT 14,
  compare_window_days integer DEFAULT 1,
  start_date date DEFAULT NULL::date,
  end_date date DEFAULT NULL::date,
  p_limit integer DEFAULT 14
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  WITH target_prop AS (
    SELECT id, name, market, platform, is_active
    FROM public.properties
    WHERE (id::text = property_search OR LOWER(name) = LOWER(property_search) OR LOWER(name) LIKE '%' || LOWER(property_search) || '%')
    LIMIT 1
  ),
  daily_dedup AS (
    SELECT DISTINCT ON (rh.stay_date::date)
      rh.property_id,
      tp.name AS property_name,
      tp.market,
      tp.platform,
      tp.is_active,
      rh.stay_date::date AS stay_date,
      ROUND(rh.nightly_rate::numeric, 2) AS nightly_rate,
      ROUND(rh.trailing_avg_rate::numeric, 2) AS trailing_avg_rate,
      ROUND(rh.pct_above_trailing_avg::numeric, 2) AS pct_above_trailing_avg,
      rh.recorded_at
    FROM public.v_rate_volatility rh
    JOIN target_prop tp ON tp.id = rh.property_id
    WHERE
      (
        (start_date IS NOT NULL AND end_date IS NOT NULL AND rh.stay_date::date BETWEEN start_date AND end_date)
        OR (start_date IS NULL AND rh.recorded_at >= (NOW() - (days_param || ' days')::interval))
      )
    ORDER BY rh.stay_date::date, rh.recorded_at DESC
  ),
  with_prev AS (
    SELECT
      *,
      LAG(nightly_rate, compare_window_days) OVER (ORDER BY stay_date ASC) AS prev_nightly_rate
    FROM daily_dedup
  ),
  formatted AS (
    SELECT
      property_id,
      property_name,
      market,
      platform,
      is_active,
      stay_date,
      nightly_rate,
      trailing_avg_rate,
      pct_above_trailing_avg,
      ROUND(prev_nightly_rate::numeric, 2) AS prev_nightly_rate,
      ROUND(
        CASE
          WHEN prev_nightly_rate IS NULL OR prev_nightly_rate = 0 OR nightly_rate IS NULL THEN NULL
          ELSE ((nightly_rate - prev_nightly_rate) / prev_nightly_rate) * 100.0
        END::numeric, 2
      ) AS pct_change_vs_prev,
      COUNT(*) OVER () AS total_matching
    FROM with_prev
    ORDER BY stay_date DESC
    LIMIT LEAST(GREATEST(p_limit, 1), 30)
  )
  SELECT json_build_object(
    'status', 'success',
    'property_id', (SELECT id FROM target_prop),
    'property_name', (SELECT name FROM target_prop),
    'is_active', (SELECT is_active FROM target_prop),
    'total_matching_count', COALESCE((SELECT MAX(total_matching) FROM formatted), 0),
    'returned_count', (SELECT COUNT(*) FROM formatted),
    'items', COALESCE(
      json_agg(
        json_build_object(
          'stay_date', stay_date,
          'nightly_rate', nightly_rate,
          'trailing_avg_rate', trailing_avg_rate,
          'pct_above_trailing_avg', pct_above_trailing_avg,
          'prev_nightly_rate', prev_nightly_rate,
          'pct_change_vs_prev', pct_change_vs_prev
        )
        ORDER BY stay_date ASC
      ), '[]'::json
    )
  )
  FROM formatted;
$function$;

GRANT EXECUTE ON FUNCTION public.get_property_rate_changes(text, integer, integer, date, date, integer) TO anon, authenticated, service_role;


-- ============================================================================
-- 5. get_market_trend
-- ============================================================================
-- Dropping previous signature without p_platform / p_is_active
DROP FUNCTION IF EXISTS public.get_market_trend(text, integer);
DROP FUNCTION IF EXISTS public.get_market_trend(text, integer, text, boolean);

CREATE OR REPLACE FUNCTION public.get_market_trend(
  p_market text,
  p_days integer DEFAULT 14,
  p_platform text DEFAULT NULL::text,
  p_is_active boolean DEFAULT true
)
RETURNS json
LANGUAGE plpgsql
STABLE SECURITY DEFINER
AS $function$
DECLARE
  v_midpoint timestamp with time zone;
  v_early_avg numeric;
  v_recent_avg numeric;
  v_pct_change numeric;
  v_direction text;
  v_active_count integer;
BEGIN
  v_midpoint := NOW() - ((p_days / 2) || ' days')::interval;

  -- Early period average
  SELECT ROUND(AVG(rh.nightly_rate)::numeric, 2)
  INTO v_early_avg
  FROM public.rate_history rh
  JOIN public.properties p ON p.id = rh.property_id
  WHERE LOWER(p.market) = LOWER(p_market)
    AND (p_platform IS NULL OR LOWER(p.platform) = LOWER(p_platform))
    AND (p_is_active IS NULL OR p.is_active = p_is_active)
    AND rh.nightly_rate IS NOT NULL
    AND rh.stay_date < v_midpoint
    AND rh.stay_date >= NOW() - (p_days || ' days')::interval;

  -- Recent period average
  SELECT ROUND(AVG(rh.nightly_rate)::numeric, 2)
  INTO v_recent_avg
  FROM public.rate_history rh
  JOIN public.properties p ON p.id = rh.property_id
  WHERE LOWER(p.market) = LOWER(p_market)
    AND (p_platform IS NULL OR LOWER(p.platform) = LOWER(p_platform))
    AND (p_is_active IS NULL OR p.is_active = p_is_active)
    AND rh.nightly_rate IS NOT NULL
    AND rh.stay_date >= v_midpoint;

  SELECT COUNT(DISTINCT p.id)
  INTO v_active_count
  FROM public.properties p
  WHERE LOWER(p.market) = LOWER(p_market)
    AND (p_platform IS NULL OR LOWER(p.platform) = LOWER(p_platform))
    AND (p_is_active IS NULL OR p.is_active = p_is_active);

  IF v_early_avg IS NULL OR v_recent_avg IS NULL OR v_early_avg = 0 THEN
    v_pct_change := 0.0;
    v_direction := 'INSUFFICIENT_DATA';
  ELSE
    v_pct_change := ROUND(((v_recent_avg - v_early_avg) / v_early_avg * 100.0)::numeric, 2);
    IF v_pct_change > 1.0 THEN
      v_direction := 'UP';
    ELSIF v_pct_change < -1.0 THEN
      v_direction := 'DOWN';
    ELSE
      v_direction := 'FLAT';
    END IF;
  END IF;

  RETURN json_build_object(
    'status', 'success',
    'market', p_market,
    'platform', p_platform,
    'properties_analyzed', v_active_count,
    'period_days', p_days,
    'early_period_avg', v_early_avg,
    'recent_period_avg', v_recent_avg,
    'pct_change', v_pct_change,
    'trend_direction', v_direction
  );
END;
$function$;

GRANT EXECUTE ON FUNCTION public.get_market_trend(text, integer, text, boolean) TO anon, authenticated, service_role;


-- ============================================================================
-- 6. get_rate_anomaly_report
-- ============================================================================
DROP FUNCTION IF EXISTS public.get_rate_anomaly_report(text, integer, double precision);

CREATE OR REPLACE FUNCTION public.get_rate_anomaly_report(
  p_property_search text,
  p_days integer DEFAULT 30,
  p_deviation_threshold double precision DEFAULT 25.0
)
RETURNS json
LANGUAGE plpgsql
STABLE SECURITY DEFINER
AS $function$
DECLARE
  v_property_id uuid;
  v_property_name text;
  v_market text;
  v_platform text;
  v_is_active boolean;
  v_total_readings integer;
  v_anomaly_count integer;
  v_normal_min numeric;
  v_normal_max numeric;
  v_recent_anomalies json;
  v_trailing_avg numeric;
BEGIN
  -- Resolve property
  SELECT id, name, market, platform, is_active
  INTO v_property_id, v_property_name, v_market, v_platform, v_is_active
  FROM public.properties
  WHERE id::text = p_property_search
     OR LOWER(name) = LOWER(p_property_search)
     OR LOWER(name) LIKE '%' || LOWER(p_property_search) || '%'
  LIMIT 1;

  IF v_property_id IS NULL THEN
    RETURN json_build_object('status', 'error', 'message', 'Property not found: ' || p_property_search);
  END IF;

  -- Total observations in window
  SELECT COUNT(*), ROUND(AVG(nightly_rate)::numeric, 2)
  INTO v_total_readings, v_trailing_avg
  FROM public.rate_history
  WHERE property_id = v_property_id
    AND created_at >= NOW() - (p_days || ' days')::interval
    AND nightly_rate IS NOT NULL;

  -- Anomalous rows (join on v.id = rh.id)
  SELECT COUNT(*)
  INTO v_anomaly_count
  FROM public.rate_history rh
  JOIN public.v_rate_volatility v ON v.id = rh.id
  WHERE rh.property_id = v_property_id
    AND rh.created_at >= NOW() - (p_days || ' days')::interval
    AND rh.nightly_rate IS NOT NULL
    AND ABS(v.pct_above_trailing_avg) >= p_deviation_threshold;

  -- Normal rate baseline (explicitly qualified rh.nightly_rate)
  SELECT
    ROUND(MIN(rh.nightly_rate)::numeric, 2),
    ROUND(MAX(rh.nightly_rate)::numeric, 2)
  INTO v_normal_min, v_normal_max
  FROM public.rate_history rh
  JOIN public.v_rate_volatility v ON v.id = rh.id
  WHERE rh.property_id = v_property_id
    AND rh.created_at >= NOW() - (p_days || ' days')::interval
    AND rh.nightly_rate IS NOT NULL
    AND ABS(v.pct_above_trailing_avg) < p_deviation_threshold;

  -- Most recent 5 anomalies
  SELECT json_agg(r)
  INTO v_recent_anomalies
  FROM (
    SELECT
      rh.stay_date::date AS stay_date,
      ROUND(rh.nightly_rate::numeric, 2) AS nightly_rate,
      ROUND(v.trailing_avg_rate::numeric, 2) AS trailing_avg_rate,
      ROUND(v.pct_above_trailing_avg::numeric, 2) AS pct_above_trailing_avg,
      rh.created_at AS recorded_at
    FROM public.rate_history rh
    JOIN public.v_rate_volatility v ON v.id = rh.id
    WHERE rh.property_id = v_property_id
      AND rh.created_at >= NOW() - (p_days || ' days')::interval
      AND rh.nightly_rate IS NOT NULL
      AND ABS(v.pct_above_trailing_avg) >= p_deviation_threshold
    ORDER BY rh.created_at DESC
    LIMIT 5
  ) r;

  RETURN json_build_object(
    'status', 'success',
    'property_id', v_property_id,
    'property_name', v_property_name,
    'market', v_market,
    'platform', v_platform,
    'is_active', v_is_active,
    'lookback_days', p_days,
    'deviation_threshold_pct', p_deviation_threshold,
    'baseline_avg_rate', v_trailing_avg,
    'normal_rate_range', json_build_object('min', v_normal_min, 'max', v_normal_max),
    'total_observations', v_total_readings,
    'anomaly_count', v_anomaly_count,
    'anomaly_rate_pct', ROUND((v_anomaly_count::numeric / NULLIF(v_total_readings, 0) * 100.0), 2),
    'recent_anomalies', COALESCE(v_recent_anomalies, '[]'::json)
  );
END;
$function$;

GRANT EXECUTE ON FUNCTION public.get_rate_anomaly_report(text, integer, double precision) TO anon, authenticated, service_role;


-- ============================================================================
-- 7. compare_properties
-- ============================================================================
DROP FUNCTION IF EXISTS public.compare_properties(text[]);

CREATE OR REPLACE FUNCTION public.compare_properties(
  p_property_ids text[]
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  SELECT json_build_object(
    'status', 'success',
    'compared_count', (SELECT COUNT(*) FROM (
      SELECT DISTINCT ON (rvv.property_id) rvv.property_id
      FROM public.v_rate_volatility rvv
      WHERE rvv.property_id::text = ANY(p_property_ids)
         OR rvv.property_name ILIKE ANY(
             SELECT '%' || unnest || '%' FROM unnest(p_property_ids)
           )
      ORDER BY rvv.property_id, rvv.recorded_at DESC
      LIMIT 5
    ) c),
    'compared', COALESCE(
      json_agg(
        json_build_object(
          'property_id', v.property_id,
          'property_name', v.property_name,
          'market', v.market,
          'platform', v.platform,
          'bedrooms', v.bedrooms,
          'is_active', v.is_active,
          'current_rate', ROUND(v.nightly_rate::numeric, 2),
          'trailing_7d_avg', ROUND(v.trailing_avg_rate::numeric, 2),
          'pct_above_trailing_avg', ROUND(v.pct_above_trailing_avg::numeric, 2),
          'is_available', v.is_available,
          'avg_rating', ROUND(v.avg_rating::numeric, 2),
          'review_count', v.review_count,
          'last_recorded', v.recorded_at
        )
        ORDER BY v.property_name
      ), '[]'::json
    )
  )
  FROM (
    SELECT DISTINCT ON (rvv.property_id) rvv.*
    FROM public.v_rate_volatility rvv
    WHERE rvv.property_id::text = ANY(p_property_ids)
       OR rvv.property_name ILIKE ANY(
           SELECT '%' || unnest || '%' FROM unnest(p_property_ids)
         )
    ORDER BY rvv.property_id, rvv.recorded_at DESC
    LIMIT 5
  ) v;
$function$;

GRANT EXECUTE ON FUNCTION public.compare_properties(text[]) TO anon, authenticated, service_role;


-- ============================================================================
-- 8. get_spike_alerts
-- ============================================================================
-- Dropping previous TABLE(...) signature and overloads
DROP FUNCTION IF EXISTS public.get_spike_alerts(numeric, integer);
DROP FUNCTION IF EXISTS public.get_spike_alerts(numeric, integer, text, integer, text);

CREATE OR REPLACE FUNCTION public.get_spike_alerts(
  threshold_param numeric DEFAULT 25.0,
  days_param integer DEFAULT 7,
  p_market text DEFAULT NULL::text,
  p_limit integer DEFAULT 8,
  p_rank_position text DEFAULT 'top'::text
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  WITH all_spikes AS (
    SELECT
      property_id,
      property_name,
      market,
      platform,
      is_active,
      stay_date::date AS stay_date,
      ROUND(nightly_rate::numeric, 2) AS nightly_rate,
      ROUND(trailing_avg_rate::numeric, 2) AS trailing_avg_rate,
      ROUND(pct_above_trailing_avg::numeric, 2) AS pct_above_trailing_avg,
      recorded_at
    FROM public.v_rate_volatility
    WHERE ABS(pct_above_trailing_avg) >= threshold_param
      AND recorded_at >= NOW() - (days_param || ' days')::interval
      AND (p_market IS NULL OR LOWER(market) = LOWER(p_market))
  ),
  ranked AS (
    SELECT
      *,
      COUNT(*) OVER () AS total_matching,
      ROW_NUMBER() OVER (ORDER BY ABS(pct_above_trailing_avg) DESC) AS rank_asc,
      ROW_NUMBER() OVER (ORDER BY ABS(pct_above_trailing_avg) ASC) AS rank_desc
    FROM all_spikes
  ),
  sliced AS (
    SELECT
      property_id,
      property_name,
      market,
      platform,
      is_active,
      stay_date,
      nightly_rate,
      trailing_avg_rate,
      pct_above_trailing_avg,
      recorded_at,
      total_matching
    FROM ranked
    WHERE
      CASE
        WHEN LOWER(p_rank_position) = 'bottom' THEN
          rank_desc <= LEAST(GREATEST(p_limit, 1), 15)
        WHEN LOWER(p_rank_position) = 'middle' THEN
          rank_asc > GREATEST(0, (total_matching - LEAST(GREATEST(p_limit, 1), 15)) / 2)
          AND rank_asc <= GREATEST(0, (total_matching - LEAST(GREATEST(p_limit, 1), 15)) / 2) + LEAST(GREATEST(p_limit, 1), 15)
        ELSE -- 'top'
          rank_asc <= LEAST(GREATEST(p_limit, 1), 15)
      END
    ORDER BY
      CASE WHEN LOWER(p_rank_position) = 'bottom' THEN rank_desc ELSE rank_asc END ASC
  )
  SELECT json_build_object(
    'status', 'success',
    'total_matching_count', COALESCE((SELECT MAX(total_matching) FROM sliced), (SELECT COUNT(*) FROM all_spikes)),
    'returned_count', (SELECT COUNT(*) FROM sliced),
    'rank_position', LOWER(COALESCE(p_rank_position, 'top')),
    'items', COALESCE(
      json_agg(
        json_build_object(
          'property_id', property_id,
          'property_name', property_name,
          'market', market,
          'platform', platform,
          'is_active', is_active,
          'stay_date', stay_date,
          'nightly_rate', nightly_rate,
          'trailing_avg_rate', trailing_avg_rate,
          'pct_above_trailing_avg', pct_above_trailing_avg,
          'recorded_at', recorded_at
        )
      ), '[]'::json
    )
  )
  FROM sliced;
$function$;

GRANT EXECUTE ON FUNCTION public.get_spike_alerts(numeric, integer, text, integer, text) TO anon, authenticated, service_role;


-- ============================================================================
-- 9. get_most_volatile_properties
-- ============================================================================
DROP FUNCTION IF EXISTS public.get_most_volatile_properties(text, integer, integer);
DROP FUNCTION IF EXISTS public.get_most_volatile_properties(text, integer, integer, text);

CREATE OR REPLACE FUNCTION public.get_most_volatile_properties(
  p_market text DEFAULT NULL::text,
  p_days integer DEFAULT 14,
  p_limit integer DEFAULT 5,
  p_rank_position text DEFAULT 'top'::text
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  WITH property_vol AS (
    SELECT
      v.property_id,
      v.property_name,
      v.market,
      v.platform,
      v.is_active,
      ROUND(STDDEV(v.nightly_rate)::numeric, 2) AS rate_stddev,
      COUNT(CASE WHEN ABS(v.pct_above_trailing_avg) >= 25 THEN 1 END) AS spike_count,
      ROUND(AVG(v.nightly_rate)::numeric, 2) AS avg_rate,
      ROUND(MIN(v.nightly_rate)::numeric, 2) AS min_rate,
      ROUND(MAX(v.nightly_rate)::numeric, 2) AS max_rate,
      COUNT(*) AS observation_count
    FROM public.v_rate_volatility v
    WHERE v.recorded_at >= NOW() - (p_days || ' days')::interval
      AND v.nightly_rate IS NOT NULL
      AND (p_market IS NULL OR LOWER(v.market) = LOWER(p_market))
    GROUP BY v.property_id, v.property_name, v.market, v.platform, v.is_active
    HAVING COUNT(*) >= 2
  ),
  ranked AS (
    SELECT
      *,
      COUNT(*) OVER () AS total_matching,
      ROW_NUMBER() OVER (ORDER BY COALESCE(rate_stddev, 0) DESC, spike_count DESC) AS rank_asc,
      ROW_NUMBER() OVER (ORDER BY COALESCE(rate_stddev, 0) ASC, spike_count ASC) AS rank_desc
    FROM property_vol
  ),
  sliced AS (
    SELECT
      property_id,
      property_name,
      market,
      platform,
      is_active,
      rate_stddev,
      spike_count,
      avg_rate,
      min_rate,
      max_rate,
      observation_count,
      total_matching
    FROM ranked
    WHERE
      CASE
        WHEN LOWER(p_rank_position) = 'bottom' THEN
          rank_desc <= LEAST(GREATEST(p_limit, 1), 15)
        WHEN LOWER(p_rank_position) = 'middle' THEN
          rank_asc > GREATEST(0, (total_matching - LEAST(GREATEST(p_limit, 1), 15)) / 2)
          AND rank_asc <= GREATEST(0, (total_matching - LEAST(GREATEST(p_limit, 1), 15)) / 2) + LEAST(GREATEST(p_limit, 1), 15)
        ELSE -- 'top'
          rank_asc <= LEAST(GREATEST(p_limit, 1), 15)
      END
    ORDER BY
      CASE WHEN LOWER(p_rank_position) = 'bottom' THEN rank_desc ELSE rank_asc END ASC
  )
  SELECT json_build_object(
    'status', 'success',
    'market_filter', p_market,
    'period_days', p_days,
    'total_matching_count', COALESCE((SELECT MAX(total_matching) FROM sliced), (SELECT COUNT(*) FROM property_vol)),
    'returned_count', (SELECT COUNT(*) FROM sliced),
    'rank_position', LOWER(COALESCE(p_rank_position, 'top')),
    'items', COALESCE(
      json_agg(
        json_build_object(
          'property_id', property_id,
          'property_name', property_name,
          'market', market,
          'platform', platform,
          'is_active', is_active,
          'rate_stddev', rate_stddev,
          'spike_count', spike_count,
          'avg_rate', avg_rate,
          'min_rate', min_rate,
          'max_rate', max_rate,
          'observation_count', observation_count
        )
      ), '[]'::json
    )
  )
  FROM sliced;
$function$;

GRANT EXECUTE ON FUNCTION public.get_most_volatile_properties(text, integer, integer, text) TO anon, authenticated, service_role;


-- ============================================================================
-- 10. get_property_snapshot
-- ============================================================================
DROP FUNCTION IF EXISTS public.get_property_snapshot(text);

CREATE OR REPLACE FUNCTION public.get_property_snapshot(
  p_property_search text
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  SELECT json_build_object(
    'status', 'success',
    'property_id', v.property_id,
    'property_name', v.property_name,
    'market', v.market,
    'platform', v.platform,
    'bedrooms', v.bedrooms,
    'is_active', v.is_active,
    'current_rate', ROUND(v.nightly_rate::numeric, 2),
    'trailing_7d_avg', ROUND(v.trailing_avg_rate::numeric, 2),
    'pct_above_trailing_avg', ROUND(v.pct_above_trailing_avg::numeric, 2),
    'is_available', v.is_available,
    'avg_rating', ROUND(v.avg_rating::numeric, 2),
    'review_count', v.review_count,
    'listing_url', v.url,
    'coordinates', json_build_object('lat', v.latitude, 'lng', v.longitude),
    'last_recorded', v.recorded_at
  )
  FROM (
    SELECT DISTINCT ON (rvv.property_id) rvv.*
    FROM public.v_rate_volatility rvv
    WHERE rvv.property_id::text = p_property_search
       OR LOWER(rvv.property_name) = LOWER(p_property_search)
       OR LOWER(rvv.property_name) LIKE '%' || LOWER(p_property_search) || '%'
    ORDER BY rvv.property_id, rvv.recorded_at DESC
    LIMIT 1
  ) v;
$function$;

GRANT EXECUTE ON FUNCTION public.get_property_snapshot(text) TO anon, authenticated, service_role;


-- ============================================================================
-- 11. get_nearby_properties
-- ============================================================================
DROP FUNCTION IF EXISTS public.get_nearby_properties(numeric, numeric, numeric, integer);

CREATE OR REPLACE FUNCTION public.get_nearby_properties(
  p_latitude numeric,
  p_longitude numeric,
  p_radius_km numeric DEFAULT 5.0,
  p_limit integer DEFAULT 6
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  WITH calculated AS (
    SELECT DISTINCT ON (rvv.property_id)
      rvv.property_id,
      rvv.property_name,
      rvv.market,
      rvv.platform,
      rvv.bedrooms,
      rvv.is_active,
      ROUND(rvv.nightly_rate::numeric, 2) AS nightly_rate,
      ROUND(rvv.avg_rating::numeric, 2) AS avg_rating,
      rvv.review_count,
      rvv.is_available,
      ROUND((
        6371.0 * acos(
          LEAST(1.0, GREATEST(-1.0,
            cos(radians(p_latitude))
            * cos(radians(rvv.latitude))
            * cos(radians(rvv.longitude) - radians(p_longitude))
            + sin(radians(p_latitude))
            * sin(radians(rvv.latitude))
          ))
        )
      )::numeric, 2) AS distance_km
    FROM public.v_rate_volatility rvv
    WHERE rvv.latitude IS NOT NULL
      AND rvv.longitude IS NOT NULL
    ORDER BY rvv.property_id, rvv.recorded_at DESC
  ),
  within_radius AS (
    SELECT *, COUNT(*) OVER () AS total_matching
    FROM calculated
    WHERE distance_km <= p_radius_km
    ORDER BY distance_km ASC
    LIMIT LEAST(GREATEST(p_limit, 1), 20)
  )
  SELECT json_build_object(
    'status', 'success',
    'search_center', json_build_object('lat', p_latitude, 'lng', p_longitude),
    'radius_km', p_radius_km,
    'total_matching_count', COALESCE((SELECT MAX(total_matching) FROM within_radius), 0),
    'returned_count', (SELECT COUNT(*) FROM within_radius),
    'items', COALESCE(
      json_agg(
        json_build_object(
          'property_id', property_id,
          'property_name', property_name,
          'market', market,
          'platform', platform,
          'bedrooms', bedrooms,
          'is_active', is_active,
          'nightly_rate', nightly_rate,
          'is_available', is_available,
          'avg_rating', avg_rating,
          'review_count', review_count,
          'distance_km', distance_km
        )
        ORDER BY distance_km ASC
      ), '[]'::json
    )
  )
  FROM within_radius;
$function$;

GRANT EXECUTE ON FUNCTION public.get_nearby_properties(numeric, numeric, numeric, integer) TO anon, authenticated, service_role;


-- ============================================================================
-- 12. get_recently_changed_tracking
-- ============================================================================
DROP FUNCTION IF EXISTS public.get_recently_changed_tracking(integer);

CREATE OR REPLACE FUNCTION public.get_recently_changed_tracking(
  p_days integer DEFAULT 30
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  WITH newly_added AS (
    SELECT
      id AS property_id,
      name AS property_name,
      market,
      platform,
      is_active,
      created_at AS tracking_started_at
    FROM public.properties
    WHERE created_at >= NOW() - (p_days || ' days')::interval
    ORDER BY created_at DESC
    LIMIT 10
  ),
  untracked_props AS (
    SELECT
      id AS property_id,
      name AS property_name,
      market,
      platform,
      is_active,
      updated_at AS status_changed_at
    FROM public.properties
    WHERE is_active = false
      AND updated_at >= NOW() - (p_days || ' days')::interval
    ORDER BY updated_at DESC
    LIMIT 10
  )
  SELECT json_build_object(
    'status', 'success',
    'period_days', p_days,
    'newly_added_count', (SELECT COUNT(*) FROM newly_added),
    'newly_added', COALESCE((SELECT json_agg(na) FROM newly_added na), '[]'::json),
    'untracked_count', (SELECT COUNT(*) FROM untracked_props),
    'untracked_or_removed', COALESCE((SELECT json_agg(up) FROM untracked_props up), '[]'::json)
  );
$function$;

GRANT EXECUTE ON FUNCTION public.get_recently_changed_tracking(integer) TO anon, authenticated, service_role;


-- ============================================================================
-- 13. get_real_estate_kpis (NEW)
-- ============================================================================
DROP FUNCTION IF EXISTS public.get_real_estate_kpis(text, text, integer, boolean, uuid[], date, date);

CREATE OR REPLACE FUNCTION public.get_real_estate_kpis(
  p_market        TEXT     DEFAULT NULL,
  p_platform      TEXT     DEFAULT NULL,
  p_bedrooms      INTEGER  DEFAULT NULL,
  p_is_active     BOOLEAN  DEFAULT true,
  p_property_ids  UUID[]   DEFAULT NULL,
  p_start_date    DATE     DEFAULT NULL,
  p_end_date      DATE     DEFAULT NULL
)
RETURNS json
LANGUAGE sql
SECURITY DEFINER
AS $function$
  WITH filtered_props AS (
    SELECT DISTINCT ON (property_id)
      property_id,
      market,
      platform,
      bedrooms,
      is_active,
      is_available,
      stay_date
    FROM public.v_rate_volatility
    WHERE (p_market       IS NULL OR market      = p_market)
      AND (p_platform     IS NULL OR platform    = p_platform)
      AND (p_bedrooms     IS NULL OR bedrooms    = p_bedrooms)
      AND (p_is_active    IS NULL OR is_active   = p_is_active)
      AND (p_property_ids IS NULL OR property_id = ANY(p_property_ids))
      AND (p_start_date   IS NULL OR stay_date  >= p_start_date)
      AND (p_end_date     IS NULL OR stay_date  <= p_end_date)
    ORDER BY property_id, recorded_at DESC
  )
  SELECT json_build_object(
    'status', 'success',
    'domain', 'real_estate',
    'filters_applied', json_build_object(
      'market', p_market,
      'platform', p_platform,
      'bedrooms', p_bedrooms,
      'is_active', p_is_active,
      'start_date', p_start_date,
      'end_date', p_end_date
    ),
    'properties_tracked', (SELECT COUNT(*) FROM filtered_props),
    'available_count', (SELECT COUNT(*) FILTER (WHERE is_available = true) FROM filtered_props),
    'unavailable_count', (SELECT COUNT(*) FILTER (WHERE is_available = false) FROM filtered_props),
    'availability_rate_pct', (
      SELECT ROUND(
        100.0 * COUNT(*) FILTER (WHERE is_available = true)
        / NULLIF(COUNT(*), 0), 2
      )
      FROM filtered_props
    ),
    'rate_changes_7d', (
      SELECT COUNT(*)
      FROM public.rate_history rh
      JOIN public.properties p ON p.id = rh.property_id
      WHERE rh.created_at >= NOW() - INTERVAL '7 days'
        AND rh.nightly_rate IS NOT NULL
        AND rh.nightly_rate != (
          SELECT nightly_rate FROM public.rate_history rh2
          WHERE rh2.property_id = rh.property_id
            AND rh2.created_at  < rh.created_at
            AND rh2.nightly_rate IS NOT NULL
          ORDER BY rh2.created_at DESC LIMIT 1
        )
        AND (p_market       IS NULL OR p.market    = p_market)
        AND (p_platform     IS NULL OR p.platform  = p_platform)
        AND (p_bedrooms     IS NULL OR p.bedrooms  = p_bedrooms)
        AND (p_is_active    IS NULL OR p.is_active = p_is_active)
        AND (p_property_ids IS NULL OR p.id        = ANY(p_property_ids))
    ),
    'spikes_7d', (
      SELECT COUNT(*)
      FROM public.v_rate_volatility
      WHERE ABS(pct_above_trailing_avg) >= 25
        AND recorded_at >= NOW() - INTERVAL '7 days'
        AND (p_market       IS NULL OR market      = p_market)
        AND (p_platform     IS NULL OR platform    = p_platform)
        AND (p_bedrooms     IS NULL OR bedrooms    = p_bedrooms)
        AND (p_is_active    IS NULL OR is_active   = p_is_active)
        AND (p_property_ids IS NULL OR property_id = ANY(p_property_ids))
        AND (p_start_date   IS NULL OR stay_date  >= p_start_date)
        AND (p_end_date     IS NULL OR stay_date  <= p_end_date)
    ),
    'tracking_since', (
      SELECT MIN(created_at) FROM public.properties
      WHERE (p_market IS NULL OR market = p_market)
    ),
    'scrape_health', (
      SELECT COALESCE(json_object_agg(platform, last_status), '{}'::json)
      FROM public.v_scrape_health
      WHERE job_type = 'REAL_ESTATE_MONITOR'
    )
  );
$function$;

GRANT EXECUTE ON FUNCTION public.get_real_estate_kpis(text, text, integer, boolean, uuid[], date, date) TO anon, authenticated, service_role;


-- ============================================================================
-- 14. get_property_detail (NEW)
-- ============================================================================
DROP FUNCTION IF EXISTS public.get_property_detail(text, integer);

CREATE OR REPLACE FUNCTION public.get_property_detail(
  p_property_search text,
  p_history_days integer DEFAULT 14
)
RETURNS json
LANGUAGE sql
STABLE SECURITY DEFINER
AS $function$
  WITH prop AS (
    SELECT
      id, name, market, platform, bedrooms,
      latitude, longitude, url, is_active, created_at
    FROM public.properties
    WHERE id::text = p_property_search
       OR LOWER(name) = LOWER(p_property_search)
       OR LOWER(name) LIKE '%' || LOWER(p_property_search) || '%'
    LIMIT 1
  ),
  latest_reading AS (
    SELECT DISTINCT ON (property_id)
      nightly_rate,
      trailing_avg_rate,
      pct_above_trailing_avg,
      is_available,
      avg_rating,
      review_count,
      recorded_at
    FROM public.v_rate_volatility
    WHERE property_id = (SELECT id FROM prop)
    ORDER BY property_id, recorded_at DESC
  ),
  daily_history AS (
    SELECT DISTINCT ON (stay_date::date)
      stay_date::date AS stay_date,
      ROUND(nightly_rate::numeric, 2) AS nightly_rate,
      ROUND(trailing_avg_rate::numeric, 2) AS trailing_avg_rate,
      ROUND(pct_above_trailing_avg::numeric, 2) AS pct_above_trailing_avg,
      is_available
    FROM public.v_rate_volatility
    WHERE property_id = (SELECT id FROM prop)
      AND recorded_at >= NOW() - (LEAST(GREATEST(p_history_days, 1), 30) || ' days')::interval
    ORDER BY stay_date::date, recorded_at DESC
  )
  SELECT json_build_object(
    'status', 'success',
    'property_id', p.id,
    'property_name', p.name,
    'market', p.market,
    'platform', p.platform,
    'bedrooms', p.bedrooms,
    'is_active', p.is_active,
    'listing_url', p.url,
    'coordinates', json_build_object('lat', p.latitude, 'lng', p.longitude),
    'current_rate', ROUND(lr.nightly_rate::numeric, 2),
    'trailing_7d_avg', ROUND(lr.trailing_avg_rate::numeric, 2),
    'pct_above_trailing_avg', ROUND(lr.pct_above_trailing_avg::numeric, 2),
    'is_available', lr.is_available,
    'avg_rating', ROUND(lr.avg_rating::numeric, 2),
    'review_count', lr.review_count,
    'last_scraped_at', lr.recorded_at,
    'rate_history_days', (SELECT COUNT(*) FROM daily_history),
    'rate_history', COALESCE(
      (
        SELECT json_agg(
          json_build_object(
            'stay_date', dh.stay_date,
            'nightly_rate', dh.nightly_rate,
            'trailing_avg', dh.trailing_avg_rate,
            'pct_deviation', dh.pct_above_trailing_avg,
            'is_available', dh.is_available
          )
          ORDER BY dh.stay_date ASC
        )
        FROM daily_history dh
      ), '[]'::json
    )
  )
  FROM prop p
  LEFT JOIN latest_reading lr ON true;
$function$;

GRANT EXECUTE ON FUNCTION public.get_property_detail(text, integer) TO anon, authenticated, service_role;
