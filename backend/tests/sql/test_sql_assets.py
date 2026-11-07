"""Tests for versioned DuckDB SQL assets."""

from pathlib import Path
import tomllib

import pytest


def test_project_declares_locked_m1_data_and_model_dependencies() -> None:
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {
        dependency.split("=", 1)[0].split(">", 1)[0].split("<", 1)[0].casefold()
        for dependency in project["project"]["dependencies"]
    }

    assert {
        "duckdb",
        "lightgbm",
        "numpy",
        "pyarrow",
        "scikit-learn",
        "threadpoolctl",
    } <= dependencies
    assert project["tool"]["setuptools"]["data-files"][
        "share/stock_prediction/sql/m1"
    ] == ["backend/sql/m1/toolchain_smoke.sql"]


def test_load_sql_asset_reads_only_the_versioned_sql_root() -> None:
    from stock_prediction.sql_assets import load_sql_asset

    sql = load_sql_asset("m1/toolchain_smoke.sql")

    assert "stock_prediction.m1" in sql
    assert "input_arrow" in sql
    assert "source_available_at" in sql


@pytest.mark.parametrize(
    "asset_name",
    ["../outside.sql", "m1/../../outside.sql"],
)
def test_load_sql_asset_rejects_path_traversal(asset_name: str) -> None:
    from stock_prediction.sql_assets import load_sql_asset

    with pytest.raises(ValueError, match="relative path"):
        load_sql_asset(asset_name)


def test_load_sql_asset_rejects_absolute_paths(tmp_path: Path) -> None:
    from stock_prediction.sql_assets import load_sql_asset

    with pytest.raises(ValueError, match="relative path"):
        load_sql_asset(str((tmp_path / "outside.sql").resolve()))


def test_load_sql_asset_rejects_missing_assets() -> None:
    from stock_prediction.sql_assets import load_sql_asset

    with pytest.raises(FileNotFoundError, match="SQL asset not found"):
        load_sql_asset("m1/missing.sql")


def test_source_root_is_disabled_outside_an_exact_repository_layout(
    tmp_path: Path,
) -> None:
    from stock_prediction.sql_assets import detect_source_sql_root

    installed_module = tmp_path / "Lib" / "site-packages" / "stock_prediction"
    installed_module.mkdir(parents=True)
    module_file = installed_module / "sql_assets.py"
    module_file.touch()
    shadow_root = tmp_path / "Lib" / "sql" / "m1"
    shadow_root.mkdir(parents=True)
    (shadow_root / "toolchain_smoke.sql").write_text(
        "SELECT 'shadowed';", encoding="utf-8"
    )

    assert detect_source_sql_root(module_file) is None


def test_source_root_requires_matching_backend_package_layout(tmp_path: Path) -> None:
    from stock_prediction.sql_assets import detect_source_sql_root

    module_file = (
        tmp_path / "backend" / "src" / "stock_prediction" / "sql_assets.py"
    )
    module_file.parent.mkdir(parents=True)
    module_file.touch()
    (tmp_path / "backend" / "sql").mkdir(parents=True)
    (tmp_path / "pyproject.toml").write_text("[project]\n", encoding="utf-8")

    assert detect_source_sql_root(module_file) == (
        tmp_path / "backend" / "sql"
    ).resolve()
