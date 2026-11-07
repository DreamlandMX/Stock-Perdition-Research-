"""Tests for the explicitly authorized minimal history coverage probe."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import pandas as pd  # type: ignore[import-untyped]

from stock_prediction.contracts.capability import (
    CapabilitySnapshot,
    CapabilityStatus,
    CapabilityErrorClass,
    DataCapability,
    PointInTimeStatus,
    TrainingEligibility,
)
from stock_prediction.opend.ports import SdkCallResult


PROBED_AT = datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def base_snapshot() -> CapabilitySnapshot:
    rows = [
        DataCapability(
            interface="request_history_kline",
            market="US",
            asset_type=asset,
            timeframe=timeframe,
            status=CapabilityStatus.UNKNOWN,
            point_in_time_status=PointInTimeStatus.UNPROVEN,
            probed_at=PROBED_AT,
        )
        for asset in ("stock", "etf")
        for timeframe in ("1d", "60m")
    ]
    rows.append(
        DataCapability(
            interface="get_history_kl_quota",
            market="US",
            asset_type="historical_quote",
            timeframe=None,
            status=CapabilityStatus.AVAILABLE,
            point_in_time_status=PointInTimeStatus.NOT_APPLICABLE,
            probed_at=PROBED_AT,
            quota_total=100,
            quota_used=2,
            quota_remaining=98,
        )
    )
    return CapabilitySnapshot(
        snapshot_id="before-coverage",
        probed_at=PROBED_AT,
        opend_version="1007",
        sdk_version="10.8.6808",
        capabilities=rows,
    )


class FakeHistoryContext:
    def __init__(
        self,
        fail_at: int | None = None,
        quota_remaining_before: int = 98,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.history_calls = 0
        self.close_calls = 0
        self.fail_at = fail_at
        self.quota_remaining_before = quota_remaining_before
        self.quota_calls = 0

    def __enter__(self) -> "FakeHistoryContext":
        return self

    def __exit__(self, *_args: object) -> Literal[False]:
        self.close_calls += 1
        return False

    def get_history_kl_quota(
        self, get_detail: bool = False
    ) -> SdkCallResult[tuple[int, int, list[object]]]:
        self.calls.append(("quota", {"get_detail": get_detail}))
        self.quota_calls += 1
        remaining = self.quota_remaining_before
        if self.quota_calls > 1:
            remaining = max(0, remaining - 2)
        return SdkCallResult.success((100 - remaining, remaining, []))

    def request_history_kline(self, **kwargs: object) -> SdkCallResult[Any]:
        self.history_calls += 1
        self.calls.append(("history", kwargs))
        if self.fail_at == self.history_calls:
            from stock_prediction.opend.errors import partial_response_error

            return SdkCallResult.failure(partial_response_error())
        is_earliest = kwargs["max_count"] == 1
        time_key = "2001-01-02 09:30:00" if is_earliest else "2026-07-10 16:00:00"
        return SdkCallResult.success(
            pd.DataFrame({"code": [kwargs["code"]], "time_key": [time_key]})
        )


class RecordingStore:
    def __init__(self, root: Path) -> None:
        self.path = root / "saved.json"
        self.saved: list[CapabilitySnapshot] = []

    def save(self, snapshot: CapabilitySnapshot) -> Path:
        self.saved.append(snapshot)
        return self.path


def test_coverage_probe_makes_exactly_eight_requests_and_saves_four_proven_rows(
    tmp_path: Path,
) -> None:
    from stock_prediction.opend.history_coverage import HistoryCoverageProbe

    context = FakeHistoryContext()
    store = RecordingStore(tmp_path)
    result = HistoryCoverageProbe(
        context_factory=lambda **_kwargs: context,
        clock=lambda: PROBED_AT,
        snapshot_id_factory=lambda: "after-coverage",
    ).run(base_snapshot(), store)

    assert context.history_calls == 8
    assert context.calls[0][0] == "quota"
    assert context.calls[-1][0] == "quota"
    assert context.close_calls == 1
    assert store.saved == [result.snapshot]
    earliest_calls = [
        arguments
        for name, arguments in context.calls
        if name == "history" and arguments["max_count"] == 1
    ]
    assert len(earliest_calls) == 4
    assert all(call["start"] == "1970-01-01" for call in earliest_calls)
    history_rows = [
        row for row in result.snapshot.capabilities
        if row.interface == "request_history_kline"
    ]
    assert len(history_rows) == 4
    assert all(row.status is CapabilityStatus.AVAILABLE for row in history_rows)
    assert all(row.point_in_time_status is PointInTimeStatus.PROVEN for row in history_rows)
    assert all(
        row.training_eligibility is TrainingEligibility.ELIGIBLE
        for row in history_rows
    )
    assert all(row.earliest_available_at.tzinfo is UTC for row in history_rows)
    assert all(row.latest_available_at.tzinfo is UTC for row in history_rows)
    assert {row.earliest_available_at for row in history_rows} == {
        datetime(2001, 1, 2, 14, 30, tzinfo=UTC)
    }
    assert {row.latest_available_at for row in history_rows} == {
        datetime(2026, 7, 10, 20, 0, tzinfo=UTC)
    }
    quota_row = next(
        row
        for row in result.snapshot.capabilities
        if row.interface == "get_history_kl_quota"
    )
    assert (
        quota_row.quota_total,
        quota_row.quota_used,
        quota_row.quota_remaining,
    ) == (100, 4, 96)
    assert result.quota_remaining_before == 98
    assert result.quota_remaining_after == 96


def test_partial_failure_closes_context_and_does_not_save(tmp_path: Path) -> None:
    from stock_prediction.opend.history_coverage import HistoryCoverageError, HistoryCoverageProbe

    context = FakeHistoryContext(fail_at=3)
    store = RecordingStore(tmp_path)

    try:
        HistoryCoverageProbe(context_factory=lambda **_kwargs: context).run(
            base_snapshot(), store
        )
    except HistoryCoverageError as error:
        assert str(error) == "history_coverage_failed"
    else:
        raise AssertionError("expected fixed coverage failure")
    assert context.close_calls == 1
    assert store.saved == []


def test_missing_required_history_row_fails_before_opening_context(
    tmp_path: Path,
) -> None:
    from stock_prediction.opend.history_coverage import (
        HistoryCoverageError,
        HistoryCoverageProbe,
    )

    original = base_snapshot()
    snapshot = original.model_copy(
        update={
            "capabilities": [
                row
                for row in original.capabilities
                if not (
                    row.interface == "request_history_kline"
                    and row.asset_type == "etf"
                    and row.timeframe == "60m"
                )
            ]
        }
    )
    context = FakeHistoryContext()
    store = RecordingStore(tmp_path)

    try:
        HistoryCoverageProbe(context_factory=lambda **_kwargs: context).run(
            snapshot, store
        )
    except HistoryCoverageError as error:
        assert str(error) == "history_coverage_failed"
    else:
        raise AssertionError("expected fixed coverage failure")

    assert context.calls == []
    assert store.saved == []


def test_insufficient_two_symbol_quota_fails_before_history_requests(
    tmp_path: Path,
) -> None:
    from stock_prediction.opend.history_coverage import (
        HistoryCoverageError,
        HistoryCoverageProbe,
    )

    context = FakeHistoryContext(quota_remaining_before=1)
    store = RecordingStore(tmp_path)

    try:
        HistoryCoverageProbe(context_factory=lambda **_kwargs: context).run(
            base_snapshot(), store
        )
    except HistoryCoverageError as error:
        assert str(error) == "history_coverage_failed"
    else:
        raise AssertionError("expected fixed coverage failure")

    assert context.calls == [("quota", {"get_detail": False})]
    assert context.history_calls == 0
    assert context.close_calls == 1
    assert store.saved == []


def test_wrong_symbol_history_response_closes_and_does_not_save(
    tmp_path: Path,
) -> None:
    from stock_prediction.opend.history_coverage import (
        HistoryCoverageError,
        HistoryCoverageProbe,
    )

    class WrongSymbolContext(FakeHistoryContext):
        def request_history_kline(self, **kwargs: object) -> SdkCallResult[Any]:
            result = super().request_history_kline(**kwargs)
            assert isinstance(result.value, pd.DataFrame)
            frame = result.value.copy()
            frame["code"] = "US.WRONG"
            return SdkCallResult.success(frame)

    context = WrongSymbolContext()
    store = RecordingStore(tmp_path)
    try:
        HistoryCoverageProbe(context_factory=lambda **_kwargs: context).run(
            base_snapshot(), store
        )
    except HistoryCoverageError as error:
        assert str(error) == "history_coverage_failed"
    else:
        raise AssertionError("expected fixed coverage failure")

    assert context.close_calls == 1
    assert store.saved == []


def test_out_of_window_history_response_closes_and_does_not_save(
    tmp_path: Path,
) -> None:
    from stock_prediction.opend.history_coverage import (
        HistoryCoverageError,
        HistoryCoverageProbe,
    )

    class OutOfWindowContext(FakeHistoryContext):
        def request_history_kline(self, **kwargs: object) -> SdkCallResult[Any]:
            result = super().request_history_kline(**kwargs)
            assert isinstance(result.value, pd.DataFrame)
            frame = result.value.copy()
            frame["time_key"] = "1969-12-31 16:00:00"
            return SdkCallResult.success(frame)

    context = OutOfWindowContext()
    store = RecordingStore(tmp_path)
    try:
        HistoryCoverageProbe(context_factory=lambda **_kwargs: context).run(
            base_snapshot(), store
        )
    except HistoryCoverageError as error:
        assert str(error) == "history_coverage_failed"
    else:
        raise AssertionError("expected fixed coverage failure")

    assert context.close_calls == 1
    assert store.saved == []


def test_mixed_valid_and_invalid_time_keys_fail_the_entire_response(
    tmp_path: Path,
) -> None:
    from stock_prediction.opend.history_coverage import (
        HistoryCoverageError,
        HistoryCoverageProbe,
    )

    class MixedTimeContext(FakeHistoryContext):
        def request_history_kline(self, **kwargs: object) -> SdkCallResult[Any]:
            result = super().request_history_kline(**kwargs)
            assert isinstance(result.value, pd.DataFrame)
            valid = result.value.iloc[0].to_dict()
            invalid = {**valid, "time_key": 123}
            return SdkCallResult.success(pd.DataFrame([valid, invalid]))

    context = MixedTimeContext()
    store = RecordingStore(tmp_path)
    try:
        HistoryCoverageProbe(context_factory=lambda **_kwargs: context).run(
            base_snapshot(), store
        )
    except HistoryCoverageError as error:
        assert str(error) == "history_coverage_failed"
    else:
        raise AssertionError("expected fixed coverage failure")

    assert context.close_calls == 1
    assert store.saved == []


def test_missing_quota_row_fails_before_opening_context(tmp_path: Path) -> None:
    from stock_prediction.opend.history_coverage import (
        HistoryCoverageError,
        HistoryCoverageProbe,
    )

    original = base_snapshot()
    snapshot = original.model_copy(
        update={
            "capabilities": [
                row
                for row in original.capabilities
                if row.interface != "get_history_kl_quota"
            ]
        }
    )
    context = FakeHistoryContext()
    store = RecordingStore(tmp_path)
    try:
        HistoryCoverageProbe(context_factory=lambda **_kwargs: context).run(
            snapshot, store
        )
    except HistoryCoverageError as error:
        assert str(error) == "history_coverage_failed"
    else:
        raise AssertionError("expected fixed coverage failure")

    assert context.calls == []
    assert store.saved == []


def test_successful_coverage_reconciles_recovered_quota_row(tmp_path: Path) -> None:
    from stock_prediction.opend.history_coverage import HistoryCoverageProbe

    original = base_snapshot()
    capabilities: list[DataCapability] = []
    for row in original.capabilities:
        if row.interface == "get_history_kl_quota":
            capabilities.append(
                DataCapability.model_validate(
                    {
                        **row.model_dump(),
                        "status": CapabilityStatus.DENIED,
                        "point_in_time_status": PointInTimeStatus.UNPROVEN,
                        "last_error_class": CapabilityErrorClass.QUOTA_EXHAUSTED,
                    }
                )
            )
        else:
            capabilities.append(row)
    snapshot = original.model_copy(update={"capabilities": capabilities})

    result = HistoryCoverageProbe(
        context_factory=lambda **_kwargs: FakeHistoryContext(),
        clock=lambda: PROBED_AT,
    ).run(snapshot, RecordingStore(tmp_path))

    quota_row = next(
        row
        for row in result.snapshot.capabilities
        if row.interface == "get_history_kl_quota"
    )
    assert quota_row.status is CapabilityStatus.AVAILABLE
    assert quota_row.point_in_time_status is PointInTimeStatus.NOT_APPLICABLE
    assert quota_row.last_error_class is None
