| schema_name | function_name             | identity_args                                                                                           | result_type                                                                                                                                                                                                                                                                                  | security_definer | function_definition                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| ----------- | ------------------------- | ------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| public      | get_dashboard_kpis        |                                                                                                         | json                                                                                                                                                                                                                                                                                         | true             | CREATE OR REPLACE FUNCTION public.get_dashboard_kpis()
 RETURNS json
 LANGUAGE sql
 SECURITY DEFINER
AS $function$
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
      'properties_tracked', (SELECT COUNT(DISTINCT property_id) FROM v_rate_volatility),
      'rate_changes_7d',    (
        SELECT COUNT(*) FROM rate_history
        WHERE created_at >= NOW() - INTERVAL '7 days'
        AND nightly_rate IS NOT NULL
        AND nightly_rate != (
          SELECT nightly_rate FROM rate_history rh2
          WHERE rh2.property_id = rate_history.property_id
          AND rh2.created_at < rate_history.created_at
          AND rh2.nightly_rate IS NOT NULL
          ORDER BY rh2.created_at DESC LIMIT 1
        )
      ),
      'spikes_7d',          (
        SELECT COUNT(*) FROM v_rate_volatility
        WHERE ABS(pct_above_trailing_avg) >= 25
        AND recorded_at >= NOW() - INTERVAL '7 days'
      ),
      'tracking_since',     (SELECT MIN(created_at) FROM properties),
      'last_scrape_status', (
        SELECT COALESCE(json_object_agg(platform, last_status), '{}'::json)
        FROM v_scrape_health
        WHERE job_type = 'REAL_ESTATE_MONITOR'
      )
    )
  );
$function$
 |
| public      | get_distance_km           | property_a_id uuid, property_b_id uuid                                                                  | TABLE(distance_km numeric)                                                                                                                                                                                                                                                                   | true             | CREATE OR REPLACE FUNCTION public.get_distance_km(property_a_id uuid, property_b_id uuid)
 RETURNS TABLE(distance_km numeric)
 LANGUAGE sql
 STABLE SECURITY DEFINER
AS $function$
  WITH a AS (
    SELECT latitude, longitude
    FROM public.properties
    WHERE id = property_a_id
  ),
  b AS (
    SELECT latitude, longitude
    FROM public.properties
    WHERE id = property_b_id
  )
  SELECT
    (
      6371 * 2 * ASIN(
        SQRT(
          POWER(SIN(RADIANS(b.latitude - a.latitude) / 2), 2) +
          COS(RADIANS(a.latitude)) * COS(RADIANS(b.latitude)) *
          POWER(SIN(RADIANS(b.longitude - a.longitude) / 2), 2)
        )
      )
    )::numeric(12,4) AS distance_km
  FROM a, b
  WHERE a.latitude IS NOT NULL
    AND a.longitude IS NOT NULL
    AND b.latitude IS NOT NULL
    AND b.longitude IS NOT NULL;
$function$
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| public      | get_market_averages       | market_param text                                                                                       | TABLE(market character varying, active_properties bigint, avg_nightly_rate numeric, min_nightly_rate numeric, max_nightly_rate numeric)                                                                                                                                                      | true             | CREATE OR REPLACE FUNCTION public.get_market_averages(market_param text DEFAULT NULL::text)
 RETURNS TABLE(market character varying, active_properties bigint, avg_nightly_rate numeric, min_nightly_rate numeric, max_nightly_rate numeric)
 LANGUAGE sql
 STABLE SECURITY DEFINER
AS $function$
    SELECT 
        market,
        COUNT(DISTINCT property_id) AS active_properties,
        ROUND(AVG(nightly_rate), 2) AS avg_nightly_rate,
        MIN(nightly_rate) AS min_nightly_rate,
        MAX(nightly_rate) AS max_nightly_rate
    FROM public.v_rate_volatility
    WHERE (market_param IS NULL OR LOWER(market) = LOWER(market_param))
      AND is_active = true
      AND nightly_rate IS NOT NULL
    GROUP BY market;
