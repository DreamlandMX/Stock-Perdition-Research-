from __future__ import annotations

from datetime import UTC, datetime
import json
from pathlib import Path

import pytest

from stock_prediction.modeling.m7_package import (
    M7PackageArtifactInput,
    M7PackageBuildSpec,
    M7PackageError,
    M7RuntimeCompatibility,
    load_m7_package,
    publish_m7_package,
    recover_m7_package_staging,
)


def _source_artifacts(tmp_path: Path) -> tuple[M7PackageArtifactInput, ...]:
    source = tmp_path / "sources"
    source.mkdir(parents=True)
    values = {
        "thresholds": b'{"down":-0.01,"up":0.01}',
        "model-h1": b"model-one",
        "model-h5": b"model-five",
        "model-h20": b"model-twenty",
        "calibration": b'{"method":"vector_scaling"}',
        "explanation": b'{"method":"integrated_gradients","steps":32}',
    }
    categories = {
        "thresholds": "label_threshold",
        "model-h1": "model",
        "model-h5": "model",
        "model-h20": "model",
        "calibration": "calibration",
        "explanation": "explanation",
    }
    artifacts = []
    for artifact_id, payload in values.items():
        path = source / f"{artifact_id}.bin"
        path.write_bytes(payload)
        artifacts.append(
            M7PackageArtifactInput(
                artifact_id=artifact_id,
                category=categories[artifact_id],
                source_path=path,
                schema_version="m7-test-v1",
            )
        )
    return tuple(artifacts)


def _build_spec() -> M7PackageBuildSpec:
    return M7PackageBuildSpec(
        package_id="m7-direction-shadow-001",
        bundle_id="direction",
        recipe_id="candidate-recipe-seed100-targeted-v4",
        recipe_generation=4,
        code_commit="a" * 40,
        universe_version="m1-seed-2026-07-11-v1",
        dataset_revision="opend-live-market-bars-pit-adjusted-v2",
        feature_set_version="live-daily-features-v5-seed100",
        feature_input_hash="b" * 64,
        label_version="live-labels-v2",
        split_manifest_hash="c" * 64,
        candidate_matrix_hash="d" * 64,
        dependency_lock_hash="e" * 64,
        runtime_compatibility=M7RuntimeCompatibility(
            python_version="3.11",
            platform="windows",
            architecture="x86_64",
            numpy_version="2.4.2",
            scikit_learn_version="1.9.0",
            catboost_version="1.2.10",
            torch_version="2.8.0",
            inference_devices=("cpu",),
        ),
        evidence_scope="optimization_reused",
        blind_window_id=None,
        created_at=datetime(2026, 8, 24, 12, tzinfo=UTC),
    )


def test_package_is_canonical_content_addressed_and_runtime_observations_are_separate(
    tmp_path: Path,
) -> None:
    root = tmp_path / "packages"
    published = publish_m7_package(
        root,
        build_spec=_build_spec(),
        artifacts=_source_artifacts(tmp_path),
        runtime_observations={"wall_seconds": 12.5, "peak_rss_bytes": 1234},
    )
    loaded = load_m7_package(root, "m7-direction-shadow-001")

    assert published.package_directory == root / "m7-direction-shadow-001"
    assert loaded.package_sha256 == published.manifest.package_sha256
    assert len(loaded.package_sha256) == 64
    assert loaded.bundle_id == "direction"
    assert len(loaded.model_artifacts) == 3
    assert len(loaded.calibration_artifacts) == 1
    assert len(loaded.explanation_artifacts) == 1
    assert len(loaded.label_threshold_artifacts) == 1
    assert loaded.runtime_observations_hash is not None
    assert (
        root / "runtime-observations" / f"{loaded.runtime_observations_hash}.json"
    ).is_file()
    manifest_text = (published.package_directory / "manifest.json").read_text("utf-8")
    assert str(tmp_path.resolve()) not in manifest_text
    assert (
        manifest_text
        == json.dumps(
            json.loads(manifest_text),
            ensure_ascii=True,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )
    assert all(
        not Path(item.relative_path).is_absolute() for item in loaded.all_artifacts
    )


def test_package_rejects_tampering_and_immutable_id_collision(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    published = publish_m7_package(
        root,
        build_spec=_build_spec(),
        artifacts=_source_artifacts(tmp_path),
    )
    with pytest.raises(M7PackageError, match="already_exists"):
        publish_m7_package(
            root,
            build_spec=_build_spec(),
            artifacts=_source_artifacts(tmp_path / "again"),
        )

    model_path = (
        published.package_directory
        / published.manifest.model_artifacts[0].relative_path
    )
    model_path.write_bytes(b"tamperone")
    with pytest.raises(M7PackageError, match="artifact_hash_mismatch"):
        load_m7_package(root, published.manifest.package_id)


@pytest.mark.parametrize(
    "artifact_id", ("../escape", "C:drive", "folder/name", "\\server")
)
def test_package_rejects_unsafe_artifact_identity(
    tmp_path: Path, artifact_id: str
) -> None:
    source = tmp_path / "source.bin"
    source.write_bytes(b"source")
    with pytest.raises(ValueError, match="artifact_id"):
        M7PackageArtifactInput(
            artifact_id=artifact_id,
            category="model",
            source_path=source,
            schema_version="m7-test-v1",
        )


def test_interrupted_publish_is_not_visible_and_valid_staging_can_recover(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "packages"
    real_replace = __import__("os").replace

    def fail_package_publish(source: str | Path, target: str | Path) -> None:
        if Path(target).name == "m7-direction-shadow-001":
            raise OSError("injected rename failure")
        real_replace(source, target)

    monkeypatch.setattr(
        "stock_prediction.modeling.m7_package.os.replace", fail_package_publish
    )
    with pytest.raises(M7PackageError, match="atomic_publish_failed"):
        publish_m7_package(
            root,
            build_spec=_build_spec(),
            artifacts=_source_artifacts(tmp_path),
        )
    assert not (root / "m7-direction-shadow-001").exists()

    monkeypatch.setattr("stock_prediction.modeling.m7_package.os.replace", real_replace)
    recovered = recover_m7_package_staging(root)

    assert [item.manifest.package_id for item in recovered] == [
        "m7-direction-shadow-001"
    ]
    assert load_m7_package(root, "m7-direction-shadow-001").package_id == (
        "m7-direction-shadow-001"
    )


def test_runtime_contract_requires_cpu_and_exact_dependency_versions() -> None:
    with pytest.raises(ValueError, match="cpu"):
        M7RuntimeCompatibility(
            python_version="3.11",
            platform="windows",
            architecture="x86_64",
            numpy_version="2.4.2",
            scikit_learn_version="1.9.0",
            catboost_version="1.2.10",
            torch_version="2.8.0",
            inference_devices=("cuda",),
        )


def test_package_load_rejects_incompatible_serving_runtime(tmp_path: Path) -> None:
    root = tmp_path / "packages"
    publish_m7_package(
        root,
        build_spec=_build_spec(),
        artifacts=_source_artifacts(tmp_path),
    )
    incompatible = _build_spec().runtime_compatibility.model_copy(
        update={"torch_version": "2.7.0"}
    )

    with pytest.raises(M7PackageError, match="runtime_incompatible:torch_version"):
        load_m7_package(
            root,
            "m7-direction-shadow-001",
            current_runtime=incompatible,
        )
