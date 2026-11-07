from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from stock_prediction.live.artifact_references import ArtifactReference
from stock_prediction.modeling.m7_dataset import (
    M7DatasetCoverageError,
    M7DatasetValidationError,
    M7EligibilityInputRow,
    build_m7_eligibility_manifest,
)
from stock_prediction.modeling.m7_recipe import (
    M7_FEATURE_SET_VERSION,
    M7_LABEL_VERSION,
)
from stock_prediction.universe.seed import SEED_UNIVERSE, SEED_UNIVERSE_VERSION


def _reference(kind: str, digest_character: str) -> ArtifactReference:
    return ArtifactReference(
        artifact_id=f"{kind}-r1",
        relative_path=f"immutable/{kind}-r1.json",
        sha256=digest_character * 64,
        schema_version=f"{kind}-v1",
        created_at=datetime(2026, 8, 22, 1, tzinfo=UTC),
    )


REFERENCES = {
    "adjusted_bar": _reference("adjusted-bar", "1"),
    "calendar": _reference("calendar", "2"),
    "feature": _reference("feature", "3"),
    "label": _reference("label", "4"),
}
THRESHOLD_REFERENCE = _reference("label-threshold", "5")


def _rows(
    anchors: tuple[date, ...],
) -> list[M7EligibilityInputRow]:
    return [
        M7EligibilityInputRow(
            symbol=symbol,
            anchor_session=anchor,
            horizon=horizon,
            universe_version=SEED_UNIVERSE_VERSION,
            dataset_revision="bars-r7",
            feature_set_version=M7_FEATURE_SET_VERSION,
            label_version=M7_LABEL_VERSION,
            adjusted_bar_reference=REFERENCES["adjusted_bar"],
            calendar_reference=REFERENCES["calendar"],
            feature_reference=REFERENCES["feature"],
            label_reference=REFERENCES["label"],
            pit_lineage_valid=True,
            adjustment_verified=True,
            calendar_complete=True,
            feature_schema_valid=True,
            label_window_complete=True,
            missing_core_features=(),
        )
        for anchor in anchors
        for symbol in SEED_UNIVERSE
        for horizon in (1, 5, 20)
    ]


def _build(
    rows: list[M7EligibilityInputRow],
    anchors: tuple[date, ...],
):
    return build_m7_eligibility_manifest(
        rows,
        expected_symbols=SEED_UNIVERSE,
        expected_anchor_sessions=anchors,
        label_threshold_artifacts=(THRESHOLD_REFERENCE,),
    )


def test_manifest_binds_exact_seed100_grain_lineage_and_threshold_artifacts() -> None:
    anchors = (date(2026, 8, 20), date(2026, 8, 21))
    manifest = _build(_rows(anchors), anchors)

    assert manifest.gate_passed is True
    assert manifest.universe_version == SEED_UNIVERSE_VERSION
    assert manifest.symbol_count == 100
    assert manifest.expected_row_count == 600
    assert manifest.eligible_row_count == 600
    assert manifest.feature_set_version == M7_FEATURE_SET_VERSION
    assert manifest.label_version == M7_LABEL_VERSION
    assert manifest.label_threshold_artifacts == (THRESHOLD_REFERENCE,)
    assert len(manifest.manifest_hash) == 64
    assert manifest.rows[0].sample_key == (
        "US.SPY",
        date(2026, 8, 20),
        1,
    )
    assert manifest.rows[0].eligible is True
    assert manifest.rows[0].exclusion_reasons == ()