$function$
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| public      | get_properties_by_filter  | p_market text, p_platform text, p_available boolean, p_bedrooms integer                                 | TABLE(property_id uuid, property_name character varying, market character varying, platform character varying, bedrooms integer, avg_rating numeric, review_count integer, nightly_rate numeric, pct_above_trailing_avg numeric, is_available boolean, recorded_at timestamp with time zone) | true             | CREATE OR REPLACE FUNCTION public.get_properties_by_filter(p_market text DEFAULT NULL::text, p_platform text DEFAULT NULL::text, p_available boolean DEFAULT NULL::boolean, p_bedrooms integer DEFAULT NULL::integer)
 RETURNS TABLE(property_id uuid, property_name character varying, market character varying, platform character varying, bedrooms integer, avg_rating numeric, review_count integer, nightly_rate numeric, pct_above_trailing_avg numeric, is_available boolean, recorded_at timestamp with time zone)
 LANGUAGE sql
 STABLE SECURITY DEFINER
AS $function$
    SELECT DISTINCT ON (property_id)
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
        recorded_at
    FROM public.v_rate_volatility
    WHERE (p_market IS NULL OR LOWER(market) = LOWER(p_market))
      AND (p_platform IS NULL OR LOWER(platform) = LOWER(p_platform))
      AND (p_available IS NULL OR is_available = p_available)
      AND (p_bedrooms IS NULL OR bedrooms = p_bedrooms)
    ORDER BY property_id, recorded_at DESC;
$function$
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| public      | get_property_rate_changes | property_search text, days_param integer, compare_window_days integer                                   | TABLE(property_id uuid, property_name character varying, market character varying, platform character varying, stay_date timestamp with time zone, nightly_rate numeric, trailing_avg_rate numeric, pct_above_trailing_avg numeric, prev_nightly_rate numeric, pct_change_vs_prev numeric)   | true             | CREATE OR REPLACE FUNCTION public.get_property_rate_changes(property_search text, days_param integer DEFAULT 14, compare_window_days integer DEFAULT 1)
 RETURNS TABLE(property_id uuid, property_name character varying, market character varying, platform character varying, stay_date timestamp with time zone, nightly_rate numeric, trailing_avg_rate numeric, pct_above_trailing_avg numeric, prev_nightly_rate numeric, pct_change_vs_prev numeric)
 LANGUAGE sql
 STABLE SECURITY DEFINER
AS $function$
  WITH base AS (
    SELECT
      rh.property_id,
      p.name AS property_name,
      p.market,
      p.platform,
      rh.stay_date,
      rh.nightly_rate,
      -- compute trailing avg as avg of previous 7 days (excluding current day)
      AVG(rh.nightly_rate) OVER (
        PARTITION BY rh.property_id
        ORDER BY rh.stay_date
        ROWS BETWEEN 7 PRECEDING AND 1 PRECEDING
      ) AS trailing_avg_rate,
      LAG(rh.nightly_rate, compare_window_days) OVER (
        PARTITION BY rh.property_id
        ORDER BY rh.stay_date
      ) AS prev_nightly_rate
    FROM public.v_rate_volatility rh
    JOIN public.properties p
      ON p.id = rh.property_id
    WHERE (rh.property_id::text = property_search OR LOWER(rh.property_name) = LOWER(property_search) OR LOWER(rh.property_name) LIKE '%' || LOWER(property_search) || '%')
      AND rh.recorded_at >= (NOW() - (days_param || ' days')::interval)
  )
  SELECT
    property_id,
    property_name,
    market,
    platform,
    stay_date,
    nightly_rate,
    trailing_avg_rate,
    CASE
      WHEN trailing_avg_rate IS NULL OR trailing_avg_rate = 0 OR nightly_rate IS NULL THEN NULL
      ELSE ((nightly_rate - trailing_avg_rate) / trailing_avg_rate) * 100
    END AS pct_above_trailing_avg,
    prev_nightly_rate,
    CASE
      WHEN prev_nightly_rate IS NULL OR prev_nightly_rate = 0 OR nightly_rate IS NULL THEN NULL
      ELSE ((nightly_rate - prev_nightly_rate) / prev_nightly_rate) * 100
    END AS pct_change_vs_prev
  FROM base
  WHERE stay_date IS NOT NULL
  ORDER BY stay_date ASC;
