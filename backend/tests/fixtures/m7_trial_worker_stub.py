from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import sys
import time

from stock_prediction.modeling.m7_trial_protocol import (
    M7DeepTrialRequest,
    M7DeepTrialWorkerOutput,
    M7TrialArtifact,
    M7TrialCandidateMetadata,
)
from stock_prediction.modeling.report import write_immutable_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument(
        "--mode",
        choices=(
            "success",
            "timeout",
            "abnormal",
            "partial",
            "bad-hash",
            "peak-gpu",
            "check-cublas",
        ),
        required=True,
    )
    args = parser.parse_args()
    request = M7DeepTrialRequest.model_validate_json(
        args.request.read_text(encoding="utf-8"), strict=True
    )
    if args.mode == "check-cublas" and os.environ.get("CUBLAS_WORKSPACE_CONFIG") != ":4096:8":
        return 8
    if args.mode == "timeout":
        time.sleep(60)
        return 0
    if args.mode == "abnormal":
        return 7
    if args.mode == "partial":
        (args.staging / "partial.bin").write_bytes(b"partial")
        return 0
    artifact_root = args.staging / "artifacts"
    artifact_root.mkdir()
    artifacts: list[M7TrialArtifact] = []
    for role in ("model", "calibration_predictions", "outer_test_predictions"):
        relative = f"artifacts/{role}.bin"
        payload = f"{request.trial_id}:{role}".encode()
        path = args.staging / relative
        path.write_bytes(payload)
        digest = hashlib.sha256(payload).hexdigest()
        if args.mode == "bad-hash" and role == "model":
            digest = "0" * 64
        artifacts.append(
            M7TrialArtifact(
                role=role,
                relative_path=relative,
                sha256=digest,
                size_bytes=len(payload),
            )
        )
    output = M7DeepTrialWorkerOutput(
        trial_id=request.trial_id,
        request_hash=request.request_hash,
        status="succeeded",
        artifacts=tuple(artifacts),
        candidate_metadata=M7TrialCandidateMetadata(
            family="dlinear",
            state_hash="1" * 64,
            best_epoch=1,
            selection_macro_brier=0.5,
            promotion_eligible=True,
            ineligibility_reason=None,
            calibration_sample_keys_hash="2" * 64,
            outer_test_sample_keys_hash="3" * 64,
        ),
        terminal_failure_code=None,
        peak_gpu_memory_bytes=128 if args.mode == "peak-gpu" else 0,
    )
    write_immutable_json(
        args.staging / "worker-output.json",
        output.model_dump(mode="json"),
        pretty=False,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
