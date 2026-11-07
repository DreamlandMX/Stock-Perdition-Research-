from datetime import UTC, datetime
from io import StringIO
import json

import pytest

from stock_prediction.operations.safe_logging import (
    SafeJsonLogger,
    UnsafeLogField,
    safe_log_payload,
)
from stock_prediction.operations.schema_guard import (
    SchemaMismatch,
    require_exact_schema,
)
from stock_prediction.operations.slo import measure_slo


def test_safe_logger_allows_operational_ids_and_rejects_sensitive_fields() -> None:
    stream = StringIO()
    logger = SafeJsonLogger(stream)
    logger.emit(
        "batch_completed",
        {"component": "ingestion", "status": "ok", "count": 100},
        timestamp=datetime(2026, 7, 21, tzinfo=UTC),
    )
    payload = json.loads(stream.getvalue())
    assert payload["component"] == "ingestion"
    assert "token" not in stream.getvalue().casefold()

    for field in ("account_id", "api_token", "password", "raw_response"):
        with pytest.raises(UnsafeLogField, match="sensitive"):
            safe_log_payload("unsafe", {field: "do-not-log"})


def test_schema_guard_reports_missing_unexpected_and_changed_columns() -> None:
    require_exact_schema({"close": "DOUBLE"}, {"close": "DOUBLE"})
    with pytest.raises(SchemaMismatch) as failure:
        require_exact_schema(
            {"close": "DOUBLE", "volume": "BIGINT"},
            {"close": "VARCHAR", "ticker": "VARCHAR"},
        )
    message = str(failure.value)
    assert "volume" in message
    assert "ticker" in message
    assert "close" in message


def test_repeatable_slo_measurement_reports_sample_count_and_gate() -> None:
    measurement = measure_slo(
        metric_id="batch-prediction",
        workload="100 symbols x 3 horizons no-op harness",
        target_ms=60_000,
        operation=lambda: sum(range(100)),
        warmups=1,
        samples=5,
    )
    assert measurement.status.value == "passed"
    assert measurement.sample_count == 5
    assert measurement.p95_ms <= measurement.target_ms
