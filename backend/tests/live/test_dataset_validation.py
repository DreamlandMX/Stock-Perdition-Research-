from __future__ import annotations

from datetime import UTC, date, datetime
import json
from pathlib import Path

import pytest

from stock_prediction.contracts.market_data import MarketBar, MarketSession, Timeframe
from stock_prediction.live.config import (
    ALPACA_IEX_YAHOO_DATASET_REVISION,
    ALPACA_SIP_DATASET_REVISION,
    OPEND_EXPANDED_DATASET_REVISION,
    LivePaths,
)
from stock_prediction.live.dataset_validation import (
    DatasetValidationError,
    validate_live_dataset,
)
from stock_prediction.live.storage import LiveStorage


@pytest.mark.parametrize("day_type", ["WHOLE", "MORNING"])
@pytest.mark.parametrize(
    "defect",
    [
        None,
        "missing_bar",
        "missing_sample",
        "bad_volume",
        "stale_sample",
        "duplicate_sample",
        "accepted_gap",
        "unapproved_gap",
        "partial_gap",
        "stale_gap",
    ],
)
def test_opend_same_session_volume_and_bar_completeness(
    tmp_path: Path, day_type: str, defect: str | None
) -> None:
    paths = LivePaths.for_dataset(tmp_path, OPEND_EXPANDED_DATASET_REVISION)
    storage = LiveStorage(paths)
    day = date(2026, 8, 12)
    storage.record_calendar(
        sessions=((day, day_type),),
        source_hash="calendar",
        dataset_revision=paths.dataset_revision,
    )
    daily = _bar("US.AAPL", Timeframe.DAY_1).model_copy(
        update={"dataset_revision": paths.dataset_revision}
    )
    starts = range(13, 17 if day_type == "MORNING" else 20)
    rows = [daily] + [
        _bar("US.AAPL", Timeframe.MINUTE_60).model_copy(
            update={
                "bar_start": datetime(2026, 8, 12, hour, 30, tzinfo=UTC),
                "volume": 10,
                "dataset_revision": paths.dataset_revision,
            }
        )
        for hour in starts
    ]
    if defect == "missing_bar":
        rows.pop(2)
    if defect and defect.endswith("gap"):
        previous = date(2026, 8, 11)
        storage.record_calendar(
            sessions=((previous, day_type),),
            source_hash="earlier",
            dataset_revision=paths.dataset_revision,
        )
        rows.extend(
            [
                daily.model_copy(
                    update={
                        "session_date": previous,
                        "bar_start": datetime(2026, 8, 11, 13, 30, tzinfo=UTC),
                    }
                ),
                rows[1].model_copy(
                    update={
                        "symbol": "US.MSFT",
                        "session_date": previous,
                        "bar_start": datetime(2026, 8, 11, 13, 30, tzinfo=UTC),
                    }
                ),
            ]
        )
        if defect == "partial_gap":
            rows.append(rows[-1].model_copy(update={"symbol": "US.AAPL"}))
        (paths.root / "native-source-gap-evidence.json").write_text(
            json.dumps(
                {
                    "dataset_revision": "wrong"
                    if defect == "stale_gap"
                    else paths.dataset_revision,
                    "provider": "opend",
                    "symbol": "US.AAPL",
                    "decision": "pending_user_approval"
                    if defect == "unapproved_gap"
                    else "user_accepted_source_gap",
                    "requested_start": previous.isoformat(),
                    "requested_end": previous.isoformat(),
                    "source_missing_sessions": [previous.isoformat()],
                    "queries": [
                        {"timeframe": tf, "sdk_status": 0, "rows": 0}
                        for tf in ("60m", "1m")
                    ],
                }
            ),
            encoding="utf-8",
        )
    parts = storage.write_page(task_id="opend-volume", page_index=1, rows=tuple(rows))
    storage.register_catalog_parts(parts)
    sample = {
        "dataset_revision": paths.dataset_revision,
        "cutoff": day.isoformat(),
        "session_scope": "regular-plus-pre-post",
        "failed_symbols": [],
        "rows": [
            {
                "symbol": "US.AAPL",
                "session_date": day.isoformat(),
                "volume": 50 if defect == "bad_volume" else 100,
            }
        ],
    }
    if defect == "stale_sample":
        sample["cutoff"] = "2026-08-11"
    if defect == "duplicate_sample":
        sample["rows"] *= 2
    if defect and defect.endswith("gap"):
        sample["rows"].append(
            {"symbol": "US.AAPL", "session_date": "2026-08-11", "volume": 100}
        )
    if defect != "missing_sample":
        (paths.root / "full-session-volume-sample.json").write_text(
            json.dumps(sample), encoding="utf-8"
        )
    if defect and defect != "accepted_gap":
        with pytest.raises(DatasetValidationError):
            validate_live_dataset(paths, symbols=("US.AAPL",), expected_cutoff=day)
    else:
        result = validate_live_dataset(paths, symbols=("US.AAPL",), expected_cutoff=day)
        assert result["intraday_daily_volume_diff_median"] > 0.02
        assert result["intraday_daily_volume_comparable"] is False
        assert result["opend_volume_validation"]["incomplete_sessions"] == 0
        if defect == "accepted_gap":
            assert result["opend_volume_validation"]["retained_source_gaps"] == [
                {"symbol": "US.AAPL", "session_date": "2026-08-11"}
            ]


