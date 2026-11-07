"""Offline correctness smoke for the frozen M1 data and model stack."""

from datetime import UTC, datetime, timedelta


def test_duckdb_executes_versioned_sql_over_arrow_table() -> None:
    import duckdb
    import pyarrow as pa

    from stock_prediction.sql_assets import load_sql_asset

    start = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)
    input_arrow = pa.table(
        {
            "symbol": ["US.SPY", "US.AAPL", "US.MSFT"],
            "source_available_at": [
                start + timedelta(minutes=offset) for offset in range(3)
            ],
            "feature_value": [1.5, 2.5, 3.5],
        }
    )

    with duckdb.connect(config={"threads": 1}) as connection:
        connection.register("input_arrow", input_arrow)
        result = connection.execute(
            load_sql_asset("m1/toolchain_smoke.sql")
        ).to_arrow_table()

    assert result.column_names == [
        "asset_schema",
        "asset_version",
        "symbol",
        "source_available_at",
        "feature_value",
    ]
    assert result.schema == pa.schema(
        [
            pa.field("asset_schema", pa.string()),
            pa.field("asset_version", pa.int32()),
            pa.field("symbol", pa.string()),
            pa.field("source_available_at", pa.timestamp("us", tz="UTC")),
            pa.field("feature_value", pa.float64()),
        ]
    )
    assert result.num_rows == 3
    assert result["asset_schema"].to_pylist() == ["stock_prediction.m1"] * 3
    assert result["asset_version"].to_pylist() == [1] * 3
    assert result["symbol"].to_pylist() == ["US.AAPL", "US.MSFT", "US.SPY"]
    assert all(
        value is not None
        and value.utcoffset() == timedelta(0)
        and value.tzinfo is not None
        for value in result["source_available_at"].to_pylist()
    )


def test_lightgbm_trains_with_fixed_seed_and_predicts_probability_batch() -> None:
    import lightgbm as lgb
    import numpy as np
    import pandas as pd  # type: ignore[import-untyped]
    from sklearn.datasets import make_classification

    seed = 20260711
    feature_values, labels = make_classification(
        n_samples=96,
        n_features=8,
        n_informative=5,
        n_redundant=1,
        random_state=seed,
    )
    features = pd.DataFrame(
        feature_values,
        columns=[f"feature_{index}" for index in range(feature_values.shape[1])],
    )
    model = lgb.LGBMClassifier(
        n_estimators=12,
        learning_rate=0.1,
        max_depth=3,
        random_state=seed,
        n_jobs=1,
        deterministic=True,
        verbosity=-1,
    )

    model.fit(features, labels)
    probabilities = model.predict_proba(features[:24])[:, 1]

    assert probabilities.shape == (24,)
    assert np.isfinite(probabilities).all()
    assert ((probabilities >= 0.0) & (probabilities <= 1.0)).all()