$function$
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| public      | get_property_rate_history | property_search text, days_param integer                                                                | TABLE(property_id uuid, property_name character varying, market character varying, stay_date timestamp with time zone, nightly_rate numeric, trailing_avg_rate numeric, pct_above_trailing_avg numeric, is_available boolean)                                                                | true             | CREATE OR REPLACE FUNCTION public.get_property_rate_history(property_search text, days_param integer DEFAULT 30)
 RETURNS TABLE(property_id uuid, property_name character varying, market character varying, stay_date timestamp with time zone, nightly_rate numeric, trailing_avg_rate numeric, pct_above_trailing_avg numeric, is_available boolean)
 LANGUAGE sql
 STABLE SECURITY DEFINER
AS $function$
    SELECT 
        property_id,
        property_name,
        market,
        stay_date,
        nightly_rate,
        trailing_avg_rate,
        pct_above_trailing_avg,
        is_available
    FROM public.v_rate_volatility
    WHERE (property_id::TEXT = property_search OR LOWER(property_name) LIKE '%' || LOWER(property_search) || '%')
      AND stay_date >= (NOW() - (days_param || ' days')::INTERVAL)
    ORDER BY stay_date ASC;
$function$
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| public      | get_spike_alerts          | threshold_param numeric, days_param integer                                                             | TABLE(property_id uuid, property_name character varying, market character varying, platform character varying, nightly_rate numeric, trailing_avg_rate numeric, pct_above_trailing_avg numeric, recorded_at timestamp with time zone)                                                        | true             | CREATE OR REPLACE FUNCTION public.get_spike_alerts(threshold_param numeric DEFAULT 25.0, days_param integer DEFAULT 7)
 RETURNS TABLE(property_id uuid, property_name character varying, market character varying, platform character varying, nightly_rate numeric, trailing_avg_rate numeric, pct_above_trailing_avg numeric, recorded_at timestamp with time zone)
 LANGUAGE sql
 STABLE SECURITY DEFINER
AS $function$
    SELECT 
        property_id,
        property_name,
        market,
        platform,
        nightly_rate,
        trailing_avg_rate,
        pct_above_trailing_avg,
        recorded_at
    FROM public.v_rate_volatility
    WHERE ABS(pct_above_trailing_avg) >= threshold_param
      AND recorded_at >= (NOW() - (days_param || ' days')::INTERVAL)
    ORDER BY recorded_at DESC;
$function$
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |
| public      | get_tracked_markets       | p_platform text                                                                                         | TABLE(market character varying, active_properties bigint)                                                                                                                                                                                                                                    | true             | CREATE OR REPLACE FUNCTION public.get_tracked_markets(p_platform text DEFAULT NULL::text)
 RETURNS TABLE(market character varying, active_properties bigint)
 LANGUAGE sql
 STABLE SECURITY DEFINER
AS $function$
  SELECT p.market,
         COUNT(DISTINCT p.id) AS active_properties
  FROM public.properties p
  WHERE p.is_active = true
    AND (p_platform IS NULL OR LOWER(p.platform) = LOWER(p_platform))
    AND p.market IS NOT NULL
  GROUP BY p.market
  ORDER BY active_properties DESC, market;
$function$
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| public      | match_re_methodology      | query_embedding vector, match_threshold double precision, match_count integer                           | TABLE(id uuid, section_title character varying, chunk_content text, similarity double precision)                                                                                                                                                                                             | true             | CREATE OR REPLACE FUNCTION public.match_re_methodology(query_embedding vector, match_threshold double precision DEFAULT 0.5, match_count integer DEFAULT 3)
 RETURNS TABLE(id uuid, section_title character varying, chunk_content text, similarity double precision)
 LANGUAGE sql
 STABLE SECURITY DEFINER
