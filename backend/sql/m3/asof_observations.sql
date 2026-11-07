WITH visible AS (
    SELECT
        source.*,
        source.ingested_at <= $as_of AS locally_ingested_by_as_of,
        ROW_NUMBER() OVER (
            PARTITION BY source.event_id
            ORDER BY source.source_available_at DESC, source.revision_no DESC
        ) AS visibility_rank
    FROM fundamental_observations_input AS source
    WHERE source.point_in_time_eligibility = 'eligible'
      AND source.published_at IS NOT NULL
      AND source.published_at <= $as_of
      AND source.source_available_at <= $as_of
), future_audit AS (
    SELECT
        event_id,
        COUNT(*) FILTER (
            WHERE point_in_time_eligibility = 'eligible'
              AND (
                  published_at > $as_of
                  OR source_available_at > $as_of
              )
        ) AS future_revision_count
    FROM fundamental_observations_input
    GROUP BY event_id
)
SELECT
    visible.event_id,
    visible.observation_id,
    visible.revision_no,
    visible.symbol,
    visible.metric,
    visible.value_numeric,
    visible.locally_ingested_by_as_of,
    future_audit.future_revision_count
FROM visible
JOIN future_audit USING (event_id)
WHERE visible.visibility_rank = 1
ORDER BY visible.symbol, visible.metric, visible.report_period, visible.event_id;
