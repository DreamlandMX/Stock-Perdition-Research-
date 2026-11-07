from __future__ import annotations

from datetime import date, timedelta
from dataclasses import replace

import numpy as np
import pytest
import torch

from stock_prediction.modeling.m7_deep_models import (
    DLinearDirectionModel,
    M7DeepModelTerminalError,
    PatchTSTDirectionModel,
    fit_m7_deep_candidate,
    predict_m7_deep_candidate,
    verify_m7_probability_replay,
    weighted_multitask_cross_entropy,
)
from stock_prediction.modeling.m7_recipe import registered_m7_candidate_matrix
from stock_prediction.modeling.m7_sequences import (
    M7SequenceInputError,
    make_m7_sequence_batch,
)


def _batch(*, sequence_length: int = 64, count: int = 12):
    anchors = tuple(date(2026, 1, 20) + timedelta(days=index) for index in range(count))
    keys = tuple(
        (f"US.{count - index:03d}", anchor) for index, anchor in enumerate(anchors)
    )
    sequences = np.asarray(
        [
            [
                [
                    sample_index * 0.01 + step * 0.001 + feature * 0.0001
                    for feature in range(4)
                ]
                for step in range(sequence_length)
            ]
            for sample_index in range(count)
        ],
        dtype=np.float32,
    )
    input_sessions = tuple(
        tuple(
            anchor - timedelta(days=sequence_length - 1 - offset)
            for offset in range(sequence_length)
        )
        for anchor in anchors
    )
    labels = {
        1: np.asarray([index % 3 for index in range(count)]),
        5: np.asarray([(index + 1) % 3 for index in range(count)]),
        20: np.asarray([(index + 2) % 3 for index in range(count)]),
    }
    weights = {horizon: np.ones(count, dtype=np.float32) for horizon in (1, 5, 20)}
    masks = {horizon: np.ones(count, dtype=bool) for horizon in (1, 5, 20)}
    return make_m7_sequence_batch(
        sample_keys=keys,
        input_sessions=input_sessions,
        sequences=sequences,
        labels_by_horizon=labels,
        weights_by_horizon=weights,
        availability_by_horizon=masks,
        feature_names=("return_1", "return_20", "volatility_20", "atr_pct_20"),
    )


def test_sequence_batch_uses_stable_anchor_symbol_order_without_cross_sample_normalization() -> (
    None
):
    batch = _batch()

    assert batch.sample_keys == tuple(
        sorted(batch.sample_keys, key=lambda key: (key[1], key[0]))
    )
    assert batch.sequences.shape == (12, 64, 4)
    assert batch.input_sessions[0][-1] == batch.sample_keys[0][1]
    assert float(batch.sequences[0, -1, 0]) != pytest.approx(0.0)
    assert batch.feature_names == (
        "return_1",
        "return_20",
        "volatility_20",
        "atr_pct_20",
    )


def test_sequence_batch_preserves_sample_identity_when_sorting() -> None:
    anchors = (date(2026, 1, 22), date(2026, 1, 20), date(2026, 1, 21))
    keys = (("US.C", anchors[0]), ("US.A", anchors[1]), ("US.B", anchors[2]))
    values = np.asarray([[[30.0]], [[10.0]], [[20.0]]], dtype=np.float32)

    batch = make_m7_sequence_batch(
        sample_keys=keys,
        input_sessions=tuple((anchor,) for anchor in anchors),
        sequences=values,
        labels_by_horizon={horizon: np.asarray([2, 0, 1]) for horizon in (1, 5, 20)},
        weights_by_horizon={
            horizon: np.asarray([3.0, 1.0, 2.0]) for horizon in (1, 5, 20)
        },
        availability_by_horizon={
            horizon: np.asarray([True, False, True]) for horizon in (1, 5, 20)
        },
        feature_names=("identity",),
    )

    assert batch.sample_keys == (
        ("US.A", date(2026, 1, 20)),
        ("US.B", date(2026, 1, 21)),
        ("US.C", date(2026, 1, 22)),
    )
    assert batch.sequences[:, 0, 0].tolist() == [10.0, 20.0, 30.0]
    assert batch.labels[:, 0].tolist() == [0, 1, 2]
    assert batch.weights[:, 0].tolist() == [1.0, 2.0, 3.0]
    assert batch.availability[:, 0].tolist() == [False, True, True]