AS $function$
    SELECT
        kb.id,
        kb.section_title,
        kb.chunk_content,
        1 - (kb.embedding <=> query_embedding) AS similarity
    FROM public.re_knowledge_base kb
    WHERE 1 - (kb.embedding <=> query_embedding) > match_threshold
    ORDER BY kb.embedding <=> query_embedding
    LIMIT match_count;
$function$
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| public      | rls_auto_enable           |                                                                                                         | event_trigger                                                                                                                                                                                                                                                                                | true             | CREATE OR REPLACE FUNCTION public.rls_auto_enable()
 RETURNS event_trigger
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'pg_catalog'
AS $function$
DECLARE
  cmd record;
BEGIN
  FOR cmd IN
    SELECT *
    FROM pg_event_trigger_ddl_commands()
    WHERE command_tag IN ('CREATE TABLE', 'CREATE TABLE AS', 'SELECT INTO')
      AND object_type IN ('table','partitioned table')
  LOOP
     IF cmd.schema_name IS NOT NULL AND cmd.schema_name IN ('public') AND cmd.schema_name NOT IN ('pg_catalog','information_schema') AND cmd.schema_name NOT LIKE 'pg_toast%' AND cmd.schema_name NOT LIKE 'pg_temp%' THEN
      BEGIN
        EXECUTE format('alter table if exists %s enable row level security', cmd.object_identity);
        RAISE LOG 'rls_auto_enable: enabled RLS on %', cmd.object_identity;
      EXCEPTION
        WHEN OTHERS THEN
          RAISE LOG 'rls_auto_enable: failed to enable RLS on %', cmd.object_identity;
      END;
     ELSE
        RAISE LOG 'rls_auto_enable: skip % (either system schema or not in enforced list: %.)', cmd.object_identity, cmd.schema_name;
     END IF;
  END LOOP;
END;
$function$
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| public      | search_properties         | p_search text, p_market text, p_platform text, p_bedrooms integer, p_available boolean, p_limit integer | TABLE(property_id uuid, property_name character varying, market character varying, platform character varying, bedrooms integer, avg_rating numeric, review_count integer, nightly_rate numeric, pct_above_trailing_avg numeric, is_available boolean, recorded_at timestamp with time zone) | true             | CREATE OR REPLACE FUNCTION public.search_properties(p_search text DEFAULT NULL::text, p_market text DEFAULT NULL::text, p_platform text DEFAULT NULL::text, p_bedrooms integer DEFAULT NULL::integer, p_available boolean DEFAULT NULL::boolean, p_limit integer DEFAULT 50)
 RETURNS TABLE(property_id uuid, property_name character varying, market character varying, platform character varying, bedrooms integer, avg_rating numeric, review_count integer, nightly_rate numeric, pct_above_trailing_avg numeric, is_available boolean, recorded_at timestamp with time zone)
 LANGUAGE sql
 STABLE SECURITY DEFINER
AS $function$
  SELECT DISTINCT ON (rvv.property_id)
    rvv.property_id,
    rvv.property_name,
    rvv.market,
    rvv.platform,
    rvv.bedrooms,
    rvv.avg_rating,
    rvv.review_count,
    rvv.nightly_rate,
    rvv.pct_above_trailing_avg,
    rvv.is_available,
    rvv.recorded_at
  FROM public.v_rate_volatility rvv
  WHERE
    (p_market IS NULL OR LOWER(rvv.market) = LOWER(p_market))
    AND (p_platform IS NULL OR LOWER(rvv.platform) = LOWER(p_platform))
    AND (p_bedrooms IS NULL OR rvv.bedrooms = p_bedrooms)
    AND (p_available IS NULL OR rvv.is_available = p_available)
    AND (
      p_search IS NULL
      OR rvv.property_name ILIKE '%' || p_search || '%'
      OR rvv.property_id::text = p_search
    )
  ORDER BY rvv.property_id, rvv.recorded_at DESC
  LIMIT LEAST(GREATEST(p_limit, 1), 200);
$function$
                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       |