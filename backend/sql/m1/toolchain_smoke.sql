SET TimeZone = 'UTC';

SELECT
    CAST('stock_prediction.m1' AS VARCHAR) AS asset_schema,
    CAST(1 AS INTEGER) AS asset_version,
    CAST(symbol AS VARCHAR) AS symbol,
    CAST(source_available_at AS TIMESTAMPTZ) AS source_available_at,
    CAST(feature_value AS DOUBLE) AS feature_value
FROM input_arrow
ORDER BY symbol;
