"""Daily return context from the same immutable stock snapshot."""

from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
import json
from math import log
from math import log1p, isfinite
import pandas as pd
from zoneinfo import ZoneInfo
from threading import RLock
from pathlib import Path

from stock_prediction.contracts.options import OptionStockReturn
from stock_prediction.contracts.serving import InstrumentHistory
from stock_prediction.live.config import LivePaths
from stock_prediction.live.features import (
    LiveFeatureArtifact,
    load_latest_feature_artifact,
)
from stock_prediction.live.artifact_references import ArtifactReference
from stock_prediction.options.store import _validated_file_identity


_FileIdentity = tuple[int, ...]
_FeatureRead = tuple[
    _FileIdentity, _FileIdentity, LiveFeatureArtifact, pd.DataFrame | None
]
_request_artifacts: ContextVar[dict[Path, _FeatureRead] | None] = ContextVar(
    "option_return_artifacts", default=None
)
_frames: OrderedDict[
    tuple[Path, _FileIdentity, ArtifactReference | None], pd.DataFrame
] = OrderedDict()
_frame_lock = RLock()


@contextmanager
def stock_return_scope() -> Iterator[None]:
    token = _request_artifacts.set({})
    try:
        yield
    finally:
        _request_artifacts.reset(token)


def _identity(root: Path, path: Path) -> _FileIdentity:
    try:
        return _validated_file_identity(root, path)
    except OSError as error:
        raise RuntimeError("live_feature_artifact_missing") from error


def _feature_frame(
    paths: LivePaths, as_of: datetime
) -> tuple[LiveFeatureArtifact, pd.DataFrame | None]:
    scope = _request_artifacts.get()
    pointer = paths.features / "LATEST.json"
    pointer_identity: _FileIdentity | None
    cached = scope.get(pointer) if scope is not None else None
    unchanged = False
    if cached is not None:
        pointer_identity, artifact_identity, artifact, frame = cached
        if (
            _identity(paths.features, pointer) == pointer_identity
            and _identity(paths.features, artifact.path) == artifact_identity
        ):
            unchanged = True
            if artifact.generated_at > as_of or frame is not None:
                return artifact, frame
    # Each request validates the SHA; only unchanged reads within that request reuse it.
    if not unchanged:
        pointer_identity = (
            _identity(paths.features, pointer) if pointer.exists() else None
        )
        target = None
        before_identity = None
        if pointer_identity is not None:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or not isinstance(
                payload.get("artifact_reference"), dict
            ):
                raise RuntimeError("live_feature_artifact_manifest_invalid")
            reference = ArtifactReference.from_dict(payload["artifact_reference"])
            target = paths.features / reference.relative_path
            before_identity = _identity(paths.features, target)
        artifact = load_latest_feature_artifact(paths)
        artifact_identity = _identity(paths.features, artifact.path)
        if target is not None and (
            target.resolve() != artifact.path or before_identity != artifact_identity
        ):
            raise RuntimeError("live_feature_artifact_changed_during_validation")
    if artifact.generated_at > as_of:
        if scope is not None and pointer_identity is not None:
            scope[pointer] = pointer_identity, artifact_identity, artifact, None
        return artifact, None
    key = (artifact.path, artifact_identity, getattr(artifact, "reference", None))
    with _frame_lock:
        frame = _frames.get(key)
        if frame is None:
            frame = pd.read_parquet(
                artifact.path,
                columns=[
                    "symbol",
                    "session_date",
                    "source_available_at",
                    "capital_gain_return_1",
                    "quality_quarantined",
                ],
            )
            frame["source_available_at"] = pd.to_datetime(
                frame["source_available_at"], utc=True
            )
            if _identity(paths.features, artifact.path) != artifact_identity:
                raise RuntimeError("live_feature_artifact_changed_during_read")
            _frames[key] = frame
        _frames.move_to_end(key)
        while len(_frames) > 2:
            _frames.popitem(last=False)
    if (
        pointer_identity is not None
        and _identity(paths.features, pointer) != pointer_identity
    ):
        raise RuntimeError("live_feature_artifact_changed_during_read")
    if scope is not None and pointer_identity is not None:
        scope[pointer] = pointer_identity, artifact_identity, artifact, frame
    return artifact, frame


def stock_returns_from_history(
    history: InstrumentHistory, *, available_at: datetime, annualization_sessions: int
) -> tuple[OptionStockReturn, ...]:
    if history.timeframe != "1d" or history.adjustment != "split_adjusted":
        raise ValueError("realized volatility requires daily split-adjusted prices")
    bars = [bar for bar in history.bars if bar.at <= available_at]
    return tuple(
        OptionStockReturn(
            symbol=history.symbol,
            session=current.at.astimezone(ZoneInfo("America/New_York")).date(),
            log_return=log(current.close / previous.close),
            observed_at=current.at,
            source_available_at=available_at,
            annualization_sessions=annualization_sessions,
        )
        for previous, current in zip(bars, bars[1:])
        if not previous.data_gap and not current.data_gap
    )


def stock_returns_from_feature_artifact(
    paths: LivePaths, symbol: str, *, as_of: datetime
) -> tuple[OptionStockReturn, ...]:
    artifact, frame = _feature_frame(paths, as_of)
    if artifact.generated_at > as_of:
        return ()
    assert frame is not None
    frame = (
        frame.loc[(frame["symbol"] == symbol) & (frame["source_available_at"] <= as_of)]
        .sort_values("session_date")
        .tail(40)
    )
    output = []
    for row in frame.itertuples(index=False):
        value = float(row.capital_gain_return_1)
        if not isfinite(value) or value <= -1 or bool(row.quality_quarantined):
            continue
        observed = row.source_available_at.to_pydatetime()
        output.append(
            OptionStockReturn(
                symbol=symbol,
                session=pd.Timestamp(row.session_date).date(),
                log_return=log1p(value),
                observed_at=observed,
                source_available_at=max(artifact.generated_at, observed),
                annualization_sessions=252,
            )
        )
    return tuple(output)
