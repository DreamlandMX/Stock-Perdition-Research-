SET TimeZone = 'UTC';

CREATE TABLE IF NOT EXISTS adjustment_artifacts (
    adjustment_revision VARCHAR PRIMARY KEY,
    input_hash VARCHAR NOT NULL,
    mode VARCHAR NOT NULL,
    as_of TIMESTAMPTZ NOT NULL,
    point_in_time_eligible BOOLEAN NOT NULL,
    row_count BIGINT NOT NULL,
    artifact_json JSON NOT NULL
);

CREATE TABLE IF NOT EXISTS adjusted_market_bars (
    adjustment_revision VARCHAR NOT NULL,
    input_hash VARCHAR NOT NULL,
    point_in_time_eligible BOOLEAN NOT NULL,
    symbol VARCHAR NOT NULL,
    timeframe VARCHAR NOT NULL,
    session VARCHAR NOT NULL,
    session_date DATE NOT NULL,
    bar_start TIMESTAMPTZ NOT NULL,
    raw_open DOUBLE NOT NULL,
    raw_high DOUBLE NOT NULL,
    raw_low DOUBLE NOT NULL,
    raw_close DOUBLE NOT NULL,
    split_factor DOUBLE NOT NULL,
    adjusted_open DOUBLE NOT NULL,
    adjusted_high DOUBLE NOT NULL,
    adjusted_low DOUBLE NOT NULL,
    adjusted_close DOUBLE NOT NULL,
    adjusted_volume DOUBLE NOT NULL,
    capital_gain_return DOUBLE,
    cash_distribution DOUBLE NOT NULL,
    total_return_index DOUBLE NOT NULL,
    PRIMARY KEY (adjustment_revision, symbol, timeframe, session, bar_start)
);