def test_sequence_batch_rejects_future_or_duplicate_input_sessions() -> None:
    anchor = date(2026, 1, 20)
    common = {
        "sample_keys": (("US.A", anchor),),
        "sequences": np.zeros((1, 2, 1), dtype=np.float32),
        "labels_by_horizon": {horizon: np.asarray([1]) for horizon in (1, 5, 20)},
        "weights_by_horizon": {horizon: np.asarray([1.0]) for horizon in (1, 5, 20)},
        "availability_by_horizon": {
            horizon: np.asarray([True]) for horizon in (1, 5, 20)
        },
        "feature_names": ("return_1",),
    }

    with pytest.raises(M7SequenceInputError, match="window_invalid"):
        make_m7_sequence_batch(
            **common,
            input_sessions=((anchor, anchor + timedelta(days=1)),),
        )
    with pytest.raises(M7SequenceInputError, match="window_invalid"):
        make_m7_sequence_batch(
            **common,
            input_sessions=((anchor, anchor),),
        )


def test_multitask_loss_uses_equal_horizon_weights_and_rejects_empty_horizon() -> None:
    logits = {
        horizon: torch.tensor([[2.0, 0.0, 0.0], [0.0, 2.0, 0.0]])
        for horizon in (1, 5, 20)
    }
    labels = torch.tensor([[0, 0, 0], [1, 1, 1]])
    weights = torch.ones((2, 3))
    availability = torch.ones((2, 3), dtype=torch.bool)

    loss = weighted_multitask_cross_entropy(
        logits,
        labels=labels,
        weights=weights,
        availability=availability,
    )
    expected = torch.nn.functional.cross_entropy(logits[1], labels[:, 0])
    assert float(loss) == pytest.approx(float(expected))

    availability[:, 2] = False
    with pytest.raises(M7DeepModelTerminalError, match="empty_horizon_batch"):
        weighted_multitask_cross_entropy(
            logits,
            labels=labels,
            weights=weights,
            availability=availability,
        )


def test_dlinear_and_patchtst_have_three_heads_and_no_symbol_embedding() -> None:
    dlinear = DLinearDirectionModel(
        sequence_length=64,
        feature_count=4,
        hidden_size=32,
    )
    patchtst = PatchTSTDirectionModel(
        sequence_length=64,
        feature_count=4,
        patch_length=8,
        d_model=64,
        encoder_layers=2,
        attention_heads=4,
        dropout=0.1,
    )
    features = torch.zeros((3, 64, 4))

    assert {key: value.shape for key, value in dlinear(features).items()} == {
        1: (3, 3),
        5: (3, 3),
        20: (3, 3),
    }
    assert {key: value.shape for key, value in patchtst(features).items()} == {
        1: (3, 3),
        5: (3, 3),
        20: (3, 3),
    }
    assert all(
        "embedding" not in name.lower() for name, _ in dlinear.named_parameters()
    )
    assert all("symbol" not in name.lower() for name, _ in patchtst.named_parameters())


def test_dlinear_fresh_process_equivalent_replay_is_deterministic_on_cpu() -> None:
    batch = _batch()
    first = fit_m7_deep_candidate(
        configuration_id="m7-dlinear-01",
        train_batch=batch,
        selection_batch=batch,
        synthetic_epoch_cap=3,
        timeout_seconds=60,
        device="cpu",
        batch_size=4,
    )
    second = fit_m7_deep_candidate(
        configuration_id="m7-dlinear-01",
        train_batch=batch,
        selection_batch=batch,
        synthetic_epoch_cap=3,
        timeout_seconds=60,
        device="cpu",
        batch_size=4,
    )
    first_predictions = predict_m7_deep_candidate(first, batch)
    second_predictions = predict_m7_deep_candidate(second, batch)

    assert first.state_hash == second.state_hash
    assert first.best_epoch == second.best_epoch
    assert first.promotion_eligible is False
    assert first.ineligibility_reason == "synthetic_epoch_cap"
    assert first.batch_size == 4
    assert all(
        np.allclose(first_predictions[horizon], second_predictions[horizon], atol=1e-7)
        for horizon in (1, 5, 20)
    )
    assert all(
        np.allclose(first_predictions[horizon].sum(axis=1), 1.0, atol=1e-9)
        for horizon in (1, 5, 20)
    )