def _bar(symbol: str, timeframe: Timeframe) -> MarketBar:
    session_date = date(2026, 8, 12)
    start = datetime(2026, 8, 12, 13, 30, tzinfo=UTC)
    return MarketBar(
        symbol=symbol,
        timeframe=timeframe,
        session=MarketSession.REGULAR,
        session_date=session_date,
        bar_start=start,
        open=100.0,
        high=102.0,
        low=99.0,
        close=101.0,
        volume=100,
        turnover=10_100.0,
        source_available_at=datetime(2026, 8, 12, 20, 15, tzinfo=UTC),
        ingested_at=datetime(2026, 8, 12, 20, 16, tzinfo=UTC),
        raw_batch_id="validation-source",
        schema_version="market-bar-v1",
        dataset_revision=ALPACA_SIP_DATASET_REVISION,
        revision_no=1,
    )


def _seed(paths: LivePaths, symbols: tuple[str, ...]) -> None:
    storage = LiveStorage(paths)
    for index, symbol in enumerate(symbols):
        rows = (_bar(symbol, Timeframe.DAY_1), _bar(symbol, Timeframe.MINUTE_60))
        task = f"validation-{index}"
        parts = storage.write_page(task_id=task, page_index=1, rows=rows)
        storage._register_catalog_parts(parts)


def test_complete_dataset_writes_validation_evidence(tmp_path: Path) -> None:
    symbols = ("US.AAPL", "US.EWJ")
    paths = LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)
    _seed(paths, symbols)

    result = validate_live_dataset(
        paths, symbols=symbols, expected_cutoff=date(2026, 8, 12)
    )

    assert result["status"] == "pass"
    assert result["daily_symbols_complete"] == 2
    assert result["intraday_symbols_complete"] == 2
    assert (paths.root / "validation.json").is_file()


def test_incomplete_dataset_fails_without_validation_evidence(tmp_path: Path) -> None:
    paths = LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)
    _seed(paths, ("US.AAPL",))

    with pytest.raises(DatasetValidationError, match="dataset_coverage_incomplete"):
        validate_live_dataset(
            paths,
            symbols=("US.AAPL", "US.EWJ"),
            expected_cutoff=date(2026, 8, 12),
        )

    assert not (paths.root / "validation.json").exists()
    failure = (paths.root / "validation-failure.json").read_text("utf-8")
    assert '"decision": "abstain"' in failure
    assert '"daily_current_coverage"' in failure


def test_intraday_daily_volume_mismatch_fails_closed(tmp_path: Path) -> None:
    paths = LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)
    storage = LiveStorage(paths)
    daily = _bar("US.AAPL", Timeframe.DAY_1)
    intraday = _bar("US.AAPL", Timeframe.MINUTE_60).model_copy(update={"volume": 90})
    parts = storage.write_page(
        task_id="volume-mismatch", page_index=1, rows=(daily, intraday)
    )
    storage._register_catalog_parts(parts)

    with pytest.raises(DatasetValidationError, match="dataset_coverage_incomplete"):
        validate_live_dataset(
            paths,
            symbols=("US.AAPL",),
            expected_cutoff=date(2026, 8, 12),
        )


def test_iex_yahoo_dataset_requires_explicit_volume_provenance(tmp_path: Path) -> None:
    paths = LivePaths.for_dataset(tmp_path, ALPACA_IEX_YAHOO_DATASET_REVISION)
    _seed(paths, ("US.AAPL",))

    with pytest.raises(DatasetValidationError, match="dataset_coverage_incomplete"):
        validate_live_dataset(
            paths,
            symbols=("US.AAPL",),
            expected_cutoff=date(2026, 8, 12),
        )


def test_required_history_start_fails_closed(tmp_path: Path) -> None:
    paths = LivePaths.for_dataset(tmp_path, ALPACA_SIP_DATASET_REVISION)
    _seed(paths, ("US.AAPL",))

    with pytest.raises(DatasetValidationError, match="dataset_coverage_incomplete"):
        validate_live_dataset(
            paths,
            symbols=("US.AAPL",),
            expected_cutoff=date(2026, 8, 12),
            required_daily_start=date(2018, 1, 1),
            required_intraday_start=date(2024, 8, 12),
        )
