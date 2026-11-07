from argparse import Namespace
from datetime import UTC, date, datetime
from io import StringIO
from pathlib import Path

import duckdb
import pytest

from stock_prediction.cli import build_parser, main
from stock_prediction.live.commands import run_live_command
from stock_prediction.live.config import LivePaths, LIVE_FEATURE_SET_VERSION
from stock_prediction.live.features import _artifact_versions
from stock_prediction.live.ingestion import LiveOpenDService
from stock_prediction.live.universe_scope import resolve_live_universe
from stock_prediction.universe.active import ACTIVE_UNIVERSE
from stock_prediction.universe.seed import SEED_UNIVERSE
from backend.tests.live.test_ingestion import (
    PreflightContext,
    HistoryCollectionContext,
    _daily_history_frame,
)
from stock_prediction.contracts.market_data import Timeframe
from stock_prediction.opend.history import OpenDHistoryReader
from stock_prediction.opend.ports import RawHistoryPage, SdkCallResult

REVISION = "opend-expanded-live-market-bars-pit-adjusted-v1"


def test_expanded_opend_versions_and_reused_quota(tmp_path: Path) -> None:
    paths = LivePaths.for_dataset(tmp_path, REVISION)
    assert resolve_live_universe(paths).symbols == ACTIVE_UNIVERSE
    assert _artifact_versions(paths) == (REVISION, LIVE_FEATURE_SET_VERSION)
    context = PreflightContext(
        history_used=100,
        history_remaining=200,
        history_details=[{"code": code} for code in SEED_UNIVERSE],
    )
    service = LiveOpenDService(
        paths,
        context_factory=lambda: context,
        clock=lambda: datetime(2026, 7, 13, 18, tzinfo=UTC),
    )
    preflight = service.preflight()
    assert preflight.ready and preflight.new_history_symbols == 104
    assert context.history_calls == 0
    assert service.storage.latest_health()["daily_symbols_total"] == 204
    service._persist_calendar(preflight)
    context = HistoryCollectionContext(
        [
            SdkCallResult.success(
                RawHistoryPage(frame=_daily_history_frame(), next_page_token=None)
            )
        ]
    )
    reader = OpenDHistoryReader(context_factory=lambda: context)
    values = dict(
        reader=reader,
        context=context,
        symbol="US.SPY",
        timeframe=Timeframe.DAY_1,
        start=date(2018, 1, 1),
        end=date(2026, 7, 10),
        priority="high",
    )
    assert service._collect_task(**values)["committed"] == 1
    assert service._collect_task(**values)["skipped"] == 1
    with duckdb.connect(str(paths.catalog), read_only=True) as connection:
        assert connection.execute(
            "SELECT DISTINCT dataset_revision FROM market_bar_observations"
        ).fetchall() == [(REVISION,)]


@pytest.mark.parametrize(
    "command",
    [
        "live-preflight",
        "live-bootstrap",
        "live-expand-universe",
        "live-build-features",
        "live-publish",
    ],
)
def test_cli_accepts_staged_opend_revision(command: str) -> None:
    args = build_parser().parse_args([command, "--dataset-revision", REVISION])
    assert args.dataset_revision == REVISION


@pytest.mark.parametrize("command", ["live-validate-dataset", "live-activate-dataset"])
def test_cli_dispatches_dataset_commands(command: str) -> None:
    captured = []
    argv = [command, "--dataset-revision", REVISION]
    if command == "live-activate-dataset":
        assert main(argv, stdout=StringIO(), stderr=StringIO()) == 2
        argv.append("--confirm-activation")

    def run(args):
        captured.append(args)
        return {"status": "ok"}

    assert main(argv, live_runner=run, stdout=StringIO(), stderr=StringIO()) == 0
    assert captured[0].command == command
    assert captured[0].dataset_revision == REVISION


@pytest.mark.parametrize(
    "command", ["live-preflight", "live-bootstrap", "live-expand-universe"]
)
def test_command_routes_to_staged_opend_without_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    captured = []

    class Service:
        def __init__(self, paths):
            captured.append(paths)

        def preflight(self):
            return self

        def as_dict(self):
            return {"status": "ready"}

        def bootstrap(self):
            return {"status": "ok"}

        def expand_universe(self, **kwargs):
            return {"status": "ok"}

    monkeypatch.setattr("stock_prediction.live.commands.LiveOpenDService", Service)
    run_live_command(
        Namespace(
            command=command,
            workspace_root=tmp_path,
            dataset_revision=REVISION,
            provider="opend",
            allow_zero_reserve=False,
        )
    )
    assert captured[0].dataset_revision == REVISION
    assert captured[0].root != LivePaths.from_workspace(tmp_path).root
    assert not (tmp_path / "var" / "live-active.json").exists()


@pytest.mark.parametrize("failure", ["history", "rehab", "metadata"])
@pytest.mark.parametrize("command", ["bootstrap", "refresh"])
def test_bootstrap_collection_failure_is_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str, command: str
) -> None:
    context = PreflightContext(history_remaining=300)
    service = LiveOpenDService(
        LivePaths.for_dataset(tmp_path, REVISION),
        context_factory=lambda: context,
        clock=lambda: datetime(2026, 7, 13, 18, tzinfo=UTC),
    )
    monkeypatch.setattr(
        service,
        "_collect_ranges",
        lambda *a, **k: {
            "committed": 0,
            "skipped": 0,
            "failed": int(failure == "history"),
            "pages": 0,
            "rows": 0,
        },
    )
    monkeypatch.setattr(
        service,
        "_collect_rehab",
        lambda *a: {"committed": 0, "failed": int(failure == "rehab")},
    )
    monkeypatch.setattr(
        service,
        "_collect_instrument_metadata",
        lambda *a: {"committed": 0, "failed": int(failure == "metadata")},
    )
    assert getattr(service, command)()["status"] == "partial"


def test_validation_uses_completed_session_and_bound_universe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = {}
    paths = LivePaths.for_dataset(tmp_path, REVISION)
    service = LiveOpenDService(paths)
    service.storage.record_calendar(
        sessions=((date(2026, 9, 29), "WHOLE"), (date(2026, 9, 30), "WHOLE")),
        source_hash="test",
        dataset_revision=REVISION,
    )
    monkeypatch.setattr(
        "stock_prediction.live.commands.datetime",
        type(
            "Clock",
            (),
            {"now": staticmethod(lambda tz: datetime(2026, 9, 30, 16, tzinfo=UTC))},
        ),
    )
    monkeypatch.setattr(
        "stock_prediction.live.commands.compare_provider_catalogs",
        lambda *a, **k: {"status": "pass"},
    )

    def validate(paths, **kwargs):
        captured.update(kwargs)
        return {"status": "pass"}

    monkeypatch.setattr(
        "stock_prediction.live.commands.validate_live_dataset", validate
    )
    assert run_live_command(
        Namespace(
            command="live-validate-dataset",
            workspace_root=tmp_path,
            dataset_revision=REVISION,
        )
    ) == {"status": "pass"}
    assert captured["symbols"] == ACTIVE_UNIVERSE
    assert captured["expected_cutoff"] == date(2026, 9, 29)