@pytest.mark.parametrize(
    ("overrides", "expected_reason"),
    [
        ({"pit_lineage_valid": False}, "pit_lineage_invalid"),
        ({"adjustment_verified": False}, "adjustment_unverified"),
        ({"calendar_complete": False}, "calendar_incomplete"),
        ({"feature_schema_valid": False}, "feature_schema_invalid"),
        ({"label_window_complete": False}, "label_window_incomplete"),
        (
            {"missing_core_features": ("return_20",)},
            "required_core_features_missing",
        ),
    ],
)
def test_each_provenance_failure_is_excluded_with_a_machine_reason(
    overrides: dict[str, object],
    expected_reason: str,
) -> None:
    anchors = tuple(date(2026, 7, 1) + timedelta(days=index) for index in range(20))
    rows = _rows(anchors)
    target = rows[0]
    rows[0] = M7EligibilityInputRow(
        **{
            **target.as_dict(),
            **overrides,
        }
    )

    manifest = _build(rows, anchors)

    assert manifest.gate_passed is True
    assert manifest.rows[0].eligible is False
    assert expected_reason in manifest.rows[0].exclusion_reasons
    assert manifest.exclusion_counts[expected_reason] == 1


def test_missing_source_row_is_explicit_and_cannot_be_hidden() -> None:
    anchors = tuple(date(2026, 7, 1) + timedelta(days=index) for index in range(20))
    rows = _rows(anchors)
    rows.pop()

    manifest = _build(rows, anchors)

    assert manifest.expected_row_count == len(rows) + 1
    assert manifest.exclusion_counts["source_row_missing"] == 1


def test_symbol_coverage_below_ninety_percent_fails_the_run() -> None:
    anchors = tuple(date(2026, 7, 1) + timedelta(days=index) for index in range(20))
    rows = _rows(anchors)
    target_symbol = SEED_UNIVERSE[0]
    affected = [index for index, row in enumerate(rows) if row.symbol == target_symbol][
        :7
    ]
    for index in affected:
        row = rows[index]
        rows[index] = M7EligibilityInputRow(
            **{**row.as_dict(), "adjustment_verified": False}
        )

    with pytest.raises(M7DatasetCoverageError) as raised:
        _build(rows, anchors)

    assert "symbol_coverage_below_0_90" in raised.value.failure_codes
    assert raised.value.manifest.symbol_coverage[target_symbol] < 0.90
    assert raised.value.manifest.symbol_count == 100


def test_horizon_coverage_below_ninety_five_percent_fails_without_shrinking() -> None:
    anchors = tuple(date(2026, 7, 1) + timedelta(days=index) for index in range(2))
    rows = _rows(anchors)
    affected = [index for index, row in enumerate(rows) if row.horizon == 20][:11]
    for index in affected:
        row = rows[index]
        rows[index] = M7EligibilityInputRow(
            **{**row.as_dict(), "label_window_complete": False}
        )

    with pytest.raises(M7DatasetCoverageError) as raised:
        _build(rows, anchors)

    assert "horizon_20_coverage_below_0_95" in raised.value.failure_codes
    assert raised.value.manifest.horizon_coverage[20] < 0.95
    assert raised.value.manifest.symbols == SEED_UNIVERSE


def test_universe_cannot_be_changed_to_make_coverage_pass() -> None:
    anchors = (date(2026, 8, 21),)

    with pytest.raises(M7DatasetValidationError, match="must_equal_seed100"):
        build_m7_eligibility_manifest(
            _rows(anchors),
            expected_symbols=SEED_UNIVERSE[1:],
            expected_anchor_sessions=anchors,
            label_threshold_artifacts=(THRESHOLD_REFERENCE,),
        )


def test_duplicate_and_out_of_contract_rows_fail_loudly() -> None:
    anchors = (date(2026, 8, 21),)
    rows = _rows(anchors)

    with pytest.raises(M7DatasetValidationError, match="duplicate_sample_key"):
        _build([*rows, rows[0]], anchors)

    invalid = rows[0]
    rows[0] = M7EligibilityInputRow(
        **{**invalid.as_dict(), "dataset_revision": "bars-r8"}
    )
    with pytest.raises(M7DatasetValidationError, match="mixed_dataset_revision"):
        _build(rows, anchors)
