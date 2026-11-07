SET TimeZone = 'UTC';

CREATE OR REPLACE TABLE market_bar_observations AS
WITH ordered AS (
    SELECT
        *,
        ROW_NUMBER() OVER natural_order AS natural_row_no,
        LAG(session_date) OVER natural_order AS previous_session_date,
        LAG(open) OVER natural_order AS previous_open,
        LAG(high) OVER natural_order AS previous_high,
        LAG(low) OVER natural_order AS previous_low,
        LAG(close) OVER natural_order AS previous_close,
        LAG(volume) OVER natural_order AS previous_volume,
        LAG(turnover) OVER natural_order AS previous_turnover,
        MD5(CONCAT_WS('|',
            CAST(session_date AS VARCHAR),
            CAST(open AS VARCHAR),
            CAST(high AS VARCHAR),
            CAST(low AS VARCHAR),
            CAST(close AS VARCHAR),
            CAST(volume AS VARCHAR),
            COALESCE(CAST(turnover AS VARCHAR), '<NULL>')
        )) AS value_hash
    FROM raw_market_bars_input
    WINDOW natural_order AS (
        PARTITION BY symbol, timeframe, session, bar_start
        ORDER BY manifest_committed_at, manifest_batch_id
    )
), marked AS (
    SELECT
        *,
        CASE
            WHEN natural_row_no = 1 THEN 1
            WHEN session_date IS DISTINCT FROM previous_session_date
                OR open IS DISTINCT FROM previous_open
                OR high IS DISTINCT FROM previous_high
                OR low IS DISTINCT FROM previous_low
                OR close IS DISTINCT FROM previous_close
                OR volume IS DISTINCT FROM previous_volume
                OR turnover IS DISTINCT FROM previous_turnover
                THEN 1
            ELSE 0
        END AS value_changed
    FROM ordered
), numbered AS (
    SELECT
        *,
        SUM(value_changed) OVER (
            PARTITION BY symbol, timeframe, session, bar_start
            ORDER BY manifest_committed_at, manifest_batch_id
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS derived_revision_no
    FROM marked
)
SELECT
    symbol,
    timeframe,
    session,
    session_date,
    bar_start,
    open,
    high,
    low,
    close,
    volume,
    turnover,
    FIRST(source_available_at ORDER BY manifest_committed_at, manifest_batch_id)
        AS source_available_at,
    FIRST(ingested_at ORDER BY manifest_committed_at, manifest_batch_id)
        AS ingested_at,
    FIRST(schema_version ORDER BY manifest_committed_at, manifest_batch_id)
        AS schema_version,
    FIRST(dataset_revision ORDER BY manifest_committed_at, manifest_batch_id)
        AS dataset_revision,
    FIRST(rebuild_id ORDER BY manifest_committed_at, manifest_batch_id)
        AS rebuild_id,
    CAST(derived_revision_no AS INTEGER) AS revision_no,
    MIN(manifest_committed_at) AS first_seen_at,
    MAX(manifest_committed_at) AS last_seen_at,
    FIRST(manifest_batch_id ORDER BY manifest_committed_at, manifest_batch_id)
        AS first_batch_id,
    LAST(manifest_batch_id ORDER BY manifest_committed_at, manifest_batch_id)
        AS last_batch_id,
    CASE
        WHEN derived_revision_no = 1 THEN 'initial_observation'
        ELSE 'source_value_changed'
    END AS revision_reason,
    value_hash
FROM numbered
GROUP BY
    symbol,
    timeframe,
    session,
    bar_start,
    derived_revision_no,
    session_date,
    open,
    high,
    low,
    close,
    volume,
    turnover,
    value_hash;

CREATE OR REPLACE VIEW market_bars_canonical AS
SELECT * EXCLUDE (canonical_rank)
FROM (
    SELECT
        *,
        ROW_NUMBER() OVER (
            PARTITION BY symbol, timeframe, session, bar_start
            ORDER BY revision_no DESC
        ) AS canonical_rank
    FROM market_bar_observations
)
WHERE canonical_rank = 1;