def test_registered_deep_matrix_is_exact_and_structural_mismatch_fails() -> None:
    matrix = registered_m7_candidate_matrix()
    deep = [
        row for row in matrix.configurations if row.family in {"dlinear", "patchtst"}
    ]

    assert len([row for row in deep if row.family == "dlinear"]) == 8
    assert len([row for row in deep if row.family == "patchtst"]) == 8
    with pytest.raises(M7DeepModelTerminalError, match="sequence_length_mismatch"):
        fit_m7_deep_candidate(
            configuration_id="m7-dlinear-05",
            train_batch=_batch(sequence_length=64),
            selection_batch=_batch(sequence_length=64),
            synthetic_epoch_cap=1,
            device="cpu",
        )


def test_nan_timeout_and_non_deterministic_replay_are_terminal() -> None:
    batch = _batch()
    batch.sequences[0, 0, 0] = float("nan")
    with pytest.raises(M7DeepModelTerminalError, match="non_finite"):
        fit_m7_deep_candidate(
            configuration_id="m7-dlinear-01",
            train_batch=batch,
            selection_batch=batch,
            synthetic_epoch_cap=1,
            device="cpu",
        )

    with pytest.raises(M7DeepModelTerminalError, match="batch_size"):
        fit_m7_deep_candidate(
            configuration_id="m7-dlinear-01",
            train_batch=_batch(),
            selection_batch=_batch(),
            synthetic_epoch_cap=1,
            batch_size=0,
        )

    with pytest.raises(M7DeepModelTerminalError, match="timeout"):
        fit_m7_deep_candidate(
            configuration_id="m7-dlinear-01",
            train_batch=_batch(),
            selection_batch=_batch(),
            synthetic_epoch_cap=1,
            timeout_seconds=0,
            device="cpu",
        )

    reference = {horizon: np.full((2, 3), 1 / 3) for horizon in (1, 5, 20)}
    changed = {horizon: values.copy() for horizon, values in reference.items()}
    changed[20][0, 0] += 2e-7
    with pytest.raises(M7DeepModelTerminalError, match="non_deterministic"):
        verify_m7_probability_replay(reference, changed, device="cpu")


def test_cpu_allocator_runtime_oom_is_normalized_to_terminal_oom(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Catch PyTorch CPU OOM escaping because it is a RuntimeError, not MemoryError."""

    def fail_allocation(
        _model: DLinearDirectionModel, _features: torch.Tensor
    ) -> dict[int, torch.Tensor]:
        raise RuntimeError(
            "[enforce fail at alloc_cpu.cpp:121] data. "
            "DefaultCPUAllocator: not enough memory: you tried to allocate 802816 bytes."
        )

    monkeypatch.setattr(DLinearDirectionModel, "forward", fail_allocation)

    with pytest.raises(M7DeepModelTerminalError, match="^m7_deep_oom$"):
        fit_m7_deep_candidate(
            configuration_id="m7-dlinear-01",
            train_batch=_batch(),
            selection_batch=_batch(),
            synthetic_epoch_cap=1,
            device="cpu",
            batch_size=4,
        )


def test_full_iterations_checkpoint_resumes_at_epoch_boundary(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from stock_prediction.modeling import m7_deep_models

    original = m7_deep_models.resolve_m7_candidate_configuration(
        "m7-dlinear-01"
    )
    parameters = dict(original.parameters)
    parameters.update({"max_epochs": 4, "patience": 1})
    monkeypatch.setattr(
        m7_deep_models,
        "resolve_m7_candidate_configuration",
        lambda *args, **kwargs: replace(original, parameters=parameters),
    )
    monkeypatch.setattr(m7_deep_models, "_macro_brier", lambda *args, **kwargs: 0.5)
    checkpoint = tmp_path / "candidate.ckpt"
    calls = 0

    def cancel_after_two_epochs() -> bool:
        nonlocal calls
        calls += 1
        return calls == 2

    with pytest.raises(M7DeepModelTerminalError, match="cancelled"):
        fit_m7_deep_candidate(
            configuration_id="m7-dlinear-01",
            train_batch=_batch(count=6),
            selection_batch=_batch(count=6),
            device="cpu",
            batch_size=3,
            full_iterations=True,
            checkpoint_path=checkpoint,
            checkpoint_identity="frozen-input-v1",
            cancellation_check=cancel_after_two_epochs,
        )

    resumed = fit_m7_deep_candidate(
        configuration_id="m7-dlinear-01",
        train_batch=_batch(count=6),
        selection_batch=_batch(count=6),
        device="cpu",
        batch_size=3,
        full_iterations=True,
        checkpoint_path=checkpoint,
        checkpoint_identity="frozen-input-v1",
    )

    assert resumed.completed_epochs == 4
    assert resumed.best_epoch == 1
