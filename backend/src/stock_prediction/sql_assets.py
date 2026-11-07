"""Safe loading for versioned DuckDB SQL assets."""

from pathlib import Path
import sysconfig


INVALID_ASSET_MESSAGE = "SQL asset name must be a relative path without traversal"


def detect_source_sql_root(module_file: Path) -> Path | None:
    """Return the SQL root only for the exact checked-out repository layout."""

    resolved_module = module_file.resolve()
    try:
        repository_root = resolved_module.parents[3]
    except IndexError:
        return None
    expected_module = (
        repository_root
        / "backend"
        / "src"
        / "stock_prediction"
        / "sql_assets.py"
    ).resolve()
    sql_root = (repository_root / "backend" / "sql").resolve()
    if (
        resolved_module != expected_module
        or not (repository_root / "pyproject.toml").is_file()
        or not sql_root.is_dir()
    ):
        return None
    return sql_root


INSTALLED_SQL_ROOT = (
    Path(sysconfig.get_path("data")) / "share" / "stock_prediction" / "sql"
)
SOURCE_SQL_ROOT = detect_source_sql_root(Path(__file__))
SQL_ASSET_ROOTS = (
    (SOURCE_SQL_ROOT,) if SOURCE_SQL_ROOT is not None else (INSTALLED_SQL_ROOT,)
)


def load_sql_asset(asset_name: str) -> str:
    """Read one SQL asset confined to a configured versioned SQL root."""

    relative_path = Path(asset_name)
    if (
        not asset_name
        or relative_path.is_absolute()
        or relative_path.drive
        or ".." in relative_path.parts
    ):
        raise ValueError(INVALID_ASSET_MESSAGE)

    for root in SQL_ASSET_ROOTS:
        resolved_root = root.resolve()
        candidate = (root / relative_path).resolve()
        if not candidate.is_relative_to(resolved_root):
            raise ValueError(INVALID_ASSET_MESSAGE)
        if candidate.is_file():
            return candidate.read_text(encoding="utf-8")

    raise FileNotFoundError(f"SQL asset not found: {asset_name}")
