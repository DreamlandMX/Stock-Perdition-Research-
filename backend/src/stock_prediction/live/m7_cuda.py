"""Fail-closed CUDA readiness checks for M7 deep trials."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import subprocess
from typing import Any


_MINIMUM_FREE_BYTES = 10 * 1024**3
_REQUIRED_TORCH_PREFIX = "2.8.0+cu128"


class M7CudaPreflightError(RuntimeError):
    """The v10 deep-trial runtime is not exactly the approved CUDA runtime."""


@dataclass(frozen=True, slots=True)
class M7CudaRuntime:
    torch_version: str
    cuda_version: str
    cudnn_version: int | None
    device_name: str
    device_index: int
    free_memory_bytes: int
    total_memory_bytes: int
    driver_version: str | None

    def payload(self) -> dict[str, str | int | None]:
        return asdict(self)


def validate_m7_cuda_runtime(*, torch_module: Any | None = None) -> M7CudaRuntime:
    """Verify the one approved GPU runtime before an operation can train."""

    if torch_module is None:
        import torch

        torch_module = torch
    try:
        version = str(torch_module.__version__)
        cuda_version = getattr(torch_module.version, "cuda", None)
        cuda = torch_module.cuda
        if (
            not version.startswith(_REQUIRED_TORCH_PREFIX)
            or not isinstance(cuda_version, str)
            or not cuda.is_available()
            or int(cuda.device_count()) < 1
        ):
            raise M7CudaPreflightError("m7_cuda_preflight_failed")
        free_memory, total_memory = cuda.mem_get_info(0)
        if int(free_memory) < _MINIMUM_FREE_BYTES:
            raise M7CudaPreflightError("m7_cuda_preflight_failed")
        torch_module.use_deterministic_algorithms(True)
        torch_module.backends.cuda.matmul.allow_tf32 = False
        torch_module.backends.cudnn.allow_tf32 = False
        probe = torch_module.ones((8, 8), device="cuda", requires_grad=True)
        (probe.square().mean()).backward()
        cuda.synchronize(0)
        return M7CudaRuntime(
            torch_version=version,
            cuda_version=cuda_version,
            cudnn_version=torch_module.backends.cudnn.version(),
            device_name=str(cuda.get_device_name(0)),
            device_index=0,
            free_memory_bytes=int(free_memory),
            total_memory_bytes=int(total_memory),
            driver_version=_driver_version(),
        )
    except M7CudaPreflightError:
        raise
    except Exception as error:
        raise M7CudaPreflightError("m7_cuda_preflight_failed") from error


def _driver_version() -> str | None:
    try:
        completed = subprocess.run(
            ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    value = completed.stdout.splitlines()
    return value[0].strip() if value and value[0].strip() else None


__all__ = ["M7CudaPreflightError", "M7CudaRuntime", "validate_m7_cuda_runtime"]
