from __future__ import annotations

from types import SimpleNamespace

import pytest

from stock_prediction.live.m7_cuda import M7CudaPreflightError, validate_m7_cuda_runtime


def test_cuda_preflight_rejects_cpu_only_torch_before_candidate_search() -> None:
    torch = SimpleNamespace(
        __version__="2.8.0+cpu",
        version=SimpleNamespace(cuda=None),
        cuda=SimpleNamespace(is_available=lambda: False),
    )

    with pytest.raises(M7CudaPreflightError, match="m7_cuda_preflight_failed"):
        validate_m7_cuda_runtime(torch_module=torch)
