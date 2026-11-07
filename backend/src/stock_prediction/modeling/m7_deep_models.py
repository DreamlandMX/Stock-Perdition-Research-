"""Deterministic global DLinear and PatchTST direction candidates for M7."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import math
import os
from pathlib import Path
import random
import tempfile
import time
from typing import Any, Literal, cast

import numpy as np
import torch
from torch import nn
from torch.nn import functional as functional

from stock_prediction.modeling.m7_candidates import (
    M7CandidateTerminalError,
    resolve_m7_candidate_configuration,
)
from stock_prediction.live.candidate_recipes import (
    CANDIDATE_RECIPE_SEED100_TARGETED_V4,
)
from stock_prediction.modeling.m7_sequences import M7SequenceBatch


class M7DeepModelTerminalError(RuntimeError):
    """A deep configuration is terminally invalid, failed, or non-deterministic."""


class DLinearDirectionModel(nn.Module):
    def __init__(
        self,
        *,
        sequence_length: int,
        feature_count: int,
        hidden_size: int,
        moving_average: int = 7,
    ) -> None:
        super().__init__()
        if moving_average < 1 or moving_average > sequence_length:
            raise ValueError("DLinear moving average is invalid")
        self.sequence_length = sequence_length
        self.feature_count = feature_count
        self.moving_average = moving_average
        self.trend_projection = nn.Linear(sequence_length, hidden_size)
        self.seasonal_projection = nn.Linear(sequence_length, hidden_size)
        self.normalization = nn.LayerNorm(hidden_size)
        self.heads = nn.ModuleDict(
            {str(horizon): nn.Linear(hidden_size, 3) for horizon in (1, 5, 20)}
        )

    def forward(self, features: torch.Tensor) -> dict[int, torch.Tensor]:
        if features.ndim != 3 or features.shape[1:] != (
            self.sequence_length,
            self.feature_count,
        ):
            raise M7DeepModelTerminalError("m7_deep_input_shape_invalid")
        transposed = features.transpose(1, 2)
        padded = functional.pad(
            transposed,
            (self.moving_average - 1, 0),
            mode="replicate",
        )
        trend = functional.avg_pool1d(
            padded,
            kernel_size=self.moving_average,
            stride=1,
        )
        seasonal = transposed - trend
        encoded = self.trend_projection(trend) + self.seasonal_projection(seasonal)
        pooled = self.normalization(encoded.mean(dim=1))
        return {horizon: self.heads[str(horizon)](pooled) for horizon in (1, 5, 20)}


class PatchTSTDirectionModel(nn.Module):
    def __init__(
        self,
        *,
        sequence_length: int,
        feature_count: int,
        patch_length: int,
        d_model: int,
        encoder_layers: int,
        attention_heads: int,
        dropout: float,
    ) -> None:
        super().__init__()
        if sequence_length % patch_length != 0:
            raise ValueError("PatchTST sequence length must divide into patches")
        self.sequence_length = sequence_length
        self.feature_count = feature_count
        self.patch_length = patch_length
        self.patch_count = sequence_length // patch_length
        self.patch_projection = nn.Linear(patch_length * feature_count, d_model)
        self.position = nn.Parameter(torch.zeros(1, self.patch_count, d_model))
        layer = nn.TransformerEncoderLayer(
            d_model=d_model,
            nhead=attention_heads,
            dim_feedforward=d_model * 4,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.encoder = nn.TransformerEncoder(layer, num_layers=encoder_layers)
        self.normalization = nn.LayerNorm(d_model)
        self.heads = nn.ModuleDict(
            {str(horizon): nn.Linear(d_model, 3) for horizon in (1, 5, 20)}
        )

    def forward(self, features: torch.Tensor) -> dict[int, torch.Tensor]:
        if features.ndim != 3 or features.shape[1:] != (
            self.sequence_length,
            self.feature_count,
        ):
            raise M7DeepModelTerminalError("m7_deep_input_shape_invalid")
        patches = features.unfold(1, self.patch_length, self.patch_length)
        patches = patches.permute(0, 1, 3, 2).contiguous()
        tokens = self.patch_projection(patches.flatten(start_dim=2)) + self.position
        encoded = self.encoder(tokens)
        pooled = self.normalization(encoded.mean(dim=1))
        return {horizon: self.heads[str(horizon)](pooled) for horizon in (1, 5, 20)}


@dataclass(frozen=True, slots=True)
class M7DeepCandidate:
    configuration_id: str
    family: Literal["dlinear", "patchtst"]
    model: Any
    feature_names: tuple[str, ...]
    sequence_length: int
    best_epoch: int
    selection_macro_brier: float
    state_hash: str
    seed: int
    device: str
    batch_size: int
    promotion_eligible: bool
    ineligibility_reason: str | None
    completed_epochs: int = 0


def weighted_multitask_cross_entropy(
    logits: Mapping[int, torch.Tensor],
    *,
    labels: torch.Tensor,
    weights: torch.Tensor,
    availability: torch.Tensor,
) -> torch.Tensor:
    if (
        set(logits) != {1, 5, 20}
        or labels.ndim != 2
        or labels.shape[1] != 3
        or weights.shape != labels.shape
        or availability.shape != labels.shape
    ):
        raise M7DeepModelTerminalError("m7_multitask_shape_invalid")
    losses: list[torch.Tensor] = []
    for column, horizon in enumerate((1, 5, 20)):
        mask = availability[:, column].bool()
        if int(mask.sum().item()) == 0:
            raise M7DeepModelTerminalError("m7_deep_empty_horizon_batch")
        row_losses = functional.cross_entropy(
            logits[horizon][mask],
            labels[mask, column],
            reduction="none",
        )
        horizon_weights = weights[mask, column]
        losses.append(
            torch.sum(row_losses * horizon_weights) / torch.sum(horizon_weights)
        )
    return torch.stack(losses).mean()


def fit_m7_deep_candidate(
    *,
    configuration_id: str,
    train_batch: M7SequenceBatch,
    selection_batch: M7SequenceBatch,
    synthetic_epoch_cap: int | None = None,
    timeout_seconds: float | None = None,
    device: str = "cpu",
    batch_size: int = 512,
    recipe_id: str = CANDIDATE_RECIPE_SEED100_TARGETED_V4,
    full_iterations: bool = False,
    checkpoint_path: Path | None = None,
    checkpoint_identity: str | None = None,
    cancellation_check: Callable[[], bool] | None = None,
) -> M7DeepCandidate:
    """Resume epoch boundaries; identity must bind the caller's exact input data.

    Full iterations ignores patience, but retains the synthetic cap and best-model
    selection. Timeout applies to this invocation. Use one writer per checkpoint.
    """
    if checkpoint_path is not None and (
        not isinstance(checkpoint_identity, str) or not checkpoint_identity.strip()
    ):
        raise M7DeepModelTerminalError("m7_deep_checkpoint_identity_invalid")
    try:
        configuration = resolve_m7_candidate_configuration(
            configuration_id,
            recipe_id=recipe_id,
        )
    except M7CandidateTerminalError as error:
        raise M7DeepModelTerminalError(str(error)) from error
    if configuration.family not in {"dlinear", "patchtst"}:
        raise M7DeepModelTerminalError("m7_deep_family_mismatch")
    if timeout_seconds is not None and timeout_seconds <= 0:
        raise M7DeepModelTerminalError("m7_deep_timeout")
    if (
        not isinstance(batch_size, int)
        or isinstance(batch_size, bool)
        or batch_size < 1
    ):
        raise M7DeepModelTerminalError("m7_deep_batch_size_invalid")
    expected_length = int(configuration.parameters["sequence_length"])
    if (
        train_batch.sequence_length != expected_length
        or selection_batch.sequence_length != expected_length
    ):
        raise M7DeepModelTerminalError("m7_deep_sequence_length_mismatch")
    if (
        train_batch.feature_names != selection_batch.feature_names
        or train_batch.feature_count != selection_batch.feature_count
    ):
        raise M7DeepModelTerminalError("m7_deep_feature_contract_mismatch")
    _validate_batch(train_batch)
    _validate_batch(selection_batch)
    if device != "cpu" and not (device == "cuda" and torch.cuda.is_available()):
        raise M7DeepModelTerminalError("m7_deep_device_unavailable")
    configured_epochs = int(configuration.parameters["max_epochs"])
    epochs, promotion_eligible, reason = _epoch_policy(
        configured_epochs,
        synthetic_epoch_cap,
    )
    _set_determinism(configuration.seed)
    target_device = torch.device(device)
    model = _build_model(
        configuration.family,
        parameters=configuration.parameters,
        feature_count=train_batch.feature_count,
    ).to(target_device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3, weight_decay=1e-4)
    train_tensors = (
        train_batch.sequences,
        train_batch.labels,
        train_batch.weights,
        train_batch.availability,
    )
    best_epoch = 0
    best_brier = float("inf")
    best_state: dict[str, torch.Tensor] | None = None
    without_improvement = 0
    patience = int(configuration.parameters["patience"])
    completed_epochs = 0
    checkpoint_config = {
        "configuration_id": configuration.configuration_id,
        "family": configuration.family,
        "parameters": dict(configuration.parameters),
        "seed": configuration.seed,
        "recipe_id": recipe_id,
        "feature_names": train_batch.feature_names,
        "feature_count": train_batch.feature_count,
        "device": device,
        "batch_size": batch_size,
        "synthetic_epoch_cap": synthetic_epoch_cap,
        "full_iterations": full_iterations,
    }
    if checkpoint_path is not None and checkpoint_path.exists():
        try:
            checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=True)
            if (
                checkpoint["version"] != 1
                or checkpoint["identity"] != checkpoint_identity
                or checkpoint["config"] != checkpoint_config
            ):
                raise M7DeepModelTerminalError("m7_deep_checkpoint_mismatch")
            completed_epochs = checkpoint["completed_epochs"]
            best_epoch = checkpoint["best_epoch"]
            best_brier = checkpoint["best_brier"]
            best_state = checkpoint["best"]
            without_improvement = checkpoint["without_improvement"]
            if (
                type(completed_epochs) is not int
                or not 1 <= completed_epochs <= epochs
                or type(best_epoch) is not int
                or not 1 <= best_epoch <= completed_epochs
                or not math.isfinite(best_brier)
                or best_state is None
                or type(without_improvement) is not int
                or without_improvement != completed_epochs - best_epoch
            ):
                raise M7DeepModelTerminalError("m7_deep_checkpoint_invalid")
            model.load_state_dict(best_state)
            model.load_state_dict(checkpoint["model"])
            optimizer.load_state_dict(checkpoint["optimizer"])
            for state in optimizer.state.values():
                for name, value in state.items():
                    if isinstance(value, torch.Tensor):
                        state[name] = value.to(target_device)
            _restore_rng(checkpoint["rng"])
        except M7DeepModelTerminalError:
            raise
        except Exception as error:
            raise M7DeepModelTerminalError("m7_deep_checkpoint_invalid") from error
    started = time.monotonic()
    try:
        for epoch in range(completed_epochs + 1, epochs + 1):
            if not full_iterations and without_improvement >= patience and completed_epochs:
                break
            model.train()
            generator = torch.Generator(device="cpu")
            generator.manual_seed(configuration.seed + epoch)
            order = torch.randperm(len(train_batch.sample_keys), generator=generator)
            for start in range(0, len(order), batch_size):
                positions = order[start : start + batch_size]
                features = train_tensors[0][positions].to(target_device)
                labels = train_tensors[1][positions].to(target_device)
                weights = train_tensors[2][positions].to(target_device)
                availability = train_tensors[3][positions].to(target_device)
                optimizer.zero_grad(set_to_none=True)
                logits = model(features)
                loss = weighted_multitask_cross_entropy(
                    logits,
                    labels=labels,
                    weights=weights,
                    availability=availability,
                )
                if not torch.isfinite(loss):
                    raise M7DeepModelTerminalError("m7_deep_nan_loss")
                torch.autograd.backward(loss)
                optimizer.step()
            model.eval()
            with torch.no_grad():
                selection_logits = _predict_logits_in_batches(
                    model,
                    selection_batch.sequences,
                    batch_size=batch_size,
                    device=target_device,
                )
                brier = _macro_brier(
                    selection_logits,
                    labels=selection_batch.labels,
                    availability=selection_batch.availability,
                )
            if brier < best_brier:
                best_brier = brier
                best_epoch = epoch
                best_state = {
                    name: tensor.detach().cpu().clone()
                    for name, tensor in model.state_dict().items()
                }
                without_improvement = 0
            else:
                without_improvement += 1
            completed_epochs = epoch
            if checkpoint_path is not None:
                _save_checkpoint(checkpoint_path, {
                    "version": 1,
                    "identity": checkpoint_identity,
                    "config": checkpoint_config,
                    "model": model.state_dict(),
                    "best": best_state,
                    "optimizer": optimizer.state_dict(),
                    "rng": _capture_rng(),
                    "completed_epochs": completed_epochs,
                    "best_epoch": best_epoch,
                    "best_brier": best_brier,
                    "without_improvement": without_improvement,
                })
            if cancellation_check is not None and cancellation_check():
                raise M7DeepModelTerminalError("m7_deep_cancelled")
            if not full_iterations and without_improvement >= patience:
                break
            if (
                timeout_seconds is not None
                and time.monotonic() - started > timeout_seconds
            ):
                raise M7DeepModelTerminalError("m7_deep_timeout")
    except torch.cuda.OutOfMemoryError as error:
        raise M7DeepModelTerminalError("m7_deep_oom") from error
    except MemoryError as error:
        raise M7DeepModelTerminalError("m7_deep_oom") from error
    except RuntimeError as error:
        if _is_torch_allocator_oom(error):
            raise M7DeepModelTerminalError("m7_deep_oom") from error
        raise
    if best_state is None or best_epoch < 1 or not math.isfinite(best_brier):
        raise M7DeepModelTerminalError("m7_deep_no_valid_epoch")
    model.load_state_dict(deepcopy(best_state))
    model.eval()
    state_hash = _state_hash(best_state)
    candidate = M7DeepCandidate(
        configuration_id=configuration.configuration_id,
        family=configuration.family,
        model=model,
        feature_names=train_batch.feature_names,
        sequence_length=expected_length,
        best_epoch=best_epoch,
        selection_macro_brier=best_brier,
        state_hash=state_hash,
        seed=configuration.seed,
        device=device,
        batch_size=batch_size,
        promotion_eligible=promotion_eligible,
        ineligibility_reason=reason,
        completed_epochs=completed_epochs,
    )
    predict_m7_deep_candidate(candidate, selection_batch)
    return candidate


def _capture_rng() -> dict[str, Any]:
    numpy_state = cast(tuple[Any, ...], np.random.get_state())
    return {
        "python": random.getstate(),
        # Primitive lists keep checkpoints compatible with weights_only=True.
        "numpy": (numpy_state[0], numpy_state[1].tolist(), *numpy_state[2:]),
        "torch": torch.get_rng_state(),
        "cuda": torch.cuda.get_rng_state_all() if torch.cuda.is_available() else [],
    }


def _restore_rng(state: Mapping[str, Any]) -> None:
    random.setstate(state["python"])
    numpy_state = cast(tuple[Any, ...], state["numpy"])
    np.random.set_state((numpy_state[0], np.asarray(numpy_state[1], dtype=np.uint32), *numpy_state[2:]))
    torch.set_rng_state(state["torch"])
    if state["cuda"]:
        if not torch.cuda.is_available() or len(state["cuda"]) != torch.cuda.device_count():
            raise M7DeepModelTerminalError("m7_deep_checkpoint_cuda_mismatch")
        torch.cuda.set_rng_state_all(state["cuda"])


def _save_checkpoint(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp", delete=False) as stream:
            temporary = Path(stream.name)
            torch.save(payload, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _is_torch_allocator_oom(error: RuntimeError) -> bool:
    message = str(error).lower()
    return "out of memory" in message or (
        "defaultcpuallocator" in message and "not enough memory" in message
    )


def predict_m7_deep_candidate(
    candidate: M7DeepCandidate,
    batch: M7SequenceBatch,
) -> Mapping[int, np.ndarray]:
    if (
        batch.sequence_length != candidate.sequence_length
        or batch.feature_names != candidate.feature_names
    ):
        raise M7DeepModelTerminalError("m7_deep_prediction_contract_mismatch")
    _validate_batch(batch)
    device = torch.device(candidate.device)
    candidate.model.eval()
    with torch.no_grad():
        logits = _predict_logits_in_batches(
            candidate.model,
            batch.sequences,
            batch_size=candidate.batch_size,
            device=device,
        )
        output = {
            horizon: _normalized_probability_array(torch.softmax(values, dim=1).numpy())
            for horizon, values in logits.items()
        }
    return output


def verify_m7_probability_replay(
    reference: Mapping[int, np.ndarray],
    replay: Mapping[int, np.ndarray],
    *,
    device: Literal["cpu", "cuda"],
) -> None:
    tolerance = 1e-7 if device == "cpu" else 1e-6
    if set(reference) != {1, 5, 20} or set(replay) != {1, 5, 20}:
        raise M7DeepModelTerminalError("m7_deep_non_deterministic")
    if any(
        np.asarray(reference[horizon]).shape != np.asarray(replay[horizon]).shape
        or not np.allclose(
            reference[horizon],
            replay[horizon],
            rtol=0.0,
            atol=tolerance,
        )
        for horizon in (1, 5, 20)
    ):
        raise M7DeepModelTerminalError("m7_deep_non_deterministic")


def _build_model(
    family: str,
    *,
    parameters: Mapping[str, object],
    feature_count: int,
) -> nn.Module:
    if family == "dlinear":
        return DLinearDirectionModel(
            sequence_length=_int_parameter(parameters, "sequence_length"),
            feature_count=feature_count,
            hidden_size=_int_parameter(parameters, "hidden_size"),
        )
    if family == "patchtst":
        return PatchTSTDirectionModel(
            sequence_length=_int_parameter(parameters, "sequence_length"),
            feature_count=feature_count,
            patch_length=_int_parameter(parameters, "patch_length"),
            d_model=_int_parameter(parameters, "d_model"),
            encoder_layers=_int_parameter(parameters, "encoder_layers"),
            attention_heads=_int_parameter(parameters, "attention_heads"),
            dropout=_float_parameter(parameters, "dropout"),
        )
    raise M7DeepModelTerminalError("m7_deep_family_mismatch")


def _int_parameter(parameters: Mapping[str, object], name: str) -> int:
    value = parameters.get(name)
    if not isinstance(value, int) or isinstance(value, bool):
        raise M7DeepModelTerminalError(f"m7_deep_parameter_invalid:{name}")
    return value


def _float_parameter(parameters: Mapping[str, object], name: str) -> float:
    value = parameters.get(name)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise M7DeepModelTerminalError(f"m7_deep_parameter_invalid:{name}")
    return float(value)


def _set_determinism(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    torch.set_num_threads(4)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False


def _batch_to_device(
    batch: M7SequenceBatch,
    device: torch.device,
) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    return (
        batch.sequences.to(device),
        batch.labels.to(device),
        batch.weights.to(device),
        batch.availability.to(device),
    )


def _predict_logits_in_batches(
    model: nn.Module,
    sequences: torch.Tensor,
    *,
    batch_size: int,
    device: torch.device,
) -> dict[int, torch.Tensor]:
    outputs: dict[int, list[torch.Tensor]] = {1: [], 5: [], 20: []}
    for start in range(0, len(sequences), batch_size):
        logits = model(sequences[start : start + batch_size].to(device))
        for horizon, values in logits.items():
            outputs[horizon].append(values.detach().cpu())
    if any(not values for values in outputs.values()):
        raise M7DeepModelTerminalError("m7_deep_empty_prediction_batch")
    return {horizon: torch.cat(values, dim=0) for horizon, values in outputs.items()}


def _validate_batch(batch: M7SequenceBatch) -> None:
    if (
        batch.sequences.ndim != 3
        or not bool(torch.isfinite(batch.sequences).all())
        or not bool(torch.isfinite(batch.weights).all())
        or bool((batch.weights <= 0).any())
        or batch.labels.shape != batch.weights.shape
        or batch.labels.shape != batch.availability.shape
        or batch.labels.shape[1] != 3
    ):
        raise M7DeepModelTerminalError("m7_deep_batch_non_finite_or_invalid")
    if any(int(batch.availability[:, column].sum().item()) == 0 for column in range(3)):
        raise M7DeepModelTerminalError("m7_deep_empty_horizon_batch")


def _macro_brier(
    logits: Mapping[int, torch.Tensor],
    *,
    labels: torch.Tensor,
    availability: torch.Tensor,
) -> float:
    scores: list[float] = []
    for column, horizon in enumerate((1, 5, 20)):
        mask = availability[:, column].bool()
        if int(mask.sum().item()) == 0:
            raise M7DeepModelTerminalError("m7_deep_empty_horizon_batch")
        probabilities = torch.softmax(logits[horizon][mask], dim=1)
        one_hot = functional.one_hot(labels[mask, column], num_classes=3).float()
        scores.append(
            float(torch.mean(torch.sum((probabilities - one_hot) ** 2, dim=1)))
        )
    return float(np.mean(scores))


def _epoch_policy(
    configured_epochs: int,
    synthetic_epoch_cap: int | None,
) -> tuple[int, bool, str | None]:
    if synthetic_epoch_cap is None:
        return configured_epochs, True, None
    if synthetic_epoch_cap < 1 or synthetic_epoch_cap > configured_epochs:
        raise M7DeepModelTerminalError("m7_synthetic_epoch_cap_invalid")
    return synthetic_epoch_cap, False, "synthetic_epoch_cap"


def _state_hash(state: Mapping[str, torch.Tensor]) -> str:
    digest = hashlib.sha256()
    for name in sorted(state):
        tensor = state[name].detach().cpu().contiguous()
        digest.update(name.encode("utf-8"))
        digest.update(str(tensor.dtype).encode("ascii"))
        digest.update(np.asarray(tensor.shape, dtype=np.int64).tobytes())
        digest.update(tensor.numpy().tobytes())
    return digest.hexdigest()


def _normalized_probability_array(values: np.ndarray) -> np.ndarray:
    probabilities = np.asarray(values, dtype=np.float64)
    probabilities /= probabilities.sum(axis=1, keepdims=True)
    if (
        not np.all(np.isfinite(probabilities))
        or np.any(probabilities < 0)
        or np.any(probabilities > 1)
        or not np.allclose(probabilities.sum(axis=1), 1.0, rtol=0.0, atol=1e-9)
    ):
        raise M7DeepModelTerminalError("m7_deep_probability_invalid")
    return probabilities


__all__ = [
    "DLinearDirectionModel",
    "M7DeepCandidate",
    "M7DeepModelTerminalError",
    "PatchTSTDirectionModel",
    "fit_m7_deep_candidate",
    "predict_m7_deep_candidate",
    "verify_m7_probability_replay",
    "weighted_multitask_cross_entropy",
]
